from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Protocol

from .models import ActionEvent, Decision, DecisionOutcome


class GovernanceEngine(Protocol):
    mode: str

    def evaluate(self, event: ActionEvent) -> Decision: ...


def _allow(event: ActionEvent, mode: str) -> Decision:
    return Decision(event.event_id, event.workflow_id, mode, DecisionOutcome.ALLOW)


def _block(event: ActionEvent, mode: str, policy_id: str, reason: str) -> Decision:
    return Decision(
        event.event_id,
        event.workflow_id,
        mode,
        DecisionOutcome.BLOCK,
        policy_id,
        reason,
    )


class NoGovernanceEngine:
    mode = "none"

    def evaluate(self, event: ActionEvent) -> Decision:
        return _allow(event, self.mode)


class StatelessGovernanceEngine:
    """Baseline rules that never consult prior events."""

    mode = "stateless"

    def __init__(self, transaction_limit: float = 1_000.0) -> None:
        self.transaction_limit = transaction_limit

    def evaluate(self, event: ActionEvent) -> Decision:
        if event.action == "delete_production_database":
            return _block(
                event,
                self.mode,
                "S-DANGEROUS-ACTION",
                "Direct production database deletion is prohibited.",
            )
        if event.action == "transfer_funds" and (event.amount or 0) > self.transaction_limit:
            return _block(
                event,
                self.mode,
                "S-TRANSACTION-LIMIT",
                f"Transaction exceeds the per-action limit of {self.transaction_limit:g}.",
            )
        if not event.authorized and event.action not in {"request_delegation"}:
            return _block(
                event,
                self.mode,
                "S-UNAUTHORIZED-ACTOR",
                "The proposing agent is not authorized for this action.",
            )
        return _allow(event, self.mode)


class PathAwareGovernanceEngine:
    """Stateful policy engine for ordered, cross-agent workflow paths.

    This first implementation is deliberately single-process and strongly
    ordered. Later phases will keep this interface while swapping the history
    store for weakly and strongly consistent concurrent implementations.
    """

    mode = "path-aware"

    def __init__(self, transaction_limit: float = 1_000.0) -> None:
        self.transaction_limit = transaction_limit
        self._stateless = StatelessGovernanceEngine(transaction_limit)
        self._history: dict[str, list[ActionEvent]] = defaultdict(list)

    def evaluate(self, event: ActionEvent) -> Decision:
        previous = self._history[event.workflow_id]
        stateless_decision = self._stateless.evaluate(event)

        if stateless_decision.blocked:
            decision = _block(
                event,
                self.mode,
                stateless_decision.policy_id or "S-UNKNOWN",
                stateless_decision.reason or "Stateless policy violation.",
            )
        else:
            path = [*previous, event]
            decision = (
                self._detect_data_exfiltration(event, path)
                or self._detect_privilege_laundering(event, path)
                or self._detect_split_action_evasion(event, path)
                or _allow(event, self.mode)
            )

        # Attempted actions are retained for auditability. Downstream experiments
        # can separately model whether blocked actions mutate business state.
        self.observe(event)
        return decision

    def history(self, workflow_id: str) -> tuple[ActionEvent, ...]:
        return tuple(self._history.get(workflow_id, ()))

    def observe(self, event: ActionEvent) -> None:
        """Merge an event learned from shared state without evaluating it again."""

        history = self._history[event.workflow_id]
        if any(existing.event_id == event.event_id for existing in history):
            return
        history.append(event)
        history.sort(key=lambda item: (item.sequence, item.occurred_at, item.event_id))

    def _detect_data_exfiltration(
        self, event: ActionEvent, path: list[ActionEvent]
    ) -> Decision | None:
        required = ("read_data", "aggregate_data", "export_file", "transmit_external")
        matched = _ordered_subsequence(path, required)
        flow_ids = {
            item.attributes.get("data_flow_id") for item in matched or ()
        }
        if (
            event.action == required[-1]
            and matched
            and matched[0].data_classification == "pii"
            and event.destination == "external"
            and None not in flow_ids
            and len(flow_ids) == 1
        ):
            cross_agent = len({item.agent_id for item in matched}) >= 2
            return _block(
                event,
                self.mode,
                "P-CROSS-AGENT-EXFILTRATION" if cross_agent else "P-DATA-EXFILTRATION",
                ("Cross-agent path" if cross_agent else "Action path")
                + " reads PII, aggregates it, exports it, and transmits it externally.",
            )
        return None

    def _detect_privilege_laundering(
        self, event: ActionEvent, path: list[ActionEvent]
    ) -> Decision | None:
        if event.action != "delegated_action":
            return None
        requested_action = event.attributes.get("requested_action")
        for request in reversed(path[:-1]):
            if (
                request.action == "request_delegation"
                and not request.authorized
                and request.agent_id != event.agent_id
                and request.attributes.get("requested_action") == requested_action
                and request.attributes.get("delegate_to") == event.agent_id
            ):
                return _block(
                    event,
                    self.mode,
                    "P-PRIVILEGE-LAUNDERING",
                    "An unauthorized agent induced another agent to perform its restricted action.",
                )
        return None

    def _detect_split_action_evasion(
        self, event: ActionEvent, path: list[ActionEvent]
    ) -> Decision | None:
        if event.action != "transfer_funds":
            return None
        transfers = [
            item
            for item in path
            if item.action == "transfer_funds"
            and item.destination == event.destination
            and item.resource_id == event.resource_id
        ]
        total = sum(item.amount or 0 for item in transfers)
        if total > self.transaction_limit and len({item.agent_id for item in transfers}) >= 2:
            return _block(
                event,
                self.mode,
                "P-SPLIT-ACTION-EVASION",
                f"Cross-agent transfers total {total:g}, exceeding the limit of "
                f"{self.transaction_limit:g}.",
            )
        return None


def _ordered_subsequence(
    events: Iterable[ActionEvent], required_actions: tuple[str, ...]
) -> list[ActionEvent] | None:
    matched: list[ActionEvent] = []
    next_index = 0
    for event in events:
        if event.action == required_actions[next_index]:
            matched.append(event)
            next_index += 1
            if next_index == len(required_actions):
                return matched
    return None
