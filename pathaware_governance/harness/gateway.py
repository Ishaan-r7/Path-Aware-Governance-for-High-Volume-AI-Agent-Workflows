"""Governance gateway: every proposed tool call is checked before it touches the world."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from ..engines import GovernanceEngine
from ..models import ActionEvent, Decision
from .tools import TOOLS, ToolResult, _loggable, validate_call
from .world import World

_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True, slots=True)
class GatewayResult:
    tool_result: ToolResult
    event: ActionEvent | None
    decision: Decision | None
    executed: bool


def flow_id_for(world: World, lineage: frozenset[str] | set[str]) -> str | None:
    """Derive a data_flow_id from a lineage.

    The id joins the sorted ids of the sources that carry the lineage's highest
    classification, so customers merged with public docs is still "customers" and
    matches the id of the original read. Known limit: two different sources at the
    top classification (e.g. customers and hr, both pii) give "customers+hr", which
    does not match the earlier single-source read, so a path-aware chain rule that
    requires one flow id will not see them as the same flow.
    """

    known = [source_id for source_id in lineage if source_id in world.sources]
    if not known:
        return None
    top = world.classification_of(lineage)
    return "+".join(sorted(s for s in known if world.sources[s].classification == top))


class Gateway:
    """Validate -> build event from world state -> ask engine -> run tool if allowed.

    Event fields come from the harness and world, never from agent claims. An allowed
    tool may still fail on input (for example an overdraft); the gateway then reports the
    outcome so the engine does not count that attempt toward cumulative limits. Blocked calls never run and never touch
    the world, including its ledger; the engine still records them in its history.
    """

    def __init__(
        self,
        world: World,
        engine: GovernanceEngine,
        workflow_id: str,
        *,
        attach_provenance: bool = True,
    ) -> None:
        self.world = world
        self.engine = engine
        self.workflow_id = workflow_id
        self.attach_provenance = attach_provenance
        self._sequence = 0
        self._log: list[dict[str, Any]] = []

    @property
    def log(self) -> list[dict[str, Any]]:
        return self._log

    def submit(self, agent_id: str, tool_name: str, args: dict[str, Any]) -> GatewayResult:
        error = validate_call(tool_name, args)
        if error:
            result = GatewayResult(ToolResult(False, {}, error), None, None, False)
            self._record(agent_id, tool_name, args, result)
            return result

        self._sequence += 1
        event = self._build_event(agent_id, tool_name, args, self._sequence)
        decision = self.engine.evaluate(event)
        if decision.blocked:
            reason = f"Blocked by governance policy {decision.policy_id}: {decision.reason}"
            tool_result = ToolResult(
                False, {"blocked": True, "policy_id": decision.policy_id}, reason
            )
            result = GatewayResult(tool_result, event, decision, False)
        else:
            tool_result = TOOLS[tool_name](self.world, agent_id, **args)
            self.engine.record_outcome(event.event_id, tool_result.ok)
            result = GatewayResult(tool_result, event, decision, True)
        self._record(agent_id, tool_name, args, result)
        return result

    def _record(
        self, agent_id: str, tool_name: str, args: Any, result: GatewayResult
    ) -> None:
        event, decision = result.event, result.decision
        self._log.append(
            {
                "sequence": event.sequence if event else None,
                "agent_id": agent_id,
                "tool": tool_name,
                "args": _loggable(args) if isinstance(args, dict) else repr(args),
                "event": _event_dict(event) if event else None,
                "decision": (
                    {
                        "outcome": str(decision.outcome),
                        "policy_id": decision.policy_id,
                        "reason": decision.reason,
                    }
                    if decision
                    else None
                ),
                "executed": result.executed,
                "ok": result.tool_result.ok,
                "error": result.tool_result.error,
            }
        )

    def _flow(self, lineage: frozenset[str] | None) -> dict[str, Any]:
        if not self.attach_provenance or lineage is None:
            return {}
        flow_id = flow_id_for(self.world, lineage)
        return {} if flow_id is None else {"data_flow_id": flow_id}

    def _build_event(
        self, agent_id: str, tool: str, args: dict[str, Any], sequence: int
    ) -> ActionEvent:
        world = self.world
        fields: dict[str, Any] = {
            "authorized": world.is_authorized(agent_id, tool),
            "attributes": {},
        }
        attributes: dict[str, Any] = fields["attributes"]
        lineage: frozenset[str] | None = None

        if tool == "read_data":
            source = world.sources.get(args["source_id"])
            fields["resource_id"] = args["source_id"]
            fields["resource_type"] = "data_source"
            if source is not None:
                fields["data_classification"] = source.classification
                lineage = frozenset({args["source_id"]})
        elif tool == "aggregate_data":
            fields["resource_type"] = "artifact"
            artifacts = [world.artifacts.get(item) for item in args["artifact_ids"]]
            if all(artifacts):
                lineage = frozenset().union(*(item.lineage for item in artifacts))
                fields["data_classification"] = world.classification_of(lineage)
        elif tool in ("export_file", "transmit_external"):
            key = "artifact_id" if tool == "export_file" else "file_id"
            artifact = world.artifacts.get(args[key])
            fields["resource_id"] = args[key]
            fields["resource_type"] = "artifact" if tool == "export_file" else "file"
            if artifact is not None:
                fields["data_classification"] = artifact.classification
                lineage = artifact.lineage
            if tool == "transmit_external":
                external = world.is_external(args["destination"])
                fields["destination"] = "external" if external else "internal"
                attributes["destination_host"] = args["destination"]
        elif tool == "transfer_funds":
            fields["resource_id"] = args["from_account"]
            fields["resource_type"] = "account"
            fields["destination"] = args["destination"]
            fields["amount"] = args["amount"]
        elif tool == "request_delegation":
            fields["authorized"] = world.is_authorized(agent_id, args["requested_action"])
            attributes["requested_action"] = args["requested_action"]
            attributes["delegate_to"] = args["delegate_to"]
        elif tool == "delegated_action":
            request = world.delegation_requests.get(args["request_id"])
            if request is None:
                fields["authorized"] = world.is_authorized(agent_id, tool)
            else:
                attributes["requested_action"] = request.requested_action
                fields["authorized"] = world.is_authorized(agent_id, request.requested_action)
        elif tool == "delete_production_database":
            fields["resource_type"] = "database"

        attributes.update(self._flow(lineage))
        return ActionEvent(
            event_id=f"{self.workflow_id}-e{sequence}",
            workflow_id=self.workflow_id,
            agent_id=agent_id,
            sequence=sequence,
            action=tool,
            occurred_at=_EPOCH + timedelta(seconds=sequence),
            **fields,
        )


def _event_dict(event: ActionEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "workflow_id": event.workflow_id,
        "agent_id": event.agent_id,
        "sequence": event.sequence,
        "action": event.action,
        "resource_id": event.resource_id,
        "resource_type": event.resource_type,
        "data_classification": event.data_classification,
        "destination": event.destination,
        "amount": event.amount,
        "authorized": event.authorized,
        "occurred_at": event.occurred_at.isoformat(),
        "attributes": dict(event.attributes),
    }
