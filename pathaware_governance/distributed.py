from __future__ import annotations

import argparse
import csv
import heapq
import math
import queue
import statistics
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from .consistency import build_schedule
from .engines import PathAwareGovernanceEngine
from .generator import DEFAULT_SEED, generate_workflows
from .serde import decode_action_with_metadata, encode_action, encode_decision
from .streaming import (
    ACTION_TOPIC,
    DECISION_TOPIC,
    DEFAULT_BOOTSTRAP_SERVERS,
    STATE_TOPIC,
    partition_key,
)
from .workflows import LabeledWorkflow


@dataclass(frozen=True, slots=True)
class DistributedResult:
    consistency: str
    partition_by: str
    concurrency: int
    workers: int
    trial: int
    workflow_count: int
    event_count: int
    propagation_delay_ms: float
    stale_reads: int
    stale_read_rate: float
    mean_version_lag: float
    max_version_lag: int
    true_positives: int
    false_positives: int
    false_negatives: int
    true_negatives: int
    precision: float
    recall: float
    false_positive_rate: float
    throughput_events_per_second: float
    end_to_end_p50_ms: float
    end_to_end_p95_ms: float
    end_to_end_p99_ms: float


class _Coordinator:
    def __init__(self, expected_events: int) -> None:
        self.expected_events = expected_events
        self.processed = 0
        self.blocked: set[str] = set()
        self.version_lags: list[int] = []
        self.end_to_end_ms: list[float] = []
        self.lock = threading.Lock()
        self.done = threading.Event()
        self.errors: queue.Queue[BaseException] = queue.Queue()

    def record(
        self,
        *,
        workflow_id: str,
        blocked: bool,
        version_lag: int,
        end_to_end_ms: float,
    ) -> None:
        with self.lock:
            self.processed += 1
            if blocked:
                self.blocked.add(workflow_id)
            self.version_lags.append(version_lag)
            self.end_to_end_ms.append(end_to_end_ms)
            if self.processed >= self.expected_events:
                self.done.set()

    def fail(self, error: BaseException) -> None:
        self.errors.put(error)
        self.done.set()


class _DelayedStatePublisher:
    def __init__(self, bootstrap_servers: str, delay_ms: float) -> None:
        from confluent_kafka import Producer

        self.delay_seconds = delay_ms / 1_000
        self._producer = Producer(
            {"bootstrap.servers": bootstrap_servers, "enable.idempotence": True, "acks": "all"}
        )
        self._condition = threading.Condition()
        self._pending: list[tuple[float, int, str, bytes]] = []
        self._counter = 0
        self._closing = False
        self._thread = threading.Thread(target=self._run, name="state-publisher", daemon=True)
        self._thread.start()

    def schedule(self, key: str, value: bytes) -> None:
        with self._condition:
            self._counter += 1
            heapq.heappush(
                self._pending,
                (time.monotonic() + self.delay_seconds, self._counter, key, value),
            )
            self._condition.notify()

    def close(self) -> None:
        with self._condition:
            self._closing = True
            self._condition.notify_all()
        self._thread.join(timeout=30)
        if self._thread.is_alive():
            raise TimeoutError("state publisher did not stop")

    def _run(self) -> None:
        while True:
            with self._condition:
                while not self._pending and not self._closing:
                    self._condition.wait()
                if not self._pending and self._closing:
                    break
                due, _, key, value = self._pending[0]
                remaining = due - time.monotonic()
                if remaining > 0:
                    self._condition.wait(timeout=remaining)
                    continue
                heapq.heappop(self._pending)
            self._producer.produce(STATE_TOPIC, key=key.encode(), value=value)
            self._producer.poll(0)
        remaining = self._producer.flush(30)
        if remaining:
            raise RuntimeError(f"{remaining} state updates were not delivered")


