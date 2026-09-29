# Experiment Contract

## Primary question

How much detection quality does path-aware governance add over no governance and
stateless rules, and what latency, throughput, resource, and consistency costs
does it introduce as concurrency and workflow length increase?

## Independent variables

- Governance mode: none, stateless, path-aware.
- Concurrency: 1, 10, 25, 50, 100, and (if stable) 200 agents.
- Workflow length: short, medium, long.
- Offered event rate.
- Violation category and safe/violation class balance.
- Kafka partitioning key: agent versus workflow.
- State consistency strategy: asynchronous/weak versus coordinated/strong.

## Primary measurements

### Safety accuracy

- True/false positives and true/false negatives.
- Precision, recall, specificity, F1, and false-positive/negative rates.
- Per-policy and per-concurrency breakdowns.

### Performance

- Governance decision latency: p50, p95, p99, and maximum.
- End-to-end event latency.
- Sustained throughput and offered-versus-achieved rate.
- CPU and memory utilization.

### Consistency

- State version visible when each decision is made.
- Expected state version from the strongly ordered reference engine.
- Stale-read count/rate and version lag.
- False negatives attributable to stale state rather than incorrect policy
  semantics.

## Ground truth

Labels originate in versioned workflow templates. They are never inferred from
an engine's output. Before producing the full corpus, two team members will
independently label an approximately 20-workflow validation sample using only
the written policy definitions. Templates or definitions will be revised until
agreement is complete, and then frozen for the benchmark.

Every generated record will include:

- template and template-version identifiers;
- random seed;
- workflow category and expected policy;
- expected violating event (if any);
- the condition changed for a near miss.

## Experimental controls

- Replay the identical corpus and event schedule through every governance mode.
- Warm up each configuration before measurement.
- Run multiple trials and report dispersion, not only averages.
- Separate producer backpressure from governance-decision latency.
- Pin software/configuration versions and record the machine profile.
- Use the synchronous path-aware implementation as a correctness oracle.

## Phase 1 acceptance criteria

- One canonical event model is shared by all engines.
- All three modes execute from one command.
- Each proposed policy has a deterministic positive example.
- A near miss is allowed by the path-aware engine.
- Automated tests verify expected mode-by-workflow results.

