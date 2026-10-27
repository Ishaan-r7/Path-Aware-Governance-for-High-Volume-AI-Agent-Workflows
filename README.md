# Path-Aware Governance for AI-Agent Workflows

This repository contains the experimental system for comparing three runtime
governance configurations:

1. `none` — every proposed action is allowed.
2. `stateless` — each action is checked independently.
3. `path-aware` — actions are checked against workflow history shared across
   agents.

The first milestone was a deterministic, in-memory vertical slice that fixed the
event contract and policy semantics before Kafka, concurrency, and monitoring
were introduced. The current work adds an agent test harness so that LLM agents,
not only scripted workflows, can be run through the same engines.

## Status

- **Engines, corpus, Kafka pipeline, shared-state variants, benchmarks, and
  dashboard** are implemented. Their results come from a seeded,
  template-generated corpus of 150 workflows, which verifies that each engine
  behaves as intended on controlled, labeled inputs. Blind human labeling of the
  review sample is not done yet, and the Kafka and shared-state results have not
  been validated with traces from real agents.
- **Agent harness** (simulated tools, governance gateway, provenance tracking,
  scripted scenarios, outcome check, model-neutral agent loop) is implemented
  and tested. No model-driven runs have been recorded yet.
- **LLM pilot** (GPT-5.6 Luna, medium reasoning effort) is in progress. A Codex
  CLI route was tested live; see [WORK_LOG.md](WORK_LOG.md) for what worked and
  the open problems.

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

## Agent harness

The harness in `pathaware_governance/harness/` lets an agent act in a simulated
environment while every tool call is checked by one of the three engines.

| Module | Role |
| --- | --- |
| `world.py`, `tools.py` | Deterministic fake world (data sources, files, accounts, permissions) and eight tools. Tools only validate input; they enforce no policy. |
| `gateway.py` | Validates each call, builds the `ActionEvent` from world state rather than agent claims, asks the engine, then runs the tool or returns a refusal. Blocked calls leave the world unchanged, but the engine still records the attempt. |
| `outcomes.py` | Reads harm from the world itself (PII sent outside, over-limit totals, unauthorized refunds, database deleted), independent of any engine. |
| `scenarios.py`, `runner.py` | Eleven scripted scenarios run under all three levels, with offline replay of ungoverned runs through the other engines. |
| `agent.py` | Model-neutral agent loop with a scripted backend for tests and three draft pilot tasks. |
| `mcp_server.py`, `codex_driver.py` | Serve the tools over MCP and run `codex exec` with built-in tools disabled. Used for the Codex route test. |

```bash
make harness                                   # scenario table for all three levels
python3 -m pathaware_governance.harness --json out.json
```

Credentials are never stored in this repository. Model access, when used, goes
through the provider's own login or an environment variable.

## Known limitations

These are documented in [docs/experiment-plan.md](docs/experiment-plan.md) and
must be labeled when scenarios or results touch them:

- Merging two different PII sources gives a combined `data_flow_id`, so the
  exfiltration rule misses that path.
- One agent splitting a payment into several under-limit transfers is not caught
  by any engine.
- A legitimate payment above the transaction limit has no allowed path, because
  agents cannot request approval.
- Codex sub-agents start separate copies of the tool server, which splits the
  workflow history, and file-edit attempts are missing from its event stream.

A change-by-change record, with the reasons for each change, is in
[WORK_LOG.md](WORK_LOG.md). The AMS 560 midterm report source is in
`report/midterm/`.

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
6. **Agent harness** — simulated tools, governance gateway, provenance tracking,
   scripted scenarios, outcome check, and a model-neutral agent loop are
   implemented under `pathaware_governance/harness/`.
7. **LLM agent pilot** — one model run through the harness to debug the loop and
   the outcome check. In progress.
8. **Scenario suite and experiment design** — independently authored scenarios,
   a held-out set, repeated trials, and pre-registered metrics. Groundwork only.
9. **Final validation** — reproducibility check on a second machine and final
   report. Planned.
