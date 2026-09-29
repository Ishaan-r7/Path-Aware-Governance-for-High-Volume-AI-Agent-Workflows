from __future__ import annotations

import argparse
import csv
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from .streaming import (
    DEFAULT_BOOTSTRAP_SERVERS,
    KafkaProducerBroker,
    consume_and_govern,
    load_dataset_events,
    publish_actions,
)


@dataclass(frozen=True, slots=True)
class KafkaSmokeResult:
    mode: str
    event_count: int
    produce_seconds: float
    consume_and_decide_seconds: float
    end_to_end_seconds: float
    produce_events_per_second: float
    consume_and_decide_events_per_second: float
    end_to_end_events_per_second: float


def run_smoke(
    *,
    bootstrap_servers: str,
    dataset: Path,
    modes: tuple[str, ...],
) -> list[KafkaSmokeResult]:
    events = load_dataset_events(dataset)
    results: list[KafkaSmokeResult] = []
    for mode in modes:
        run_id = f"kafka-smoke-{mode}-{uuid.uuid4()}"
        total_started = time.perf_counter()
        produce_started = time.perf_counter()
        publish_actions(
            KafkaProducerBroker(bootstrap_servers),
            events,
            run_id=run_id,
            partition_by="workflow",
        )
        produce_seconds = time.perf_counter() - produce_started
        consume_started = time.perf_counter()
        processed = consume_and_govern(
            bootstrap_servers=bootstrap_servers,
            mode=mode,
            run_id=run_id,
            expected_events=len(events),
            idle_timeout_seconds=30,
        )
        consume_seconds = time.perf_counter() - consume_started
        total_seconds = time.perf_counter() - total_started
        results.append(
            KafkaSmokeResult(
                mode=mode,
                event_count=processed,
                produce_seconds=produce_seconds,
                consume_and_decide_seconds=consume_seconds,
                end_to_end_seconds=total_seconds,
                produce_events_per_second=processed / produce_seconds,
                consume_and_decide_events_per_second=processed / consume_seconds,
                end_to_end_events_per_second=processed / total_seconds,
            )
        )
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a local Kafka transport smoke benchmark.")
    parser.add_argument("--bootstrap-servers", default=DEFAULT_BOOTSTRAP_SERVERS)
    parser.add_argument("--dataset", type=Path, default=Path("data/generated/workflows.jsonl"))
    parser.add_argument(
        "--modes",
        nargs="+",
        choices=("none", "stateless", "path-aware"),
        default=["none", "stateless", "path-aware"],
    )
    parser.add_argument(
        "--output", type=Path, default=Path("benchmark-results/kafka-smoke.csv")
    )
    args = parser.parse_args()
    results = run_smoke(
        bootstrap_servers=args.bootstrap_servers,
        dataset=args.dataset,
        modes=tuple(args.modes),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=asdict(results[0]).keys())
        writer.writeheader()
        writer.writerows(asdict(result) for result in results)
    for result in results:
        print(
            f"{result.mode:10} events={result.event_count} "
            f"consume+decide={result.consume_and_decide_events_per_second:.1f} events/s"
        )


if __name__ == "__main__":
    main()

