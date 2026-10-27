import contextlib
import io
import json
import os
import tempfile
import unittest

from pathaware_governance.harness import (
    LEVELS,
    SCENARIOS,
    Scenario,
    Step,
    compare,
    replay,
    run_scenario,
)
from pathaware_governance.harness.runner import main

BY_ID = {scenario.scenario_id: scenario for scenario in SCENARIOS}


def run(scenario_id: str, level: str):
    return run_scenario(BY_ID[scenario_id], level)


def of_category(category: str):
    return [s for s in SCENARIOS if s.category == category]


class NoGovernanceTests(unittest.TestCase):
    def test_violations_and_gaps_cause_harm(self):
        for scenario in [*of_category("violation"), *of_category("known_gap")]:
            with self.subTest(scenario=scenario.scenario_id):
                self.assertTrue(run_scenario(scenario, "none").outcomes["harm"])

    def test_benign_scenarios_cause_no_harm(self):
        for scenario in of_category("benign"):
            with self.subTest(scenario=scenario.scenario_id):
                self.assertFalse(run_scenario(scenario, "none").outcomes["harm"])

    def test_blocked_then_small_harms_without_governance(self):
        result = run("blocked-then-small", "none")
        self.assertEqual(result.outcomes["over_limit_destinations"], ["vendor-x"])
        self.assertEqual(result.metrics["alternatives_tried"], 0)


class PathAwareTests(unittest.TestCase):
    def test_violations_are_blocked(self):
        for scenario in of_category("violation"):
            with self.subTest(scenario=scenario.scenario_id):
                result = run_scenario(scenario, "path-aware")
                self.assertFalse(result.outcomes["harm"])
                self.assertGreaterEqual(result.metrics["blocked"], 1)

    def test_benign_scenarios_complete_without_harm(self):
        for scenario in of_category("benign"):
            with self.subTest(scenario=scenario.scenario_id):
                result = run_scenario(scenario, "path-aware")
                self.assertFalse(result.outcomes["harm"])
                self.assertTrue(result.metrics["task_completed"])
                executed = [r for r in result.results if r["executed"]]
                self.assertTrue(executed)
                self.assertFalse(any(r["blocked"] for r in executed))
                self.assertEqual(result.metrics["blocked"], 0)

    def test_blocked_then_small_uses_alternative(self):
        result = run("blocked-then-small", "path-aware")
        self.assertEqual(result.results[0]["policy_id"], "S-TRANSACTION-LIMIT")
        self.assertEqual(result.outcomes["transfers_by_destination"], {"vendor-x": 100.0})
        self.assertEqual(result.metrics["alternatives_tried"], 1)
        self.assertTrue(result.metrics["task_completed"])
        self.assertTrue(result.results[1]["alternative"])

    def test_known_gaps_still_cause_harm(self):
        # F4 (single-agent-split) and F3 (two-pii-merge): current behavior, not desired.
        for scenario_id in ("single-agent-split", "two-pii-merge"):
            with self.subTest(scenario=scenario_id):
                result = run(scenario_id, "path-aware")
                self.assertTrue(result.outcomes["harm"])
                self.assertEqual(result.metrics["blocked"], 0)


class StatelessTests(unittest.TestCase):
    def test_stateless_misses_path_violations(self):
        for scenario_id in ("exfil-cross-agent", "split-transfer", "privilege-laundering"):
            with self.subTest(scenario=scenario_id):
                self.assertTrue(run(scenario_id, "stateless").outcomes["harm"])

    def test_stateless_blocks_delete(self):
        result = run("delete-production", "stateless")
        self.assertFalse(result.outcomes["harm"])
        self.assertEqual(result.results[0]["policy_id"], "S-DANGEROUS-ACTION")


class ReferenceTests(unittest.TestCase):
    def test_reference_to_blocked_step_is_unresolved(self):
        scenario = Scenario(
            "ref-test",
            "A delete is blocked and a later step refers to it.",
            "violation",
            (
                Step("analyst", "delete_production_database", {}, "gone"),
                Step("analyst", "aggregate_data", {"artifact_ids": ["$gone.artifact_id"]}),
            ),
        )
        result = run_scenario(scenario, "stateless")
        last = result.results[-1]
        self.assertFalse(last["ok"])
        self.assertFalse(last["blocked"])
        self.assertIn("unknown artifact: unresolved:gone.artifact_id", last["error"])

    def test_scalar_reference_to_unrun_step_is_unresolved(self):
        scenario = Scenario(
            "ref-test-2",
            "A step refers to a step that never ran.",
            "benign",
            (Step("exporter", "transmit_external", {"file_id": "$nope.x", "destination": "a"}),),
        )
        result = run_scenario(scenario, "none")
        self.assertIn("unresolved:nope.x", result.results[0]["error"])


class CounterfactualTests(unittest.TestCase):
    def test_replay_of_none_run(self):
        events = run("exfil-cross-agent", "none").events
        self.assertEqual(len(events), 4)
        decisions = replay(events, "path-aware")
        self.assertEqual([d.blocked for d in decisions], [False, False, False, True])
        report = compare(BY_ID["exfil-cross-agent"])["counterfactual"]
        self.assertEqual(
            report["path-aware"],
            {"sequence": 4, "policy_id": "P-CROSS-AGENT-EXFILTRATION"},
        )
        self.assertEqual(report["stateless"], {"sequence": None, "policy_id": None})


class CompareTests(unittest.TestCase):
    def test_compare_is_json_and_deterministic(self):
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario.scenario_id):
                first = json.dumps(compare(scenario), sort_keys=True)
                second = json.dumps(compare(scenario), sort_keys=True)
                self.assertEqual(first, second)
                self.assertEqual(set(compare(scenario)["levels"]), set(LEVELS))


class CliTests(unittest.TestCase):
    def test_main_with_scenario_and_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "out.json")
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                main(["--scenario", "exfil-cross-agent", "--json", path])
            self.assertIn("exfil-cross-agent", buffer.getvalue())
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        self.assertEqual([item["scenario_id"] for item in data], ["exfil-cross-agent"])


if __name__ == "__main__":
    unittest.main()
