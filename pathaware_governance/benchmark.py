from __future__ import annotations

import argparse
import csv
import json
import math
import os
import platform
import random
import resource
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

from .engines import NoGovernanceEngine, PathAwareGovernanceEngine, StatelessGovernanceEngine
from .generator import DEFAULT_SEED, generate_workflows
from .workflows import LabeledWorkflow


@dataclass(frozen=True, slots=True)
class TrialResult:
    mode: str
    concurrency: int
    trial: int
    workflow_count: int
    event_count: int
    wall_time_seconds: float
    throughput_events_per_second: float
    cpu_time_seconds: float
    cpu_utilization_percent: float
    max_rss_mb: float
    latency_p50_us: float
    latency_p95_us: float
    latency_p99_us: float
    true_positives: int
    false_positives: int
    false_negatives: int
    true_negatives: int
    precision: float
    recall: float
    f1: float
    false_positive_rate: float


@dataclass(frozen=True, slots=True)
class BenchmarkSummary:
    mode: str
    concurrency: int
    trials: int
    workflow_count: int
    event_count: int
    throughput_median_eps: float
    throughput_stdev_eps: float
    latency_p50_median_us: float
    latency_p95_median_us: float
    latency_p99_median_us: float
    cpu_utilization_median_percent: float
    max_rss_median_mb: float
    precision: float
    recall: float
    f1: float
    false_positive_rate: float


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


def run_trial(
    workflows: list[LabeledWorkflow],
    *,
    mode: str,
    concurrency: int,
    trial: int,
    seed: int,
) -> TrialResult:
    ordered = workflows.copy()
    random.Random(seed + trial).shuffle(ordered)
    engine = _new_engine(mode)

    # Warm a separate engine so imports and interpreter specialization do not
    # disproportionately affect the first measured trial.
    warmup_engine = _new_engine(mode)
    for workflow in ordered[: min(30, len(ordered))]:
        for event in workflow.events:
            warmup_engine.evaluate(event)

    def evaluate_workflow(workflow: LabeledWorkflow) -> tuple[str, bool, list[float]]:
        blocked = False
        latencies: list[float] = []
        for event in workflow.events:
            started = time.perf_counter_ns()
            decision = engine.evaluate(event)
            latencies.append((time.perf_counter_ns() - started) / 1_000)
            blocked = blocked or decision.blocked
        return workflow.workflow_id, blocked, latencies

    cpu_started = time.process_time()
    wall_started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        outcomes = list(executor.map(evaluate_workflow, ordered))
    wall_time = time.perf_counter() - wall_started
    cpu_time = time.process_time() - cpu_started

    expected = {item.workflow_id for item in workflows if item.should_violate}
    safe = {item.workflow_id for item in workflows if not item.should_violate}
    blocked = {workflow_id for workflow_id, did_block, _ in outcomes if did_block}
    latencies = [latency for _, _, values in outcomes for latency in values]
    event_count = len(latencies)
    true_positives = len(blocked & expected)
    false_positives = len(blocked & safe)
    false_negatives = len(expected - blocked)
    true_negatives = len(safe - blocked)
    precision = _ratio(true_positives, true_positives + false_positives)
    recall = _ratio(true_positives, true_positives + false_negatives)

    return TrialResult(
        mode=mode,
        concurrency=concurrency,
        trial=trial,
        workflow_count=len(workflows),
        event_count=event_count,
        wall_time_seconds=wall_time,
        throughput_events_per_second=event_count / wall_time,
        cpu_time_seconds=cpu_time,
        cpu_utilization_percent=(cpu_time / wall_time) * 100,
        max_rss_mb=_max_rss_mb(),
        latency_p50_us=_percentile(latencies, 50),
        latency_p95_us=_percentile(latencies, 95),
        latency_p99_us=_percentile(latencies, 99),
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=false_negatives,
        true_negatives=true_negatives,
        precision=precision,
        recall=recall,
        f1=_ratio(2 * precision * recall, precision + recall),
        false_positive_rate=_ratio(false_positives, false_positives + true_negatives),
    )


