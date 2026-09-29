from __future__ import annotations

import argparse
import json
import time
import uuid
import zlib
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from .engines import NoGovernanceEngine, PathAwareGovernanceEngine, StatelessGovernanceEngine
from .models import ActionEvent, Decision
from .serde import decode_action, encode_action, encode_decision

ACTION_TOPIC = "agent-actions"
DECISION_TOPIC = "governance-decisions"
STATE_TOPIC = "governance-state-updates"
DEFAULT_BOOTSTRAP_SERVERS = "localhost:9092"


@dataclass(frozen=True, slots=True)
class StreamRecord:
    topic: str
    key: str
    value: bytes
    partition: int
    offset: int


class Broker(Protocol):
    def publish(self, topic: str, key: str, value: bytes) -> StreamRecord | None: ...


class InMemoryBroker:
    """Partitioned broker used for deterministic integration tests."""

    def __init__(self, partitions: int = 12) -> None:
        if partitions < 1:
            raise ValueError("partitions must be positive")
        self.partitions = partitions
        self._records: dict[str, dict[int, list[StreamRecord]]] = defaultdict(
            lambda: defaultdict(list)
        )

    def publish(self, topic: str, key: str, value: bytes) -> StreamRecord:
        partition = zlib.crc32(key.encode("utf-8")) % self.partitions
        records = self._records[topic][partition]
        record = StreamRecord(topic, key, value, partition, len(records))
        records.append(record)
        return record

    def records(self, topic: str) -> tuple[StreamRecord, ...]:
        return tuple(
            record
            for partition in sorted(self._records[topic])
            for record in self._records[topic][partition]
        )

    def records_for_partition(self, topic: str, partition: int) -> tuple[StreamRecord, ...]:
        return tuple(self._records[topic][partition])


class KafkaProducerBroker:
    """Thin synchronous publishing adapter around confluent-kafka."""

    def __init__(self, bootstrap_servers: str = DEFAULT_BOOTSTRAP_SERVERS) -> None:
        try:
            from confluent_kafka import Producer
        except ImportError as error:
            raise RuntimeError(
                "Kafka support requires: python3 -m pip install -e '.[kafka]'"
            ) from error
        self._producer = Producer(
            {
                "bootstrap.servers": bootstrap_servers,
                "enable.idempotence": True,
                "acks": "all",
            }
        )

    def publish(self, topic: str, key: str, value: bytes) -> None:
        self._producer.produce(topic, key=key.encode(), value=value)
        self._producer.poll(0)
        return None

    def flush(self) -> None:
        remaining = self._producer.flush(30)
        if remaining:
            raise RuntimeError(f"{remaining} Kafka messages were not delivered")


def partition_key(event: ActionEvent, strategy: str) -> str:
    if strategy == "workflow":
        return event.workflow_id
    if strategy == "agent":
        return event.agent_id
    raise ValueError("partition strategy must be 'workflow' or 'agent'")


def publish_actions(
    broker: Broker,
    events: Iterable[ActionEvent],
    *,
    run_id: str,
    partition_by: str,
) -> int:
    count = 0
    for event in events:
        broker.publish(
            ACTION_TOPIC,
            partition_key(event, partition_by),
            encode_action(event, run_id=run_id),
        )
        count += 1
    flush = getattr(broker, "flush", None)
    if flush:
        flush()
    return count


def process_records(
    records: Iterable[StreamRecord],
    broker: Broker,
    *,
    mode: str,
    run_id: str,
) -> list[Decision]:
    engine = _new_engine(mode)
    decisions: list[Decision] = []
    for record in records:
        record_run_id, event = decode_action(record.value)
        if record_run_id != run_id:
            continue
        decision = engine.evaluate(event)
        broker.publish(
            DECISION_TOPIC,
            event.workflow_id,
            encode_decision(decision, run_id=run_id),
        )
        decisions.append(decision)
    return decisions


def _new_engine(mode: str):
    engines = {
        "none": NoGovernanceEngine,
        "stateless": StatelessGovernanceEngine,
        "path-aware": PathAwareGovernanceEngine,
    }
    try:
        return engines[mode]()
    except KeyError as error:
        raise ValueError(f"unknown governance mode: {mode}") from error


def load_dataset_events(path: Path) -> list[ActionEvent]:
    events: list[ActionEvent] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            for raw in record["events"]:
                raw["occurred_at"] = datetime.fromisoformat(raw["occurred_at"])
                events.append(ActionEvent(**raw))
    events.sort(key=lambda event: (event.occurred_at, event.workflow_id, event.sequence))
    return events


