from __future__ import annotations

import argparse
import csv
import heapq
import json
import math
import statistics
import time
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path

from .engines import PathAwareGovernanceEngine
from .generator import DEFAULT_SEED, generate_workflows
from .models import ActionEvent
from .workflows import LabeledWorkflow


@dataclass(frozen=True, slots=True)
class ScheduledEvent:
    logical_time_ms: float
    workflow: LabeledWorkflow
    event: ActionEvent


@dataclass(frozen=True, slots=True)
class ConsistencyResult:
    consistency: str
    concurrency: int
    workers: int
    propagation_delay_ms: float
    propagation_service_ms: float
    workflow_count: int
    event_count: int
    stale_reads: int
    stale_read_rate: float
    mean_version_lag: float
    max_version_lag: int
    true_positives: int
    false_positives: int
    false_negatives: int
    true_negatives: int
    recall: float
    false_positive_rate: float
    decision_latency_p50_us: float
    decision_latency_p95_us: float
    decision_latency_p99_us: float


def build_schedule(
    workflows: list[LabeledWorkflow],
    *,
    concurrency: int,
    step_interval_ms: float = 10.0,
) -> list[ScheduledEvent]:
    if concurrency < 1:
        raise ValueError("concurrency must be positive")
    max_steps = max(len(workflow.events) for workflow in workflows)
    batch_span_ms = (max_steps + 6) * step_interval_ms
    scheduled: list[ScheduledEvent] = []
    for index, workflow in enumerate(workflows):
        batch = index // concurrency
        start_ms = batch * batch_span_ms
        for event in workflow.events:
            scheduled.append(
                ScheduledEvent(
                    start_ms + (event.sequence - 1) * step_interval_ms,
                    workflow,
                    event,
                )
            )
    scheduled.sort(
        key=lambda item: (
            item.logical_time_ms,
            item.event.sequence,
            item.workflow.workflow_id,
        )
    )
    return scheduled


def _worker_for(event: ActionEvent, workers: int) -> int:
    return zlib.crc32(event.agent_id.encode("utf-8")) % workers


def simulate_strong(
    workflows: list[LabeledWorkflow], *, concurrency: int, workers: int
) -> ConsistencyResult:
    engine = PathAwareGovernanceEngine()
    blocked: set[str] = set()
    latencies: list[float] = []
    schedule = build_schedule(workflows, concurrency=concurrency)
    for item in schedule:
        started = time.perf_counter_ns()
        decision = engine.evaluate(item.event)
        latencies.append((time.perf_counter_ns() - started) / 1_000)
        if decision.blocked:
            blocked.add(item.workflow.workflow_id)
    return _result(
        "strong",
        workflows,
        schedule,
        blocked,
        concurrency=concurrency,
        workers=workers,
        propagation_delay_ms=0,
        propagation_service_ms=0,
        version_lags=[0] * len(schedule),
        latencies=latencies,
    )


def simulate_weak(
    workflows: list[LabeledWorkflow],
    *,
    concurrency: int,
    workers: int,
    propagation_delay_ms: float = 2.0,
    propagation_service_ms: float = 0.25,
) -> ConsistencyResult:
    """Simulate asynchronous replicas with one serialized update queue per worker.

    A local action is visible immediately on its worker. Copies for other workers
    pay a base network delay and wait behind earlier replication writes. The
    queueing term makes propagation lag increase as concurrent writers increase.
    """

    if workers < 2:
        raise ValueError("weak consistency requires at least two workers")
    schedule = build_schedule(workflows, concurrency=concurrency)
    replicas = [PathAwareGovernanceEngine() for _ in range(workers)]
    seen: list[dict[str, set[str]]] = [dict() for _ in range(workers)]
    expected_prior: dict[str, set[str]] = {}
    pending: list[tuple[float, int, int, ActionEvent]] = []
    channel_available = [0.0] * workers
    counter = 0
    blocked: set[str] = set()
    version_lags: list[int] = []
    latencies: list[float] = []

    def deliver(until_ms: float) -> None:
        while pending and pending[0][0] <= until_ms:
            _, _, target, propagated = heapq.heappop(pending)
            replicas[target].observe(propagated)
            seen[target].setdefault(propagated.workflow_id, set()).add(propagated.event_id)

    for item in schedule:
        deliver(item.logical_time_ms)
        event = item.event
        workflow_id = event.workflow_id
        worker = _worker_for(event, workers)
        prior_ids = expected_prior.setdefault(workflow_id, set())
        visible_prior = seen[worker].setdefault(workflow_id, set())
        version_lags.append(len(prior_ids - visible_prior))

        started = time.perf_counter_ns()
        decision = replicas[worker].evaluate(event)
        latencies.append((time.perf_counter_ns() - started) / 1_000)
        if decision.blocked:
            blocked.add(workflow_id)
        visible_prior.add(event.event_id)
        prior_ids.add(event.event_id)

        for target in range(workers):
            if target == worker:
                continue
            counter += 1
            begins_at = max(
                item.logical_time_ms + propagation_delay_ms,
                channel_available[target],
            )
            delivered_at = begins_at + propagation_service_ms
            channel_available[target] = delivered_at
            heapq.heappush(pending, (delivered_at, counter, target, event))

    return _result(
        "weak",
        workflows,
        schedule,
        blocked,
        concurrency=concurrency,
        workers=workers,
        propagation_delay_ms=propagation_delay_ms,
        propagation_service_ms=propagation_service_ms,
        version_lags=version_lags,
        latencies=latencies,
    )


