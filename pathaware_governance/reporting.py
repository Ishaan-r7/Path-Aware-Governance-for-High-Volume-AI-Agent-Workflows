from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring

MODES = ("none", "stateless", "path-aware")
COLORS = {"none": "#64748b", "stateless": "#f59e0b", "path-aware": "#2563eb"}


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _svg_root(title: str, description: str) -> Element:
    root = Element(
        "svg",
        {
            "xmlns": "http://www.w3.org/2000/svg",
            "viewBox": "0 0 960 540",
            "role": "img",
            "aria-labelledby": "chart-title chart-desc",
        },
    )
    SubElement(root, "title", {"id": "chart-title"}).text = title
    SubElement(root, "desc", {"id": "chart-desc"}).text = description
    SubElement(root, "rect", {"width": "960", "height": "540", "fill": "#ffffff"})
    SubElement(
        root,
        "text",
        {"x": "70", "y": "42", "font-size": "22", "font-family": "sans-serif", "fill": "#0f172a"},
    ).text = title
    return root


def _line_chart(
    rows: list[dict[str, str]],
    output: Path,
    *,
    metric: str,
    title: str,
    y_label: str,
    value_format,
) -> None:
    root = _svg_root(title, f"Line chart comparing {y_label} by governance mode and concurrency.")
    left, top, right, bottom = 90, 75, 925, 455
    values = [float(row[metric]) for row in rows]
    x_values = sorted({int(row["concurrency"]) for row in rows})
    y_max = max(values) * 1.12

    def x_scale(value: int) -> float:
        return left + (value - min(x_values)) / (max(x_values) - min(x_values)) * (right - left)

    def y_scale(value: float) -> float:
        return bottom - value / y_max * (bottom - top)

    for tick in range(6):
        value = y_max * tick / 5
        y = y_scale(value)
        SubElement(root, "line", {"x1": str(left), "x2": str(right), "y1": str(y), "y2": str(y), "stroke": "#e2e8f0"})
        SubElement(root, "text", {"x": str(left - 10), "y": str(y + 4), "text-anchor": "end", "font-size": "12", "font-family": "sans-serif", "fill": "#475569"}).text = value_format(value)
    for value in x_values:
        x = x_scale(value)
        SubElement(root, "line", {"x1": str(x), "x2": str(x), "y1": str(top), "y2": str(bottom), "stroke": "#f1f5f9"})
        SubElement(root, "text", {"x": str(x), "y": str(bottom + 24), "text-anchor": "middle", "font-size": "12", "font-family": "sans-serif", "fill": "#475569"}).text = str(value)
    SubElement(root, "line", {"x1": str(left), "x2": str(left), "y1": str(top), "y2": str(bottom), "stroke": "#64748b"})
    SubElement(root, "line", {"x1": str(left), "x2": str(right), "y1": str(bottom), "y2": str(bottom), "stroke": "#64748b"})
    SubElement(root, "text", {"x": str((left + right) / 2), "y": "510", "text-anchor": "middle", "font-size": "14", "font-family": "sans-serif", "fill": "#0f172a"}).text = "Concurrent workflows"
    y_title = SubElement(root, "text", {"x": "24", "y": str((top + bottom) / 2), "text-anchor": "middle", "font-size": "14", "font-family": "sans-serif", "fill": "#0f172a", "transform": f"rotate(-90 24 {(top + bottom) / 2})"})
    y_title.text = y_label

    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row["mode"]].append(row)
    for legend_index, mode in enumerate(MODES):
        points = sorted(grouped[mode], key=lambda row: int(row["concurrency"]))
        coordinates = [(x_scale(int(row["concurrency"])), y_scale(float(row[metric]))) for row in points]
        SubElement(root, "polyline", {"points": " ".join(f"{x:.1f},{y:.1f}" for x, y in coordinates), "fill": "none", "stroke": COLORS[mode], "stroke-width": "3"})
        for (x, y), row in zip(coordinates, points):
            SubElement(root, "circle", {"cx": f"{x:.1f}", "cy": f"{y:.1f}", "r": "5", "fill": COLORS[mode]})
        legend_x = 610 + legend_index * 105
        SubElement(root, "line", {"x1": str(legend_x), "x2": str(legend_x + 22), "y1": "42", "y2": "42", "stroke": COLORS[mode], "stroke-width": "3"})
        SubElement(root, "text", {"x": str(legend_x + 28), "y": "46", "font-size": "12", "font-family": "sans-serif", "fill": "#334155"}).text = mode
    output.write_bytes(tostring(root, encoding="utf-8", xml_declaration=True))