def run_benchmark(
    *,
    workflow_count: int = 3_000,
    modes: tuple[str, ...] = ("none", "stateless", "path-aware"),
    concurrency_levels: tuple[int, ...] = (10, 25, 50, 100),
    trials: int = 5,
    seed: int = DEFAULT_SEED,
) -> tuple[list[TrialResult], list[BenchmarkSummary], dict]:
    workflows = generate_workflows(size=workflow_count, seed=seed)
    results: list[TrialResult] = []
    for mode in modes:
        for concurrency in concurrency_levels:
            for trial in range(1, trials + 1):
                results.append(
                    run_trial(
                        workflows,
                        mode=mode,
                        concurrency=concurrency,
                        trial=trial,
                        seed=seed,
                    )
                )
    summaries = summarize(results)
    metadata = {
        "seed": seed,
        "workflow_count": workflow_count,
        "event_count": sum(len(item.events) for item in workflows),
        "modes": list(modes),
        "concurrency_levels": list(concurrency_levels),
        "trials_per_configuration": trials,
        "python": sys.version,
        "platform": platform.platform(),
        "logical_cpu_count": os.cpu_count(),
        "measurement_notes": {
            "latency": "In-process governance evaluate() duration; excludes Kafka transport.",
            "throughput": "Actions completed divided by trial wall time.",
            "cpu": "Process CPU time divided by wall time; 100% is one fully used core.",
            "memory": "Process maximum resident set high-water mark, not per-trial allocation.",
        },
    }
    return results, summaries, metadata


def summarize(results: list[TrialResult]) -> list[BenchmarkSummary]:
    grouped: dict[tuple[str, int], list[TrialResult]] = {}
    for result in results:
        grouped.setdefault((result.mode, result.concurrency), []).append(result)
    summaries: list[BenchmarkSummary] = []
    for (mode, concurrency), group in sorted(grouped.items()):
        throughput = [item.throughput_events_per_second for item in group]
        summaries.append(
            BenchmarkSummary(
                mode=mode,
                concurrency=concurrency,
                trials=len(group),
                workflow_count=group[0].workflow_count,
                event_count=group[0].event_count,
                throughput_median_eps=statistics.median(throughput),
                throughput_stdev_eps=statistics.stdev(throughput) if len(group) > 1 else 0,
                latency_p50_median_us=statistics.median(
                    item.latency_p50_us for item in group
                ),
                latency_p95_median_us=statistics.median(
                    item.latency_p95_us for item in group
                ),
                latency_p99_median_us=statistics.median(
                    item.latency_p99_us for item in group
                ),
                cpu_utilization_median_percent=statistics.median(
                    item.cpu_utilization_percent for item in group
                ),
                max_rss_median_mb=statistics.median(item.max_rss_mb for item in group),
                precision=statistics.median(item.precision for item in group),
                recall=statistics.median(item.recall for item in group),
                f1=statistics.median(item.f1 for item in group),
                false_positive_rate=statistics.median(
                    item.false_positive_rate for item in group
                ),
            )
        )
    return summaries


def write_benchmark(
    trials: list[TrialResult],
    summaries: list[BenchmarkSummary],
    metadata: dict,
    output_dir: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / "load-trials.csv", [asdict(item) for item in trials])
    _write_csv(output_dir / "load-summary.csv", [asdict(item) for item in summaries])
    (output_dir / "load-metadata.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )


def _write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def _max_rss_mb() -> float:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes; Linux reports KiB.
    return value / (1024 * 1024) if sys.platform == "darwin" else value / 1024


def _ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def _percentile(values: list[float], percentile: int) -> float:
    ordered = sorted(values)
    index = math.ceil((percentile / 100) * len(ordered)) - 1
    return ordered[max(0, index)]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run governance load benchmarks.")
    parser.add_argument("--workflows", type=int, default=3_000)
    parser.add_argument(
        "--modes", nargs="+", choices=("none", "stateless", "path-aware"),
        default=["none", "stateless", "path-aware"]
    )
    parser.add_argument("--concurrency", type=int, nargs="+", default=[10, 25, 50, 100])
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--output", type=Path, default=Path("benchmark-results"))
    args = parser.parse_args()
    trials, summaries, metadata = run_benchmark(
        workflow_count=args.workflows,
        modes=tuple(args.modes),
        concurrency_levels=tuple(args.concurrency),
        trials=args.trials,
        seed=args.seed,
    )
    write_benchmark(trials, summaries, metadata, args.output)
    print("mode       concurrency throughput_eps p95_us recall fpr")
    for result in summaries:
        print(
            f"{result.mode:10} {result.concurrency:11d} "
            f"{result.throughput_median_eps:14.0f} "
            f"{result.latency_p95_median_us:7.2f} "
            f"{result.recall:6.3f} {result.false_positive_rate:5.3f}"
        )


if __name__ == "__main__":
    main()

