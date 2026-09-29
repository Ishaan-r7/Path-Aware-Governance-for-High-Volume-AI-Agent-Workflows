from __future__ import annotations

from .engines import NoGovernanceEngine, PathAwareGovernanceEngine, StatelessGovernanceEngine
from .workflows import sample_workflows


def main() -> None:
    engine_types = (NoGovernanceEngine, StatelessGovernanceEngine, PathAwareGovernanceEngine)
    print(f"{'workflow':32} {'expected':10} {'none':8} {'stateless':10} {'path-aware':12}")
    print("-" * 78)
    for workflow in sample_workflows():
        outcomes: list[str] = []
        for engine_type in engine_types:
            engine = engine_type()
            decisions = [engine.evaluate(event) for event in workflow.events]
            outcomes.append("BLOCK" if any(decision.blocked for decision in decisions) else "allow")
        expected = "BLOCK" if workflow.should_violate else "allow"
        print(
            f"{workflow.workflow_id:32} {expected:10} "
            f"{outcomes[0]:8} {outcomes[1]:10} {outcomes[2]:12}"
        )


if __name__ == "__main__":
    main()