def _recall_chart(rows: list[dict[str, str]], output: Path) -> None:
    root = _svg_root("Policy-violation recall", "Bar chart comparing recall for three governance modes.")
    representative = {row["mode"]: float(row["recall"]) for row in rows if row["concurrency"] == "50"}
    left, bottom, height = 165, 440, 330
    bar_width, gap = 150, 70
    for tick in range(6):
        value = tick / 5
        y = bottom - value * height
        SubElement(root, "line", {"x1": "90", "x2": "890", "y1": str(y), "y2": str(y), "stroke": "#e2e8f0"})
        SubElement(root, "text", {"x": "78", "y": str(y + 4), "text-anchor": "end", "font-size": "12", "font-family": "sans-serif", "fill": "#475569"}).text = f"{value:.0%}"
    for index, mode in enumerate(MODES):
        value = representative[mode]
        x = left + index * (bar_width + gap)
        y = bottom - value * height
        SubElement(root, "rect", {"x": str(x), "y": str(y), "width": str(bar_width), "height": str(value * height), "fill": COLORS[mode]})
        SubElement(root, "text", {"x": str(x + bar_width / 2), "y": str(y - 12), "text-anchor": "middle", "font-size": "16", "font-family": "sans-serif", "fill": "#0f172a"}).text = f"{value:.1%}"
        SubElement(root, "text", {"x": str(x + bar_width / 2), "y": str(bottom + 28), "text-anchor": "middle", "font-size": "13", "font-family": "sans-serif", "fill": "#334155"}).text = mode
    SubElement(root, "text", {"x": "28", "y": "275", "text-anchor": "middle", "font-size": "14", "font-family": "sans-serif", "fill": "#0f172a", "transform": "rotate(-90 28 275)"}).text = "Recall"
    output.write_bytes(tostring(root, encoding="utf-8", xml_declaration=True))


