"""Build, run and classify one `codex exec` run whose only tools are the governance MCP server.

Codex authenticates through its own login store; nothing here reads or writes credentials,
and the environment is passed to the child process but never recorded.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from collections.abc import Sequence
from typing import Any

from .agent import PILOT_TASKS
from .runner import LEVELS

# Codex built-in features that would be a second door around the gateway. Adjust after the
# live test shows which names the installed Codex version accepts.
BUILTIN_FEATURES_TO_DISABLE = (
    "shell_tool",
    "unified_exec",
    "view_image",
    "browser_use",
    "browser_use_external",
    "in_app_browser",
    "computer_use",
    "image_generation",
    "apps",
    "plugins",
    "tool_suggest",
    "skill_search",
    "sleep_tool",
    "goals",
    "memories",
)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_ITEM_EVENTS = ("item.started", "item.updated", "item.completed")
_QUIET_TYPES = {"agent_message", "reasoning", "todo_list", "error"}


def _toml_string(value: str) -> str:
    return json.dumps(value)  # a JSON string is a valid TOML basic string


def build_command(
    *,
    prompt: str,
    workdir: str,
    record_path: str,
    level: str,
    agent_id: str,
    workflow_id: str,
    task: str | None = None,
    model: str = "gpt-5.6-luna",
    reasoning_effort: str = "medium",
    disable_builtins: bool = True,
    extra_config: Sequence[str] = (),
) -> list[str]:
    server_args = [
        "-m",
        "pathaware_governance.harness.mcp_server",
        "--level",
        level,
        "--agent-id",
        agent_id,
        "--workflow-id",
        workflow_id,
        "--record",
        record_path,
    ]
    if task is not None:
        server_args += ["--task", task]
    command = [
        "codex",
        "exec",
        "--json",
        "--ephemeral",
        "--skip-git-repo-check",
        "--ignore-user-config",
        "--ignore-rules",
        "-s",
        "read-only",
        "-C",
        workdir,
        "-m",
        model,
        "-c",
        f"model_reasoning_effort={_toml_string(reasoning_effort)}",
        "-c",
        f"mcp_servers.governance.command={_toml_string(sys.executable)}",
        "-c",
        "mcp_servers.governance.args=[" + ", ".join(_toml_string(a) for a in server_args) + "]",
        "-c",
        f"mcp_servers.governance.env={{PYTHONPATH={_toml_string(_REPO_ROOT)}}}",
    ]
    if disable_builtins:
        for feature in BUILTIN_FEATURES_TO_DISABLE:
            command += ["--disable", feature]
        command += ["-c", 'web_search="disabled"']
    for item in extra_config:
        command += ["-c", item]
    command.append(prompt)
    return command


def run(command: list[str], *, events_path: str, timeout: int = 600) -> int:
    """Run the command; stdout (JSONL events) to events_path, stderr to events_path.stderr."""

    with open(events_path, "w", encoding="utf-8") as out, open(
        events_path + ".stderr", "w", encoding="utf-8"
    ) as err:
        try:
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=err,
                env=dict(os.environ),
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            err.write(f"timed out after {timeout}s\n")
            return 124
        except OSError as exc:
            err.write(f"could not start command: {type(exc).__name__}\n")
            return 127
    return completed.returncode


def _text(value: Any, limit: int = 200) -> str:
    return repr(value)[:limit]


def classify_events(lines: list[str]) -> dict[str, Any]:
    """Summarise Codex --json events; tolerant of unknown shapes."""

    items: dict[str, dict[str, Any]] = {}  # item key -> latest item, completed preferred
    done: set[str] = set()
    order: list[str] = []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        item = event.get("item")
        kind = event.get("type")
        if not isinstance(item, dict) or kind not in _ITEM_EVENTS:
            continue
        key = str(item.get("id", f"anon-{len(order)}"))
        if key not in items:
            order.append(key)
        if key in done:
            continue
        items[key] = item
        if kind == "item.completed":
            done.add(key)

    counts: dict[str, int] = {}
    mcp_calls: list[dict[str, Any]] = []
    other: list[dict[str, Any]] = []
    final_message: str | None = None
    for key in order:
        item = items[key]
        item_type = str(item.get("type", "unknown"))
        counts[item_type] = counts.get(item_type, 0) + 1
        if item_type == "mcp_tool_call":
            mcp_calls.append(
                {
                    "server": item.get("server"),
                    "tool": item.get("tool"),
                    "status": item.get("status"),
                }
            )
        elif item_type == "agent_message":
            text = item.get("text")
            final_message = text if isinstance(text, str) else _text(text)
        elif item_type not in _QUIET_TYPES:
            other.append({"type": item_type, "item": _text(item)})
    return {
        "item_counts": counts,
        "mcp_calls": mcp_calls,
        "other_door_calls": other,
        "final_message": final_message,
    }


def main(argv: list[str] | None = None) -> int:
    tasks = {task.task_id: task for task in PILOT_TASKS}
    parser = argparse.ArgumentParser(description="Run one governed Codex exec session.")
    parser.add_argument("--level", required=True, choices=LEVELS)
    parser.add_argument("--task", required=True, choices=sorted(tasks))
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--out", required=True, metavar="DIR")
    parser.add_argument("--prompt", help="override the task goal as the prompt")
    parser.add_argument("--keep-builtins", action="store_true")
    parser.add_argument("-c", dest="extra_config", action="append", default=[], metavar="ITEM")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    out_dir = os.path.abspath(args.out)
    workspace = os.path.join(out_dir, "workspace")
    record_path = os.path.join(out_dir, "record.json")
    events_path = os.path.join(out_dir, "events.jsonl")
    os.makedirs(workspace, exist_ok=True)
    command = build_command(
        prompt=args.prompt if args.prompt is not None else tasks[args.task].goal,
        workdir=workspace,
        record_path=record_path,
        level=args.level,
        agent_id=args.agent_id,
        workflow_id=f"{args.task}-{args.level}",
        task=args.task,
        disable_builtins=not args.keep_builtins,
        extra_config=args.extra_config,
    )
    if args.dry_run:
        print(shlex.join(command))
        return 0

    code = run(command, events_path=events_path)
    with open(events_path, encoding="utf-8", errors="replace") as handle:
        summary = classify_events(handle.read().splitlines())
    try:
        with open(record_path, encoding="utf-8") as handle:
            record_outcomes = json.load(handle).get("outcomes")
    except (OSError, ValueError):
        record_outcomes = None
    summary["record_outcomes"] = record_outcomes
    summary["exit_code"] = code
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(
        f"exit={code} mcp_calls={len(summary['mcp_calls'])} "
        f"other_door_calls={len(summary['other_door_calls'])} "
        f"harm={(record_outcomes or {}).get('harm')}"
    )
    return code


if __name__ == "__main__":
    sys.exit(main())
