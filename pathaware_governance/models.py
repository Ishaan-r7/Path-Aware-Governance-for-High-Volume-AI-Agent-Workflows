from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class DecisionOutcome(StrEnum):
    ALLOW = "allow"
    BLOCK = "block"


@dataclass(frozen=True, slots=True)
class ActionEvent:
    """Canonical proposed-action event used by every governance mode."""

    event_id: str
    workflow_id: str
    agent_id: str
    sequence: int
    action: str
    resource_id: str | None = None
    resource_type: str | None = None
    data_classification: str | None = None
    destination: str | None = None
    amount: float | None = None
    authorized: bool = True
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Decision:
    event_id: str
    workflow_id: str
    mode: str
    outcome: DecisionOutcome
    policy_id: str | None = None
    reason: str | None = None

    @property
    def blocked(self) -> bool:
        return self.outcome == DecisionOutcome.BLOCK