def generate_report(results_dir: Path) -> Path:
    summary = _read_csv(results_dir / "load-summary.csv")
    staleness = _read_csv(results_dir / "staleness-preliminary.csv")
    kafka_smoke_path = results_dir / "kafka-smoke.csv"
    kafka_smoke = _read_csv(kafka_smoke_path) if kafka_smoke_path.exists() else []
    charts = results_dir / "charts"
    charts.mkdir(exist_ok=True)
    _line_chart(summary, charts / "throughput.svg", metric="throughput_median_eps", title="Governance throughput under load", y_label="Throughput (events/second)", value_format=lambda value: f"{value / 1000:.0f}k")
    _line_chart(summary, charts / "p95-latency.svg", metric="latency_p95_median_us", title="Governance decision latency", y_label="Median trial p95 latency (µs)", value_format=lambda value: f"{value:.1f}")
    _recall_chart(summary, charts / "recall.svg")

    lookup = {(row["mode"], int(row["concurrency"])): row for row in summary}
    none_10 = float(lookup[("none", 10)]["throughput_median_eps"])
    path_10 = float(lookup[("path-aware", 10)]["throughput_median_eps"])
    none_100 = float(lookup[("none", 100)]["throughput_median_eps"])
    path_100 = float(lookup[("path-aware", 100)]["throughput_median_eps"])
    weak_100 = next(row for row in staleness if row["consistency"] == "weak" and row["concurrency"] == "100")
    lines = [
        "# Benchmark Report",
        "",
        "## Executive result",
        "",
        "Path-aware governance detected every authored violation with no false positives in the strongly consistent in-process benchmark. Stateless governance detected only the single-action cases, yielding 23.3% recall, while no governance detected none.",
        "",
        f"Path-aware throughput was {path_10:,.0f} events/s at concurrency 10 and {path_100:,.0f} events/s at concurrency 100. Relative to no governance, that is a {100 * (1 - path_10 / none_10):.1f}% throughput reduction at concurrency 10 and {100 * (1 - path_100 / none_100):.1f}% reduction at concurrency 100.",
        "",
        "![Throughput](charts/throughput.svg)",
        "",
        "![Latency](charts/p95-latency.svg)",
        "",
        "![Recall](charts/recall.svg)",
        "",
        "## Accuracy",
        "",
        "| Mode | Precision | Recall | F1 | False-positive rate |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for mode in MODES:
        row = lookup[(mode, 50)]
        lines.append(f"| {mode} | {float(row['precision']):.1%} | {float(row['recall']):.1%} | {float(row['f1']):.1%} | {float(row['false_positive_rate']):.1%} |")
    lines.extend(
        [
            "",
            "Precision and F1 are shown as zero for no governance because it emits no positive detections; precision is mathematically undefined in that case.",
            "",
            "## Consistency sensitivity",
            "",
            f"The separate controlled state-replication experiment measured a {float(weak_100['stale_read_rate']):.1%} stale-read rate and {float(weak_100['recall']):.1%} recall at 100 concurrent workflows under the fixed weak-replica parameters. Strong state retained 100% recall. These are simulator results and must not be presented as measured Kafka propagation behavior.",
            "",
            "## Method",
            "",
            "- 3,000 seeded workflows and 7,983 action events per trial.",
            "- Five trials for every mode/concurrency pair.",
            "- Concurrent workflow levels: 10, 25, 50, and 100.",
            "- Identical workflow corpus for every configuration.",
            "- Latency measures only the in-process governance decision; Kafka transport is excluded.",
            "- Throughput includes thread scheduling and all governance calls.",
            "- CPU percentage uses process CPU time divided by wall time; 100% represents one fully utilized core.",
            "- Maximum RSS is a process high-water mark and is not isolated per trial.",
            "",
            "## Interpretation and limitations",
            "",
            "The results support the core safety hypothesis: history-aware policies catch violations that independent action checks cannot. The measured decision cost remains in microseconds in this prototype, but the Python thread benchmark is CPU/GIL constrained and does not predict distributed production capacity. Kafka transport, persistence, network placement, replication factor, and multi-host state coordination require a separate system benchmark before making deployment claims.",
            "",
            "The synthetic labels are generator-authored. The planned two-reviewer blind labeling exercise must still be completed before treating the corpus as frozen ground truth.",
            "",
        ]
    )
    if kafka_smoke:
        lines.extend(
            [
                "## Local Kafka transport check",
                "",
                "| Mode | Events | Produce rate | Consume, decide, publish and commit rate | End-to-end rate |",
                "| --- | ---: | ---: | ---: | ---: |",
            ]
        )
        for row in kafka_smoke:
            lines.append(
                f"| {row['mode']} | {int(row['event_count']):,} | "
                f"{float(row['produce_events_per_second']):,.1f} events/s | "
                f"{float(row['consume_and_decide_events_per_second']):,.1f} events/s | "
                f"{float(row['end_to_end_events_per_second']):,.1f} events/s |"
            )
        lines.extend(
            [
                "",
                "This smoke benchmark used the single-node, replication-factor-one local Kafka configuration and synchronous offset commits after every decision. It validates the complete transport path but is not a repeated or multi-host capacity result.",
                "",
            ]
        )
    report = results_dir / "benchmark-report.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate benchmark charts and report.")
    parser.add_argument("--results", type=Path, default=Path("benchmark-results"))
    args = parser.parse_args()
    print(generate_report(args.results))


if __name__ == "__main__":
    main()
