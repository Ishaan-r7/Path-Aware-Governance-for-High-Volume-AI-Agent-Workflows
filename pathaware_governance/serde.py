from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime

from .models import ActionEvent, Decision, DecisionOutcome


def _default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, DecisionOutcome):
        return value.value
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def encode_action(
    event: ActionEvent, *, run_id: str, metadata: dict | None = None
) -> bytes:
    return json.dumps(
        {
            "schema_version": 1,
            "run_id": run_id,
            "metadata": metadata or {},
            "event": asdict(event),
        },
        default=_default,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def decode_action(payload: bytes) -> tuple[str, ActionEvent]:
    run_id, event, _ = decode_action_with_metadata(payload)
    return run_id, event


def decode_action_with_metadata(payload: bytes) -> tuple[str, ActionEvent, dict]:
    envelope = json.loads(payload)
    raw = envelope["event"]
    raw["occurred_at"] = datetime.fromisoformat(raw["occurred_at"])
    return envelope["run_id"], ActionEvent(**raw), envelope.get("metadata", {})


def encode_decision(decision: Decision, *, run_id: str) -> bytes:
    return json.dumps(
        {"schema_version": 1, "run_id": run_id, "decision": asdict(decision)},
        default=_default,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def decode_decision(payload: bytes) -> tuple[str, Decision]:
    envelope = json.loads(payload)
    raw = envelope["decision"]
    raw["outcome"] = DecisionOutcome(raw["outcome"])
    return envelope["run_id"], Decision(**raw)
