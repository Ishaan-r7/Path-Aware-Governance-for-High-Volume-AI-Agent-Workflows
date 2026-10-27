"""Run scripted scenarios through the gateway under each governance level and compare."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from typing import Any

from ..engines import (
    GovernanceEngine,
    NoGovernanceEngine,
    PathAwareGovernanceEngine,
    StatelessGovernanceEngine,
)
from ..models import ActionEvent, Decision
from .gateway import Gateway, GatewayResult
from .outcomes import outcomes
from .scenarios import SCENARIOS, Scenario, Step
from .world import World

_ENGINES = {
    "none": NoGovernanceEngine,
    "stateless": StatelessGovernanceEngine,
    "path-aware": PathAwareGovernanceEngine,
}
LEVELS = tuple(_ENGINES)


def make_engine(level: str) -> GovernanceEngine:
    """A fresh engine for a governance level."""

    return _ENGINES[level]()


_REF = re.compile(r"^\$([\w-]+)\.([\w-]+)$")


@dataclass
class RunResult:
    scenario_id: str
    level: str
    results: list[dict[str, Any]]
    outcomes: dict[str, Any]
    metrics: dict[str, Any]
    gateway_log: list[dict[str, Any]]
    events: list[ActionEvent] = field(default_factory=list)


def _resolve_value(value: Any, outputs: dict[str, dict[str, Any]]) -> Any:
    if not isinstance(value, str):
        return value
    match = _REF.match(value)
    if match is None:
        return value
    name, key = match.groups()
    output = outputs.get(name)
    if output is not None and key in output:
        return output[key]
    return f"unresolved:{name}.{key}"


def _resolve_args(args: dict[str, Any], outputs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    for key, value in args.items():
        if isinstance(value, list):
            resolved[key] = [_resolve_value(item, outputs) for item in value]
        else:
            resolved[key] = _resolve_value(value, outputs)
    return resolved


def _record(step: Step, label: str, alternative: bool, result: GatewayResult) -> dict[str, Any]:
    decision = result.decision
    blocked = bool(decision and decision.blocked)
    return {
        "step": label,
        "agent_id": step.agent_id,
        "tool": step.tool,
        "alternative": alternative,
        "executed": result.executed,
        "ok": result.tool_result.ok,
        "blocked": blocked,
        "policy_id": decision.policy_id if blocked else None,
        "error": result.tool_result.error,
    }


def run_scenario(scenario: Scenario, level: str) -> RunResult:
    world = World.default()
    if scenario.setup is not None:
        scenario.setup(world)
    engine = make_engine(level)
    gateway = Gateway(world, engine, workflow_id=f"{scenario.scenario_id}-{level}")
    outputs: dict[str, dict[str, Any]] = {}
    results: list[dict[str, Any]] = []
    events: list[ActionEvent] = []
    completed: list[bool] = []

    def submit(step: Step, label: str, alternative: bool) -> GatewayResult:
        args = _resolve_args(step.args, outputs)
        result = gateway.submit(step.agent_id, step.tool, args)
        results.append(_record(step, label, alternative, result))
        if result.event is not None:
            events.append(result.event)
        return result

    for index, step in enumerate(scenario.steps, start=1):
        label = step.name or f"{step.tool}#{index}"
        done = submit(step, label, False)
        tried = [done]
        for alt_index, alternative in enumerate(step.alternatives, start=1):
            if not (tried[-1].decision and tried[-1].decision.blocked):
                break
            alt_label = alternative.name or f"{label}/alt{alt_index}"
            tried.append(submit(alternative, alt_label, True))
        final = tried[-1]
        ok = any(item.executed and item.tool_result.ok for item in tried)
        completed.append(ok)
        if final.executed and final.tool_result.ok:
            outputs[label] = final.tool_result.output
            if step.name:
                outputs[step.name] = final.tool_result.output

    metrics = {
        "attempts": len(results),
        "blocked": sum(1 for item in results if item["blocked"]),
        "executed": sum(1 for item in results if item["executed"]),
        "failed": sum(1 for item in results if item["executed"] and not item["ok"]),
        "alternatives_tried": sum(1 for item in results if item["alternative"]),
        "task_completed": all(completed),
    }
    return RunResult(
        scenario.scenario_id,
        level,
        results,
        outcomes(world, getattr(engine, "transaction_limit", 1000.0)),
        metrics,
        gateway.log,
        events,
    )


def replay(events: list[ActionEvent], level: str) -> list[Decision]:
    """Counterfactual: evaluate recorded events offline. Nothing executes, no outcomes."""

    engine: GovernanceEngine = _ENGINES[level]()
    return [engine.evaluate(event) for event in events]


def _first_block(events: list[ActionEvent], level: str) -> dict[str, Any]:
    for event, decision in zip(events, replay(events, level)):
        if decision.blocked:
            return {"sequence": event.sequence, "policy_id": decision.policy_id}
    return {"sequence": None, "policy_id": None}


def compare(scenario: Scenario) -> dict[str, Any]:
    runs = {level: run_scenario(scenario, level) for level in LEVELS}
    levels = {
        level: {
            "blocked": [
                {"step": item["step"], "policy_id": item["policy_id"]}
                for item in run.results
                if item["blocked"]
            ],
            "outcomes": run.outcomes,
            "metrics": run.metrics,
        }
        for level, run in runs.items()
    }
    baseline = runs["none"].events
    return {
        "scenario_id": scenario.scenario_id,
        "category": scenario.category,
        "description": scenario.description,
        "note": scenario.note,
        "levels": levels,
        "counterfactual": {
            level: _first_block(baseline, level) for level in LEVELS if level != "none"
        },
    }


def _cell(level_data: dict[str, Any]) -> str:
    blocked = level_data["blocked"]
    verdict = "harm" if level_data["outcomes"]["harm"] else "ok"
    return f"{verdict} {blocked[0]['policy_id'] if blocked else '-'}"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run harness scenarios under each level.")
    parser.add_argument("--scenario", help="run only this scenario id")
    parser.add_argument("--json", metavar="PATH", help="write full comparison output as JSON")
    args = parser.parse_args(argv)

    selected = [s for s in SCENARIOS if args.scenario in (None, s.scenario_id)]
    if not selected:
        parser.error(f"unknown scenario: {args.scenario}")
    comparisons = [compare(scenario) for scenario in selected]

    print(
        f"{'scenario':22} {'category':10} {'none':8} {'stateless':28} {'path-aware':32}"
    )
    print("-" * 104)
    for item in comparisons:
        cells = [_cell(item["levels"][level]) for level in LEVELS]
        print(
            f"{item['scenario_id']:22} {item['category']:10} "
            f"{cells[0]:8} {cells[1]:28} {cells[2]:32}"
        )
    gaps = [item for item in comparisons if item["category"] == "known_gap"]
    if gaps:
        print()
        for item in gaps:
            print(f"{item['scenario_id']}: {item['note']}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(comparisons, handle, indent=2, sort_keys=True)
            handle.write("\n")


if __name__ == "__main__":
    main()
