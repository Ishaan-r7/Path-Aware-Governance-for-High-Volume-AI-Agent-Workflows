# Phase 1 Architecture

## Decision boundary

Every agent action is represented as an `ActionEvent` and evaluated immediately
before execution. A governance engine returns either `allow` or `block`, plus a
stable policy identifier and reason when blocked.

```text
agent proposes action
        |
        v
 canonical ActionEvent ---> governance engine ---> Decision ---> executor/audit log
                                  |
                                  +-- none
                                  +-- stateless rules
                                  +-- path-aware state
```

All three modes consume the same event stream. This makes their accuracy and
performance comparable and prevents mode-specific test data.

## Identity and ordering

- `event_id` uniquely identifies an attempted action.
- `workflow_id` (also the future Kafka trace/correlation key) connects actions
  that contribute to one business workflow across agents.
- `agent_id` identifies the proposing agent.
- `sequence` defines authored ground-truth order within a workflow.
- `occurred_at` records event time; ingestion time and governance-decision time
  will be added by the streaming adapter.

The initial engine processes actions synchronously and therefore supplies the
strongly ordered reference result. Distributed implementations must document
what ordering and consistency they actually guarantee.

## Policy semantics

### Stateless baseline

- `S-DANGEROUS-ACTION`: block a directly prohibited action.
- `S-TRANSACTION-LIMIT`: block one transfer greater than 1,000 units.
- `S-UNAUTHORIZED-ACTOR`: block an unauthorized direct action. Requesting
  delegation is not itself blocked, allowing the laundering experiment.

### Path-aware policies

Path-aware governance includes every stateless rule and adds:

- `P-CROSS-AGENT-EXFILTRATION`: ordered `read_data` of PII, `aggregate_data`,
  `export_file`, then `transmit_external`, involving at least two agents.
- `P-PRIVILEGE-LAUNDERING`: an unauthorized agent requests a named action from
  another agent, and that named delegate later performs it.
- `P-SPLIT-ACTION-EVASION`: at least two agents transfer funds from the same
  account to the same destination; each action is under the limit but the
  workflow total exceeds it.

Near misses break one required condition and must be allowed.

## Kafka design question to test, not assume

Two partitioning/state designs will be compared:

1. **Partition by `agent_id`:** preserves per-agent order and scales naturally,
   but cross-agent checks require asynchronously replicated state and can read
   stale histories.
2. **Partition by `workflow_id`:** co-locates one workflow and preserves its
   action order, but may reduce parallelism or create hot partitions.

The synchronous engine is the correctness oracle for both designs. The project
will measure disagreement with that oracle, state-version lag, latency, and
throughput rather than treating eventual consistency as an implementation
detail.

## State and audit behavior

The prototype records attempted actions, including blocked actions, in governance
history. That history is an audit log, not a statement that the underlying
business action executed. Future events and decisions will carry explicit
`attempted`, `allowed`, and `executed` timestamps/statuses where needed.