def setup_topics(bootstrap_servers: str, partitions: int) -> None:
    try:
        from confluent_kafka.admin import AdminClient, NewTopic
    except ImportError as error:
        raise RuntimeError(
            "Kafka support requires: python3 -m pip install -e '.[kafka]'"
        ) from error
    admin = AdminClient({"bootstrap.servers": bootstrap_servers})
    futures = admin.create_topics(
        [
            NewTopic(ACTION_TOPIC, num_partitions=partitions, replication_factor=1),
            NewTopic(DECISION_TOPIC, num_partitions=partitions, replication_factor=1),
            NewTopic(STATE_TOPIC, num_partitions=partitions, replication_factor=1),
        ]
    )
    for topic, future in futures.items():
        try:
            future.result()
            print(f"created {topic}")
        except Exception as error:
            if "TOPIC_ALREADY_EXISTS" not in str(error):
                raise
            print(f"exists  {topic}")


def consume_and_govern(
    *,
    bootstrap_servers: str,
    mode: str,
    run_id: str,
    expected_events: int,
    idle_timeout_seconds: float,
) -> int:
    try:
        from confluent_kafka import Consumer, Producer
    except ImportError as error:
        raise RuntimeError(
            "Kafka support requires: python3 -m pip install -e '.[kafka]'"
        ) from error

    consumer = Consumer(
        {
            "bootstrap.servers": bootstrap_servers,
            "group.id": f"governance-{mode}-{run_id}",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    producer = Producer(
        {"bootstrap.servers": bootstrap_servers, "enable.idempotence": True, "acks": "all"}
    )
    engine = _new_engine(mode)
    processed = 0
    deadline = time.monotonic() + idle_timeout_seconds
    consumer.subscribe([ACTION_TOPIC])
    try:
        while processed < expected_events and time.monotonic() < deadline:
            message = consumer.poll(0.25)
            if message is None:
                continue
            if message.error():
                raise RuntimeError(str(message.error()))
            message_run_id, event = decode_action(message.value())
            if message_run_id != run_id:
                continue
            decision = engine.evaluate(event)
            producer.produce(
                DECISION_TOPIC,
                key=event.workflow_id.encode(),
                value=encode_decision(decision, run_id=run_id),
            )
            producer.poll(0)
            consumer.commit(message=message, asynchronous=False)
            processed += 1
            deadline = time.monotonic() + idle_timeout_seconds
    finally:
        producer.flush(30)
        consumer.close()
    if processed != expected_events:
        raise TimeoutError(f"processed {processed}/{expected_events} events before idle timeout")
    return processed


def main() -> None:
    parser = argparse.ArgumentParser(description="Kafka pipeline utilities.")
    parser.add_argument("--bootstrap-servers", default=DEFAULT_BOOTSTRAP_SERVERS)
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup = subparsers.add_parser("setup", help="Create Kafka topics.")
    setup.add_argument("--partitions", type=int, default=12)

    replay = subparsers.add_parser("replay", help="Publish a generated dataset.")
    replay.add_argument("--dataset", type=Path, default=Path("data/generated/workflows.jsonl"))
    replay.add_argument("--partition-by", choices=("agent", "workflow"), default="workflow")
    replay.add_argument("--run-id", default=None)

    govern = subparsers.add_parser("govern", help="Consume actions and publish decisions.")
    govern.add_argument("--mode", choices=("none", "stateless", "path-aware"), required=True)
    govern.add_argument("--run-id", required=True)
    govern.add_argument("--expected-events", type=int, required=True)
    govern.add_argument("--idle-timeout", type=float, default=10.0)

    args = parser.parse_args()
    if args.command == "setup":
        setup_topics(args.bootstrap_servers, args.partitions)
    elif args.command == "replay":
        run_id = args.run_id or str(uuid.uuid4())
        events = load_dataset_events(args.dataset)
        count = publish_actions(
            KafkaProducerBroker(args.bootstrap_servers),
            events,
            run_id=run_id,
            partition_by=args.partition_by,
        )
        print(f"run_id={run_id} events={count} partition_by={args.partition_by}")
    else:
        count = consume_and_govern(
            bootstrap_servers=args.bootstrap_servers,
            mode=args.mode,
            run_id=args.run_id,
            expected_events=args.expected_events,
            idle_timeout_seconds=args.idle_timeout,
        )
        print(f"run_id={args.run_id} processed={count} mode={args.mode}")


if __name__ == "__main__":
    main()
