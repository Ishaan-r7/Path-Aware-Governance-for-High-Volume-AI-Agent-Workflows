# Real Multi-Worker Kafka Results

Four governance workers consumed the same 150-workflow, 396-event corpus from a
12-partition Kafka topic. Each configuration ran twice at 10, 50, and 100
concurrent workflows.

- **Strong configuration:** partition actions by workflow. A workflow remains
  on one worker, so its local history is complete.
- **Weak configuration:** partition actions by agent. Workflows can span workers,
  and each worker receives state through the `governance-state-updates` topic
  after a controlled 5 ms delay.

| State | Partition key | Concurrency | Median stale-read rate | Median recall | Median p95 end-to-end latency |
| --- | --- | ---: | ---: | ---: | ---: |
| Strong | workflow | 10 | 0.0% | 100.0% | 504.1 ms |
| Strong | workflow | 50 | 0.0% | 100.0% | 533.4 ms |
| Strong | workflow | 100 | 0.0% | 100.0% | 536.3 ms |
| Weak | agent | 10 | 24.6% | 57.8% | 510.8 ms |
| Weak | agent | 50 | 24.7% | 59.4% | 530.3 ms |
| Weak | agent | 100 | 25.9% | 56.7% | 531.5 ms |

These measurements validate the central failure mechanism on the actual Kafka
pipeline: action-level partitioning separates the workflow history required by
path policies, and asynchronously replicated state can arrive after a dependent
decision. Workflow partitioning avoided that failure in all measured trials.

The experiment runs multiple workers on one machine and uses a deliberate 5 ms
state-update delay. It demonstrates the correctness effect but is not a
multi-host production-capacity measurement. The raw per-trial measurements are
in `benchmark-results/distributed-kafka.csv`.

