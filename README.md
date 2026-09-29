# Path-Aware Governance for AI-Agent Workflows

This repository contains the experimental system for comparing three runtime
governance configurations:

1. `none` — every proposed action is allowed.
2. `stateless` — each action is checked independently.
3. `path-aware` — actions are checked against workflow history shared across
   agents.

The first milestone is a deterministic, in-memory vertical slice. It fixes the
event contract and policy semantics before Kafka, concurrency, and monitoring
are introduced.

## Run the prototype

Python 3.11 or newer is recommended. The prototype has no third-party runtime
dependencies.

```bash
python3 -m pathaware_governance.demo
python3 -m unittest discover -s tests -v
python3 -m pathaware_governance.generator
python3 -m pathaware_governance.consistency
python3 -m pathaware_governance.benchmark
python3 -m pathaware_governance.reporting
python3 -m pathaware_governance.dashboard
```

For the Kafka-backed pipeline, see
[docs/kafka-pipeline.md](docs/kafka-pipeline.md). It uses the optional `kafka`
dependency group and the included single-node `compose.yaml` environment.

The local results dashboard is generated at `dashboard/index.html`. Common
development and experiment commands are also available through the `Makefile`.

## Current scope

The path-aware engine detects:

- cross-agent data exfiltration;
- privilege laundering through delegation;
- split-action evasion of a transaction limit;
- all violations caught by the stateless baseline.

It also includes legitimate near misses to verify that partial or slightly
different paths are not incorrectly blocked.

See [the architecture](docs/architecture.md) and
[experiment plan](docs/experiment-plan.md) for the contracts that later phases
will preserve.

## Planned phases

1. **Specification and vertical slice** — event schema, policy definitions,
   three engines, deterministic workflows, and tests.
2. **Dataset generation** — template-based generator, label-review sample, and
   reproducible 100–200 workflow corpus. The initial seeded 150-workflow corpus
   is generated under `data/generated/`.
3. **Kafka pipeline** — action/decision topics, replay, consumer groups, and
   alternative partitioning strategies. The adapter, topic setup, replay CLI,
   reference consumer, and local Kafka environment are implemented.
4. **Concurrent state experiments** — weak and strong consistency variants,
   stale-read instrumentation, controlled propagation backlog, and real
   multi-worker Kafka trials are implemented; see
   [docs/distributed-kafka-results.md](docs/distributed-kafka-results.md).
5. **Benchmarking and results** — repeated load sweeps, accuracy/performance
   metrics, raw results, plots, and a generated local dashboard are implemented
   under `benchmark-results/` and `dashboard/`.
