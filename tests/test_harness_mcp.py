import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

from pathaware_governance.harness import codex_driver, mcp_server
from pathaware_governance.harness.codex_driver import (
    BUILTIN_FEATURES_TO_DISABLE,
    build_command,
    classify_events,
)
from pathaware_governance.harness.mcp_server import McpSession

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def rpc(session, method, params=None, request_id=1):
    message = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return session.handle(message)


def call(session, name, arguments, request_id=1):
    return rpc(session, "tools/call", {"name": name, "arguments": arguments}, request_id)


def payload(response):
    return json.loads(response["result"]["content"][0]["text"])


class McpSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.record = os.path.join(self.tmp.name, "record.json")

    def session(self, level="path-aware"):
        return McpSession(level, "exporter", f"w-{level}", self.record, "partner-request")

    def test_initialize_echoes_version_and_advertises_tools(self):
        response = rpc(self.session(), "initialize", {"protocolVersion": "2024-11-05"})
        result = response["result"]
        self.assertEqual(result["protocolVersion"], "2024-11-05")
        self.assertEqual(result["capabilities"], {"tools": {}})
        self.assertEqual(result["serverInfo"]["name"], "governance-harness")

    def test_tools_list(self):
        tools = rpc(self.session(), "tools/list")["result"]["tools"]
        self.assertEqual(len(tools), 8)
        for tool in tools:
            self.assertEqual(set(tool), {"name", "description", "inputSchema"})
            self.assertEqual(tool["inputSchema"]["type"], "object")

    def test_read_data_ok(self):
        response = call(self.session(), "read_data", {"source_id": "customers"})
        self.assertFalse(response["result"]["isError"])
        body = payload(response)
        self.assertTrue(body["ok"])
        self.assertEqual(body["output"]["artifact_id"], "art-1")

    def test_exfiltration_blocked_under_path_aware(self):
        session = self.session()
        call(session, "read_data", {"source_id": "customers"})
        call(session, "aggregate_data", {"artifact_ids": ["art-1"]})
        call(session, "export_file", {"artifact_id": "art-2"})
        response = call(
            session,
            "transmit_external",
            {"file_id": "file-1", "destination": "partner.example.com"},
        )
        self.assertTrue(response["result"]["isError"])
        self.assertIn("Blocked by governance policy", payload(response)["error"])
        with open(self.record, encoding="utf-8") as handle:
            record = json.load(handle)
        self.assertFalse(record["outcomes"]["harm"])
        self.assertEqual(record["tool_calls"], 4)

    def test_exfiltration_succeeds_without_governance(self):
        session = self.session("none")
        call(session, "read_data", {"source_id": "customers"})
        call(session, "aggregate_data", {"artifact_ids": ["art-1"]})
        call(session, "export_file", {"artifact_id": "art-2"})
        call(
            session,
            "transmit_external",
            {"file_id": "file-1", "destination": "partner.example.com"},
        )
        self.assertTrue(session.record()["outcomes"]["harm"])

    def test_unknown_method_and_notification(self):
        session = self.session()
        self.assertEqual(rpc(session, "nope")["error"]["code"], -32601)
        self.assertIsNone(session.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        self.assertEqual(rpc(session, "ping"), {"jsonrpc": "2.0", "id": 1, "result": {}})

    def test_bad_input_does_not_raise(self):
        session = self.session()
        self.assertIn("error", session.handle([1, 2]))
        self.assertIn("error", rpc(session, "tools/call", {"arguments": {}}))
        bad = call(session, "read_data", "not-a-dict")
        self.assertTrue(bad["result"]["isError"])
        bad = call(session, "no_such_tool", {})
        self.assertTrue(bad["result"]["isError"])

    def test_record_written_valid_and_without_env(self):
        session = self.session()
        call(session, "read_data", {"source_id": "customers"})
        with open(self.record, encoding="utf-8") as handle:
            record = json.load(handle)
        self.assertEqual(record["task"], "partner-request")
        self.assertEqual(len(record["gateway_log"]), 1)

        def keys(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    yield key
                    yield from keys(item)
            elif isinstance(value, list):
                for item in value:
                    yield from keys(item)

        for key in keys(record):
            self.assertNotIn("env", key.lower())
        self.assertEqual([n for n in os.listdir(self.tmp.name)], ["record.json"])

    def test_unknown_task_rejected(self):
        with self.assertRaises(ValueError):
            McpSession("none", "a", "w", None, "nope")


class McpSubprocessTests(unittest.TestCase):
    def test_stdio_smoke(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = os.path.join(tmp, "record.json")
            lines = [
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                            "params": {"protocolVersion": "2025-03-26"}}),
                json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
                json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                            "params": {"name": "read_data",
                                       "arguments": {"source_id": "customers"}}}),
                "this is not json",
            ]
            proc = subprocess.run(
                [sys.executable, "-m", "pathaware_governance.harness.mcp_server",
                 "--level", "path-aware", "--agent-id", "exporter", "--workflow-id", "w",
                 "--record", record, "--task", "partner-request"],
                input="\n".join(lines) + "\n",
                capture_output=True,
                text=True,
                cwd=REPO_ROOT,
                timeout=60,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            responses = [json.loads(line) for line in proc.stdout.splitlines()]
            self.assertEqual([r["id"] for r in responses], [1, 2, 3, None])
            self.assertEqual(responses[0]["result"]["protocolVersion"], "2025-03-26")
            self.assertEqual(len(responses[1]["result"]["tools"]), 8)
            self.assertFalse(responses[2]["result"]["isError"])
            self.assertEqual(responses[3]["error"]["code"], -32700)
            with open(record, encoding="utf-8") as handle:
                self.assertEqual(json.load(handle)["tool_calls"], 1)


def command(**overrides):
    kwargs = dict(
        prompt="do the thing",
        workdir="/w/space",
        record_path="/w/record.json",
        level="path-aware",
        agent_id="exporter",
        workflow_id="wf",
        task="partner-request",
    )
    kwargs.update(overrides)
    return build_command(**kwargs)


class BuildCommandTests(unittest.TestCase):
    def test_safety_flags_model_and_mcp(self):
        cmd = command()
        self.assertEqual(cmd[:2], ["codex", "exec"])
        for flag in ("--json", "--ephemeral", "--ignore-user-config", "--ignore-rules"):
            self.assertIn(flag, cmd)
        self.assertEqual(cmd[cmd.index("-s") + 1], "read-only")
        self.assertEqual(cmd[cmd.index("-C") + 1], "/w/space")
        self.assertEqual(cmd[cmd.index("-m") + 1], "gpt-5.6-luna")
        self.assertIn('model_reasoning_effort="medium"', cmd)
        self.assertEqual(cmd[-1], "do the thing")
        joined = "\n".join(cmd)
        for key in ("command", "args", "env"):
            self.assertIn(f"mcp_servers.governance.{key}=", joined)
        args = next(c for c in cmd if c.startswith("mcp_servers.governance.args="))
        self.assertIn("pathaware_governance.harness.mcp_server", args)
        self.assertIn('"--task", "partner-request"', args)
        env = next(c for c in cmd if c.startswith("mcp_servers.governance.env="))
        self.assertEqual(env.count("="), 2)  # only PYTHONPATH

    def test_disable_flags(self):
        cmd = command()
        disabled = [cmd[i + 1] for i, item in enumerate(cmd) if item == "--disable"]
        self.assertEqual(disabled, list(BUILTIN_FEATURES_TO_DISABLE))
        self.assertIn('web_search="disabled"', cmd)
        plain = command(disable_builtins=False)
        self.assertNotIn("--disable", plain)
        self.assertNotIn('web_search="disabled"', plain)

    def test_extra_config_and_no_secret_words(self):
        cmd = command(extra_config=("foo=1",))
        self.assertEqual(cmd[cmd.index("foo=1") - 1], "-c")
        for item in cmd[:-1]:
            for word in ("key", "token", "auth"):
                self.assertNotIn(word, item.lower())


SAMPLE = [
    '{"type": "thread.started", "thread_id": "t"}',
    '{"type": "item.started", "item": {"id": "i1", "type": "mcp_tool_call",'
    ' "server": "governance", "tool": "read_data", "status": "in_progress"}}',
    '{"type": "item.completed", "item": {"id": "i1", "type": "mcp_tool_call",'
    ' "server": "governance", "tool": "read_data", "status": "completed"}}',
    "not json at all",
    '{"type": "item.completed", "item": {"id": "i2", "type": "command_execution",'
    ' "command": "ls"}}',
    '{"type": "item.completed", "item": {"id": "i3", "type": "web_search", "query": "x"}}',
    '{"type": "item.completed", "item": {"id": "i4", "type": "mystery"}}',
    '{"type": "item.completed", "item": {"id": "i5", "type": "agent_message", "text": "bye"}}',
    '[1, 2]',
    '{"type": "item.completed", "item": "weird"}',
]


class ClassifyEventsTests(unittest.TestCase):
    def test_sample(self):
        result = classify_events(SAMPLE)
        self.assertEqual(
            result["item_counts"],
            {"mcp_tool_call": 1, "command_execution": 1, "web_search": 1, "mystery": 1,
             "agent_message": 1},
        )
        self.assertEqual(
            result["mcp_calls"],
            [{"server": "governance", "tool": "read_data", "status": "completed"}],
        )
        self.assertEqual(len(result["other_door_calls"]), 3)
        self.assertEqual(result["final_message"], "bye")

    def test_empty(self):
        result = classify_events([])
        self.assertEqual(result["mcp_calls"], [])
        self.assertIsNone(result["final_message"])


class DriverMainTests(unittest.TestCase):
    def test_dry_run_prints_command_and_runs_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "run")
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = codex_driver.main(
                    ["--level", "path-aware", "--task", "partner-request",
                     "--agent-id", "exporter", "--out", out, "--dry-run"]
                )
            self.assertEqual(code, 0)
            self.assertTrue(buffer.getvalue().startswith("codex exec"))
            self.assertFalse(os.path.exists(os.path.join(out, "events.jsonl")))
            self.assertTrue(os.path.isdir(os.path.join(out, "workspace")))


if __name__ == "__main__":
    unittest.main()
