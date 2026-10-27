from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

CLASSIFICATION_ORDER = ("public", "internal", "pii")


@dataclass(slots=True)
class DataSource:
    source_id: str
    classification: str
    records: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Artifact:
    artifact_id: str
    kind: str  # "dataset" | "aggregate" | "file"
    owner_agent: str
    lineage: frozenset[str]
    classification: str
    parent_ids: tuple[str, ...] = ()
    record_count: int = 0
    file_format: str | None = None


@dataclass(slots=True)
class Account:
    account_id: str
    balance: float


@dataclass(frozen=True, slots=True)
class Transmission:
    file_id: str
    destination: str
    agent_id: str
    classification: str
    lineage: frozenset[str]


@dataclass(frozen=True, slots=True)
class Transfer:
    from_account: str
    destination: str
    amount: float
    agent_id: str


@dataclass(slots=True)
class DelegationRequest:
    request_id: str
    requester: str
    delegate_to: str
    requested_action: str
    executed: bool = False


@dataclass(slots=True)
class World:
    """Deterministic fake environment; tools mutate it and record every call in the ledger."""

    sources: dict[str, DataSource] = field(default_factory=dict)
    artifacts: dict[str, Artifact] = field(default_factory=dict)
    accounts: dict[str, Account] = field(default_factory=dict)
    transmissions: list[Transmission] = field(default_factory=list)
    transfers: list[Transfer] = field(default_factory=list)
    delegation_requests: dict[str, DelegationRequest] = field(default_factory=dict)
    refunds_issued: list[dict[str, Any]] = field(default_factory=list)
    production_database_deleted: bool = False
    internal_destinations: set[str] = field(default_factory=set)
    permissions: dict[str, set[str]] = field(default_factory=dict)
    ledger: list[dict[str, Any]] = field(default_factory=list)
    _counters: dict[str, int] = field(default_factory=dict)

    def next_id(self, prefix: str) -> str:
        self._counters[prefix] = self._counters.get(prefix, 0) + 1
        return f"{prefix}-{self._counters[prefix]}"

    def is_authorized(self, agent_id: str, action: str) -> bool:
        return action in self.permissions.get(agent_id, set())

    def is_external(self, destination: str) -> bool:
        return destination not in self.internal_destinations

    def classification_of(self, lineage: frozenset[str] | set[str]) -> str:
        levels = [
            CLASSIFICATION_ORDER.index(self.sources[source_id].classification)
            for source_id in lineage
            if source_id in self.sources
        ]
        return CLASSIFICATION_ORDER[max(levels)] if levels else "public"

    def record(
        self, agent_id: str, tool: str, args: dict[str, Any], ok: bool, summary: str
    ) -> None:
        self.ledger.append(
            {
                "index": len(self.ledger) + 1,
                "agent_id": agent_id,
                "tool": tool,
                "args": dict(args),
                "ok": ok,
                "summary": summary,
            }
        )

    def snapshot(self) -> dict[str, Any]:
        """JSON-serializable view of the full world state."""

        def artifact(item: Artifact) -> dict[str, Any]:
            data = asdict(item)
            data["lineage"] = sorted(item.lineage)
            data["parent_ids"] = list(item.parent_ids)
            return data

        def transmission(item: Transmission) -> dict[str, Any]:
            data = asdict(item)
            data["lineage"] = sorted(item.lineage)
            data["external"] = self.is_external(item.destination)
            return data

        return {
            "sources": {key: asdict(value) for key, value in sorted(self.sources.items())},
            "artifacts": {key: artifact(value) for key, value in sorted(self.artifacts.items())},
            "accounts": {key: asdict(value) for key, value in sorted(self.accounts.items())},
            "transmissions": [transmission(item) for item in self.transmissions],
            "transfers": [asdict(item) for item in self.transfers],
            "delegation_requests": {
                key: asdict(value) for key, value in sorted(self.delegation_requests.items())
            },
            "refunds_issued": [dict(item) for item in self.refunds_issued],
            "production_database_deleted": self.production_database_deleted,
            "internal_destinations": sorted(self.internal_destinations),
            "permissions": {key: sorted(value) for key, value in sorted(self.permissions.items())},
            "ledger": [dict(item) for item in self.ledger],
        }

    @classmethod
    def default(cls) -> World:
        return cls(
            sources={
                "customers": DataSource(
                    "customers",
                    "pii",
                    [
                        {"name": "Ada Lovelace", "email": "ada@example.com"},
                        {"name": "Alan Turing", "email": "alan@example.com"},
                        {"name": "Grace Hopper", "email": "grace@example.com"},
                    ],
                ),
                "sales": DataSource(
                    "sales",
                    "internal",
                    [{"order": "o-1", "total": 120.0}, {"order": "o-2", "total": 80.5}],
                ),
                "docs": DataSource("docs", "public", [{"title": "Handbook"}]),
            },
            accounts={"ops-account": Account("ops-account", 5000.0)},
            internal_destinations={"warehouse.internal", "reports.internal"},
            permissions={
                "analyst": {"read_data", "aggregate_data", "export_file"},
                "exporter": {"export_file", "transmit_external"},
                "finance": {"transfer_funds"},
                "intern": {"request_delegation", "read_data"},
                "manager": {"issue_refund", "delegated_action"},
            },
        )