def _result(
    consistency: str,
    workflows: list[LabeledWorkflow],
    schedule: list[ScheduledEvent],
    blocked: set[str],
    *,
    concurrency: int,
    workers: int,
    propagation_delay_ms: float,
    propagation_service_ms: float,
    version_lags: list[int],
    latencies: list[float],
) -> ConsistencyResult:
    expected = {workflow.workflow_id for workflow in workflows if workflow.should_violate}
    safe = {workflow.workflow_id for workflow in workflows if not workflow.should_violate}
    true_positives = len(blocked & expected)
    false_positives = len(blocked & safe)
    false_negatives = len(expected - blocked)
    true_negatives = len(safe - blocked)
    stale_reads = sum(lag > 0 for lag in version_lags)
    return ConsistencyResult(
        consistency=consistency,
        concurrency=concurrency,
        workers=workers,
        propagation_delay_ms=propagation_delay_ms,
        propagation_service_ms=propagation_service_ms,
        workflow_count=len(workflows),
        event_count=len(schedule),
        stale_reads=stale_reads,
        stale_read_rate=stale_reads / len(schedule),
        mean_version_lag=statistics.fmean(version_lags),
        max_version_lag=max(version_lags, default=0),
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=false_negatives,
        true_negatives=true_negatives,
        recall=_ratio(true_positives, true_positives + false_negatives),
        false_positive_rate=_ratio(false_positives, false_positives + true_negatives),
        decision_latency_p50_us=_percentile(latencies, 50),
        decision_latency_p95_us=_percentile(latencies, 95),
        decision_latency_p99_us=_percentile(latencies, 99),
    )


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _percentile(values: list[float], percentile: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = math.ceil((percentile / 100) * len(ordered)) - 1
    return ordered[max(0, index)]


def run_sweep(
    *,
    concurrency_levels: tuple[int, ...] = (10, 25, 50, 100),
    workers: int = 8,
    propagation_delay_ms: float = 2.0,
    propagation_service_ms: float = 0.25,
    seed: int = DEFAULT_SEED,
) -> list[ConsistencyResult]:
    workflows = generate_workflows(seed=seed)
    results: list[ConsistencyResult] = []
    for concurrency in concurrency_levels:
        results.append(
            simulate_strong(workflows, concurrency=concurrency, workers=workers)
        )
        results.append(
            simulate_weak(
                workflows,
                concurrency=concurrency,
                workers=workers,
                propagation_delay_ms=propagation_delay_ms,
                propagation_service_ms=propagation_service_ms,
            )
        )
    return results


def write_results(results: list[ConsistencyResult], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = [asdict(result) for result in results]
    with (output_dir / "staleness-preliminary.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    (output_dir / "staleness-preliminary.json").write_text(
        json.dumps(rows, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run shared-state consistency experiments.")
    parser.add_argument("--concurrency", type=int, nargs="+", default=[10, 25, 50, 100])
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--propagation-delay-ms", type=float, default=2.0)
    parser.add_argument("--propagation-service-ms", type=float, default=0.25)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--output", type=Path, default=Path("benchmark-results"))
    args = parser.parse_args()
    results = run_sweep(
        concurrency_levels=tuple(args.concurrency),
        workers=args.workers,
        propagation_delay_ms=args.propagation_delay_ms,
        propagation_service_ms=args.propagation_service_ms,
        seed=args.seed,
    )
    write_results(results, args.output)
    print("consistency concurrency stale_read_rate recall false_negatives")
    for result in results:
        print(
            f"{result.consistency:11} {result.concurrency:11d} "
            f"{result.stale_read_rate:15.3f} {result.recall:6.3f} "
            f"{result.false_negatives:15d}"
        )


if __name__ == "__main__":
    main()

