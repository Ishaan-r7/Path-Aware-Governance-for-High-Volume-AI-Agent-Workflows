# Shared-State Consistency Experiment

## Purpose

This experiment isolates the failure mechanism described in the proposal: a
worker can evaluate an action before earlier actions from another worker reach
its local state replica. It compares:

- **Strong state:** one immediately consistent workflow history.
- **Weak state:** eight per-worker replicas. Local writes are immediate; remote
  writes pay 2 ms base propagation delay and enter a serialized 0.25 ms update
  queue at each destination worker.

The same seeded set of 150 workflows and 396 actions is used at every level.
Concurrency controls how many workflows are active in each scheduled batch.
This keeps the safety mix constant while increasing simultaneous writes.

## Preliminary result

| Consistency | Concurrent workflows | Stale reads | Stale-read rate | Recall | False negatives |
| --- | ---: | ---: | ---: | ---: | ---: |
| Strong | 10 | 0 | 0.0% | 100% | 0 |
| Weak | 10 | 0 | 0.0% | 100% | 0 |
| Strong | 25 | 0 | 0.0% | 100% | 0 |
| Weak | 25 | 0 | 0.0% | 100% | 0 |
| Strong | 50 | 0 | 0.0% | 100% | 0 |
| Weak | 50 | 35 | 8.8% | 90% | 9 |
| Strong | 100 | 0 | 0.0% | 100% | 0 |
| Weak | 100 | 76 | 19.2% | 70% | 27 |

Under these fixed simulator parameters, the replication queue begins producing
observable stale reads between 25 and 50 concurrent workflows. At 100, 76 of
396 checks lack at least one prior workflow event and 27 violating workflows are
missed. There are no false positives in either configuration.

This is evidence that the proposed failure mode is measurable in the controlled
model. It is **not yet a claim about Kafka or production propagation times**.
Phase 5 must repeat the sweep through real concurrent workers, report multiple
trials and dispersion, and treat these numbers as a hypothesis-generating
preliminary result.

## Version-lag definition

For an event, the expected version is the set of all preceding attempted events
in its workflow. The visible version is the subset present on the assigned
worker before evaluation.

```text
version lag = count(expected prior event IDs - visible prior event IDs)
```

A read is stale when version lag is greater than zero. This set-based definition
detects gaps that a simple maximum version number could hide.

## Reproduce

```bash
python3 -m pathaware_governance.consistency \
  --concurrency 10 25 50 100 \
  --workers 8 \
  --propagation-delay-ms 2 \
  --propagation-service-ms 0.25 \
  --output benchmark-results
```

The command writes both CSV and JSON results. Change delay and service time only
as explicit experimental factors; do not tune them after observing a desired
outcome.

