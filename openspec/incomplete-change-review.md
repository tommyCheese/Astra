# Incomplete OpenSpec Change Review

Baseline: 2026-08-12. This is an audit index for review; it does not authorize implementation.

## Incomplete Changes

| Change | Progress | Current state | Remaining review focus |
|---|---:|---|---|
| `add-trusted-execution-graph-workbench` | 65/66 | implementation complete except browser acceptance | live/reconnect/revision/half-screen/mobile/theme/keyboard/reduced-motion evidence |
| `add-parallel-dag-execution` | 67/68 | implementation complete except browser acceptance | multi-running/fan-in/resource wait/approval/failure/cancel evidence; share one browser run with graph workbench |
| `adopt-ag-ui-protocol-adapter` | 64/76 | backend and first-party entry surface exist; parity and rollout are incomplete | public tool payload policy, reconnect/conformance/fault injection, established-chat parity, default-enabled rollout mismatch |
| `align-agent-context-compaction` | 52/68 | core V2 compaction is implemented | child reference/overflow correctness, recovery/telemetry/crash proof, long-loop evaluation and rollout |
| `add-governed-agent-hooks` | 0/74 | proposal only; no runtime implementation | approve event catalog, sync/async split, admission authority, managed-only first phase and insertion boundaries |

## Dependency and Review Order

1. Review the AG-UI default-enabled mismatch first: current code selects AG-UI by default while tasks 12.6–12.7 still require staged gates. Decide whether to restore default-off later or formally revise the rollout contract before implementation continues.
2. Run one shared browser acceptance session for the graph workbench and parallel DAG changes; if it passes, both become archive candidates without additional feature work.
3. Review compaction's remaining safety/evaluation work. Hook integration with compaction must wait for these boundary semantics, but the Hook proposal itself can be reviewed now.
4. Review the Hook proposal as a contract-only change. Implementation remains explicitly deferred until the event catalog, synchronous admission authority and staged handler rollout are approved.

## Cross-Change Boundaries

- Canonical authority remains Astra Run/Plan/Agent state and persisted RunEvent; AG-UI and Hook observation are independent projections from those facts.
- Hook does not become arbitrary middleware inside the canonical Agent Loop. Observation uses the trusted lifecycle observer path; admission attaches only at explicit application boundaries.
- AG-UI is not required for backend Hooks. Hook management UI may later reuse its transport-neutral store/component pattern.
- Parallel DAG and graph workbench share browser evidence but keep separate execution and visualization specifications.
- Compaction owns checkpoint mutation. Hooks may observe or apply narrowly defined admission, never rewrite protected prefixes or checkpoints.

## Completed but Unarchived

The following are not incomplete proposals; they are archival housekeeping candidates:

- `converge-agent-runtime-core` — 61/61
- `clean-runtime-package-boundaries` — 22/22
- `reduce-backend-navigation-cost` — 13/13
