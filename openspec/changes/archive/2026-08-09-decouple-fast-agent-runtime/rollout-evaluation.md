# Fast rollout evaluation

## Deterministic qualification run

The local paired shadow summarizer was executed through
`tests/test_fast_runtime_performance_benchmark.py`. Its two representative pairs
(direct answer and one-tool flow) converged successfully in both runtimes and
exercised every required comparison field:

| Metric | Local deterministic result |
| --- | ---: |
| first-token ratio, Fast / legacy | 0.5000 |
| total-latency ratio, Fast / legacy | 0.7317 |
| model-call delta | -1.00 |
| tool-call delta | 0.00 |
| error-rate delta | 0.0000 |
| task-success delta | 0.0000 |

These fixtures qualify the measurement and gating pipeline; they are not a
claim about production model quality or cost. Before promotion in any real
deployment, run `benchmarks.fast_runtime_performance` against paired Fast and
legacy deployments and apply the thresholds in `docs/fast-agent-runtime.md`.

The default switch affects only newly created standard Runs. Existing Runs stay
on their frozen runtime throughout shadowing, rollout and rollback.
