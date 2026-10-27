"""Minimal MCP server (stdio, newline-delimited JSON-RPC 2.0) over the harness tools.

Every tools/call goes through the Gateway, so an MCP client such as Codex CLI reaches the
fake world only through governance. stdout is the protocol channel; logs go to stderr. After
each tool call the run record is rewritten atomically so a killed process still leaves data.
The record holds only the run, never environment variables or credentials.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from typing import Any

from .agent import PILOT_TASKS
from .gateway import Gateway
from .outcomes import outcomes
from .runner import LEVELS, make_engine
from .tools import TOOL_SPECS
from .world import World

SERVER_INFO = {"name": "governance-harness", "version": "0.1.0"}
DEFAULT_PROTOCOL_VERSION = "2025-06-18"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def tool_list() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "description": spec["description"],
            "inputSchema": spec["parameters"],
        }
        for name, spec in TOOL_SPECS.items()
    ]


class McpSession:
    """One governed run: a world, an engine, a gateway and an optional record file."""

    def __init__(
        self,
        level: str,
        agent_id: str,
        workflow_id: str,
        record_path: str | None = None,
        task_id: str | None = None,
    ) -> None:
        self.level = level
        self.agent_id = agent_id
        self.workflow_id = workflow_id
        self.record_path = record_path
        self.task_id = task_id
        self.world = World.default()
        if task_id is not None:
            tasks = {task.task_id: task for task in PILOT_TASKS}
            if task_id not in tasks:
                raise ValueError(f"unknown task: {task_id}")
            setup = tasks[task_id].setup
            if setup is not None:
                setup(self.world)
        self.engine = make_engine(level)
        self.gateway = Gateway(self.world, self.engine, workflow_id=workflow_id)
        self.tool_calls = 0

    def handle(self, message: Any) -> dict[str, Any] | None:
        """Return the JSON-RPC response for a message, or None for notifications."""

        if not isinstance(message, dict):
            return _error(None, INVALID_REQUEST, "request must be a JSON object")
        method = message.get("method")
        has_id = "id" in message
        request_id = message.get("id")
        if not isinstance(method, str):
            return _error(request_id, INVALID_REQUEST, "missing method") if has_id else None
        if not has_id:
            return None
        params = message.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return _error(request_id, INVALID_PARAMS, "params must be an object")

        if method == "initialize":
            version = params.get("protocolVersion")
            return _result(
                request_id,
                {
                    "protocolVersion": (
                        version if isinstance(version, str) else DEFAULT_PROTOCOL_VERSION
                    ),
                    "capabilities": {"tools": {}},
                    "serverInfo": dict(SERVER_INFO),
                },
            )
        if method == "ping":
            return _result(request_id, {})
        if method == "tools/list":
            return _result(request_id, {"tools": tool_list()})
        if method == "tools/call":
            return self._call(request_id, params)
        return _error(request_id, METHOD_NOT_FOUND, f"method not found: {method}")

    def _call(self, request_id: Any, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        if not isinstance(name, str):
            return _error(request_id, INVALID_PARAMS, "tools/call needs a tool name")
        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}
        result = self.gateway.submit(self.agent_id, name, arguments)
        self.tool_calls += 1
        tool = result.tool_result
        text = json.dumps(
            {"ok": tool.ok, "output": tool.output, "error": tool.error},
            sort_keys=True,
            default=repr,
        )
        self.write_record()
        return _result(
            request_id, {"content": [{"type": "text", "text": text}], "isError": not tool.ok}
        )

    def record(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "agent_id": self.agent_id,
            "workflow_id": self.workflow_id,
            "task": self.task_id,
            "tool_calls": self.tool_calls,
            "gateway_log": self.gateway.log,
            "outcomes": outcomes(self.world, getattr(self.engine, "transaction_limit", 1000.0)),
            "world": self.world.snapshot(),
        }

    def write_record(self) -> None:
        if not self.record_path:
            return
        directory = os.path.dirname(os.path.abspath(self.record_path))
        fd, temp = tempfile.mkstemp(dir=directory, prefix=".record-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self.record(), handle, indent=2, sort_keys=True, default=repr)
                handle.write("\n")
            os.replace(temp, self.record_path)
        except BaseException:
            try:
                os.unlink(temp)
            except OSError:
                pass
            raise


def serve(session: McpSession, stdin=None, stdout=None) -> None:
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            response: dict[str, Any] | None = _error(None, PARSE_ERROR, "parse error")
        else:
            try:
                response = session.handle(message)
            except Exception as exc:  # never crash on bad input
                print(f"internal error: {type(exc).__name__}", file=sys.stderr)
                request_id = message.get("id") if isinstance(message, dict) else None
                response = _error(request_id, -32603, "internal error")
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="MCP server for the governance harness tools.")
    parser.add_argument("--level", required=True, choices=LEVELS)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--workflow-id", required=True)
    parser.add_argument("--record", required=True, metavar="PATH")
    parser.add_argument("--task", help="pilot task id whose setup is applied to the world")
    args = parser.parse_args(argv)
    try:
        session = McpSession(args.level, args.agent_id, args.workflow_id, args.record, args.task)
    except ValueError as exc:
        parser.error(str(exc))
    serve(session)


if __name__ == "__main__":
    main()