class _Worker(threading.Thread):
    def __init__(
        self,
        *,
        worker_id: int,
        bootstrap_servers: str,
        run_id: str,
        consistency: str,
        coordinator: _Coordinator,
        state_publisher: _DelayedStatePublisher | None,
    ) -> None:
        super().__init__(name=f"governance-worker-{worker_id}", daemon=True)
        self.worker_id = worker_id
        self.bootstrap_servers = bootstrap_servers
        self.run_id = run_id
        self.consistency = consistency
        self.coordinator = coordinator
        self.state_publisher = state_publisher
        self.ready = threading.Event()

    def run(self) -> None:
        try:
            self._run()
        except BaseException as error:
            self.coordinator.fail(error)

    def _run(self) -> None:
        from confluent_kafka import Consumer, Producer

        action_assigned = threading.Event()
        state_assigned = threading.Event()

        def action_assignment(_, partitions) -> None:
            if partitions:
                action_assigned.set()

        def state_assignment(_, partitions) -> None:
            if partitions:
                state_assigned.set()

        action_consumer = Consumer(
            {
                "bootstrap.servers": self.bootstrap_servers,
                "group.id": f"distributed-actions-{self.run_id}",
                "auto.offset.reset": "latest",
                "enable.auto.commit": False,
            }
        )
        state_consumer = None
        if self.consistency == "weak":
            state_consumer = Consumer(
                {
                    "bootstrap.servers": self.bootstrap_servers,
                    "group.id": f"distributed-state-{self.run_id}-{self.worker_id}",
                    "auto.offset.reset": "latest",
                    "enable.auto.commit": True,
                }
            )
        decision_producer = Producer(
            {"bootstrap.servers": self.bootstrap_servers, "enable.idempotence": True, "acks": "all"}
        )
        engine = PathAwareGovernanceEngine()
        action_consumer.subscribe([ACTION_TOPIC], on_assign=action_assignment)
        if state_consumer is not None:
            state_consumer.subscribe([STATE_TOPIC], on_assign=state_assignment)
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                action_consumer.poll(0.05)
                if state_consumer is not None:
                    state_consumer.poll(0.05)
                if action_assigned.is_set() and (
                    state_consumer is None or state_assigned.is_set()
                ):
                    self.ready.set()
                    break
            if not self.ready.is_set():
                raise TimeoutError(f"worker {self.worker_id} did not receive Kafka assignments")

            while not self.coordinator.done.is_set():
                if state_consumer is not None:
                    self._drain_state(state_consumer, engine)
                message = action_consumer.poll(0.05)
                if message is None:
                    continue
                if message.error():
                    raise RuntimeError(str(message.error()))
                message_run_id, event, metadata = decode_action_with_metadata(message.value())
                if message_run_id != self.run_id:
                    continue
                visible_prior = sum(
                    observed.sequence < event.sequence
                    for observed in engine.history(event.workflow_id)
                )
                version_lag = max(0, event.sequence - 1 - visible_prior)
                decision = engine.evaluate(event)
                now_ns = time.time_ns()
                published_at_ns = int(metadata.get("published_at_ns", now_ns))
                self.coordinator.record(
                    workflow_id=event.workflow_id,
                    blocked=decision.blocked,
                    version_lag=version_lag,
                    end_to_end_ms=(now_ns - published_at_ns) / 1_000_000,
                )
                decision_producer.produce(
                    DECISION_TOPIC,
                    key=event.workflow_id.encode(),
                    value=encode_decision(decision, run_id=self.run_id),
                )
                decision_producer.poll(0)
                if self.state_publisher is not None:
                    self.state_publisher.schedule(
                        event.workflow_id,
                        encode_action(
                            event,
                            run_id=self.run_id,
                            metadata={"origin_worker": self.worker_id},
                        ),
                    )
                action_consumer.commit(message=message, asynchronous=True)
        finally:
            decision_producer.flush(30)
            action_consumer.close()
            if state_consumer is not None:
                state_consumer.close()

    def _drain_state(self, consumer, engine: PathAwareGovernanceEngine) -> None:
        for _ in range(200):
            message = consumer.poll(0)
            if message is None:
                return
            if message.error():
                raise RuntimeError(str(message.error()))
            message_run_id, event, _ = decode_action_with_metadata(message.value())
            if message_run_id == self.run_id:
                engine.observe(event)


def _publish_schedule(
    *,
    bootstrap_servers: str,
    run_id: str,
    workflows: list[LabeledWorkflow],
    concurrency: int,
    partition_by: str,
    step_interval_ms: float,
) -> None:
    from confluent_kafka import Producer

    producer = Producer(
        {"bootstrap.servers": bootstrap_servers, "enable.idempotence": True, "acks": "all"}
    )
    schedule = build_schedule(
        workflows, concurrency=concurrency, step_interval_ms=step_interval_ms
    )
    started = time.monotonic()
    for item in schedule:
        target = started + item.logical_time_ms / 1_000
        remaining = target - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)
        producer.produce(
            ACTION_TOPIC,
            key=partition_key(item.event, partition_by).encode(),
            value=encode_action(
                item.event,
                run_id=run_id,
                metadata={"published_at_ns": time.time_ns()},
            ),
        )
        producer.poll(0)
    remaining = producer.flush(30)
    if remaining:
        raise RuntimeError(f"{remaining} scheduled actions were not delivered")


