# Runtime Core Benchmark

## Scope

This report closes the performance and reliability comparison for the single-Loop migration. The latency run used the real HTTP Run API, SSE stream, persistence, usage aggregation, and the deterministic OpenAI-compatible model stub. It exercised three paired, tool-free cases three times per mode after one warm-up pair, with complete provider usage coverage.

Command:

```bash
python -m benchmarks.mode_performance \
  --base-url http://127.0.0.1:8011 \
  --case all --runs-per-case 3 --warmup 1
```

The local database was a freshly migrated SQLite database. The provider stub reported 100 input and 40 output tokens per invocation and introduced a 2 ms first-token delay.

## Paired result

| Measure | Standard | Trusted | Trusted / Standard |
| --- | ---: | ---: | ---: |
| Successful runs | 9 / 9 | 9 / 9 | equal |
| Mean tool calls | 0.0 | 0.0 | equal |
| Mean model calls | 1.0 | 5.0 | 5.00x |
| Mean input tokens | 100 | 500 | 5.00x |
| Mean output tokens | 40 | 200 | 5.00x |
| Mean total tokens | 140 | 700 | 5.00x |
| Usage coverage | 100% | 100% | equal |
| Mean completion latency | 27.11 ms | 154.68 ms | 5.71x |
| p50 completion latency | 26.79 ms | 149.98 ms | 5.60x |

The token result is intentionally deterministic: Standard performs one direct model decision while this Trusted fixture performs contract creation, Plan preparation, governed node/root decisions, and Memory candidate extraction with five metered invocations. Astra does not own a provider-price table, so it does not fabricate a currency amount. For any provider whose price is linear in uncached input/output tokens, the controlled cost proxy is exactly 5.00x; a real currency comparison must apply the selected provider's current rates to the recorded token categories.

## Latency investigation

The first diagnostic run showed about 15.8 seconds for Trusted. Service logs proved that Run writes and independently metered usage writes were contending for SQLite's single writer lock; three writes reached its five-second busy timeout and some invocation records were lost. Run management and Memory finalization now commit completed state before external model calls. The repeated final run has 100% metering coverage and reduced Trusted mean latency to 154.68 ms. This is a 99.0% reduction from the defective diagnostic result.

The run also exposed and fixed a real Standard SSE ordering race: terminal status could be committed before `answer.completed`, allowing the event stream to close early. Run management now completes or pauses the answer stream before persisting the terminal status, and a paired Standard/Trusted regression assertion protects that ordering.

## Recovery and functional comparison

Recovery is verified by the characterization suite rather than by injecting faults into a latency sample. Both modes cover restart/resume, cancellation, approval wait/resume, idempotent replay, and result-unknown behavior; Trusted additionally covers Plan/DAG recovery, leases, heartbeats, fan-out/fan-in, replan drain, and CompletionGate barriers. The final full test run is the success-rate authority. Tool behavior is covered by paired tool-success and permission/approval characterizations; the latency cases deliberately report zero tool calls so provider and governance overhead are not mixed with external tool variance.

## Conclusion

The target behavior is preserved: Standard remains the lightweight one-call path, while Trusted pays explicit planning and governance overhead through capabilities on the same Loop. No additional model or tool call was introduced by a duplicate controller. The SQLite transaction contention and the SSE terminal-order defect found during investigation were both corrected.
