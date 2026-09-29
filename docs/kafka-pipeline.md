# Kafka Pipeline

## Topics

| Topic | Key | Value |
| --- | --- | --- |
| `agent-actions` | `workflow_id` or `agent_id` | Versioned action envelope |
| `governance-decisions` | `workflow_id` | Versioned decision envelope |
| `governance-state-updates` | `workflow_id` | Replicated action-state update |

Each envelope contains `schema_version`, `run_id`, and either `event` or
`decision`. The run identifier isolates repeated experiments without deleting
topics between trials.

Kafka guarantees order only within a partition. The replay command therefore
supports both strategies required by the experiment:

- `--partition-by workflow` co-locates a workflow and preserves its event order.
- `--partition-by agent` preserves one agent's order but intentionally exposes
  cross-agent workflows to distributed-state and ordering effects.

## Local setup

Install the optional client and start the single-node KRaft broker:

```bash
python3 -m pip install -e '.[kafka]'
docker compose up -d
python3 -m pathaware_governance.streaming setup --partitions 12
```

Publish the corpus. Keep the printed `run_id`:

```bash
python3 -m pathaware_governance.streaming replay --partition-by workflow
```

The corpus currently contains 396 action events. Process that exact replay by
substituting the printed identifier:

```bash
python3 -m pathaware_governance.streaming govern \
  --mode path-aware \
  --run-id RUN_ID \
  --expected-events 396
```

Repeat with a new run identifier for each mode and partitioning strategy. This
consumer remains the single, strongly ordered reference service; the
multi-worker experiment below exercises distributed state.

Run the local transport smoke benchmark for all modes with:

```bash
python3 -m pathaware_governance.kafka_smoke
```

This benchmark includes Kafka production, consumption, synchronous offset
commits, governance evaluation, and decision publication. It is a single-node
transport check, not the multi-trial in-process load sweep.

## Multi-worker state experiment

The distributed experiment runs multiple Kafka governance consumers in one
consumer group:

```bash
python3 -m pathaware_governance.distributed \
  --concurrency 10 50 100 \
  --workers 4 \
  --trials 2
```

The strong configuration partitions actions by workflow, so a workflow and its
state remain on one Kafka worker. The weak configuration partitions actions by
agent and broadcasts asynchronously delayed state updates to every worker using
independent state-sync consumer groups. Each decision records how many required
prior workflow events were absent from the worker's local replica.

## Delivery behavior

- The producer enables idempotence and `acks=all`.
- The reference consumer disables automatic commits and commits after
  publishing a decision. Distributed workers use asynchronous commits to avoid
  serializing every action on the offset coordinator.
- This is at-least-once processing, so a crash between decision publication and
  offset commit can duplicate a decision. `event_id` plus `run_id` is the
  deduplication key.
- Development uses replication factor 1. Benchmark claims must clearly describe
  this and should not be generalized to production fault tolerance.