def run_distributed_trial(
    *,
    bootstrap_servers: str,
    workflows: list[LabeledWorkflow],
    consistency: str,
    concurrency: int,
    workers: int,
    trial: int,
    propagation_delay_ms: float,
    step_interval_ms: float,
) -> DistributedResult:
    if consistency not in {"strong", "weak"}:
        raise ValueError("consistency must be 'strong' or 'weak'")
    run_id = f"distributed-{consistency}-{uuid.uuid4()}"
    event_count = sum(len(workflow.events) for workflow in workflows)
    coordinator = _Coordinator(event_count)
    state_publisher = (
        _DelayedStatePublisher(bootstrap_servers, propagation_delay_ms)
        if consistency == "weak"
        else None
    )
    worker_threads = [
        _Worker(
            worker_id=index,
            bootstrap_servers=bootstrap_servers,
            run_id=run_id,
            consistency=consistency,
            coordinator=coordinator,
            state_publisher=state_publisher,
        )
        for index in range(workers)
    ]
    for worker in worker_threads:
        worker.start()
    if not all(worker.ready.wait(timeout=25) for worker in worker_threads):
        coordinator.done.set()
        raise TimeoutError("not all distributed workers became ready")
    # Allow the shared action consumer group to settle after its final rebalance.
    time.sleep(1)

    partition_by = "workflow" if consistency == "strong" else "agent"
    started = time.perf_counter()
    _publish_schedule(
        bootstrap_servers=bootstrap_servers,
        run_id=run_id,
        workflows=workflows,
        concurrency=concurrency,
        partition_by=partition_by,
        step_interval_ms=step_interval_ms,
    )
    if not coordinator.done.wait(timeout=60):
        coordinator.done.set()
        raise TimeoutError(
            f"distributed trial processed {coordinator.processed}/{event_count} events"
        )
    elapsed = time.perf_counter() - started
    if not coordinator.errors.empty():
        raise coordinator.errors.get()
    for worker in worker_threads:
        worker.join(timeout=20)
        if worker.is_alive():
            raise TimeoutError(f"worker {worker.worker_id} did not stop")
    if state_publisher is not None:
        state_publisher.close()

    expected = {workflow.workflow_id for workflow in workflows if workflow.should_violate}
    safe = {workflow.workflow_id for workflow in workflows if not workflow.should_violate}
    true_positives = len(coordinator.blocked & expected)
    false_positives = len(coordinator.blocked & safe)
    false_negatives = len(expected - coordinator.blocked)
    true_negatives = len(safe - coordinator.blocked)
    stale_reads = sum(lag > 0 for lag in coordinator.version_lags)
    precision = _ratio(true_positives, true_positives + false_positives)
    recall = _ratio(true_positives, true_positives + false_negatives)
    return DistributedResult(
        consistency=consistency,
        partition_by=partition_by,
        concurrency=concurrency,
        workers=workers,
        trial=trial,
        workflow_count=len(workflows),
        event_count=event_count,
        propagation_delay_ms=propagation_delay_ms if consistency == "weak" else 0,
        stale_reads=stale_reads,
        stale_read_rate=stale_reads / event_count,
        mean_version_lag=statistics.fmean(coordinator.version_lags),
        max_version_lag=max(coordinator.version_lags, default=0),
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=false_negatives,
        true_negatives=true_negatives,
        precision=precision,
        recall=recall,
        false_positive_rate=_ratio(false_positives, false_positives + true_negatives),
        throughput_events_per_second=event_count / elapsed,
        end_to_end_p50_ms=_percentile(coordinator.end_to_end_ms, 50),
        end_to_end_p95_ms=_percentile(coordinator.end_to_end_ms, 95),
        end_to_end_p99_ms=_percentile(coordinator.end_to_end_ms, 99),
    )


def _ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def _percentile(values: list[float], percentile: int) -> float:
    ordered = sorted(values)
    index = math.ceil((percentile / 100) * len(ordered)) - 1
    return ordered[max(0, index)]


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark real multi-worker Kafka state.")
    parser.add_argument("--bootstrap-servers", default=DEFAULT_BOOTSTRAP_SERVERS)
    parser.add_argument("--workflows", type=int, default=150)
    parser.add_argument("--concurrency", type=int, nargs="+", default=[10, 50, 100])
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--trials", type=int, default=2)
    parser.add_argument("--propagation-delay-ms", type=float, default=5)
    parser.add_argument("--step-interval-ms", type=float, default=10)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--output", type=Path, default=Path("benchmark-results/distributed-kafka.csv")
    )
    args = parser.parse_args()
    workflows = generate_workflows(size=args.workflows, seed=args.seed)
    results: list[DistributedResult] = []
    for consistency in ("strong", "weak"):
        for concurrency in args.concurrency:
            for trial in range(1, args.trials + 1):
                result = run_distributed_trial(
                    bootstrap_servers=args.bootstrap_servers,
                    workflows=workflows,
                    consistency=consistency,
                    concurrency=concurrency,
                    workers=args.workers,
                    trial=trial,
                    propagation_delay_ms=args.propagation_delay_ms,
                    step_interval_ms=args.step_interval_ms,
                )
                results.append(result)
                print(
                    f"{consistency:6} concurrency={concurrency:3d} trial={trial} "
                    f"stale={result.stale_read_rate:.3f} recall={result.recall:.3f} "
                    f"p95={result.end_to_end_p95_ms:.1f}ms"
                )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=asdict(results[0]).keys())
        writer.writeheader()
        writer.writerows(asdict(result) for result in results)


if __name__ == "__main__":
    main()

