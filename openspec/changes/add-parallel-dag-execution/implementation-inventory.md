# Parallel DAG Execution Implementation Inventory

Baseline: 2026-08-12.

## Status

- 67 of 68 tasks are marked complete.
- Backend scheduling, persistent execution attempts, leases/fencing, resource conflict handling, budgets, approval, recovery, CompletionGate barriers, event projection and frontend parallel graph state are present.
- Focused code/tests establish that unrelated known Workspace writes may overlap, hierarchical conflicts serialize, and unknown/non-idempotent external writes remain exclusive.
- Provider/capability limits currently apply within a Run; deployment-wide quotas are outside this proposal.

## Remaining Acceptance

Task 9.6 is the only remaining item: real-browser verification for multi-running nodes, fan-in, resource waits, approval pauses, branch failures, cancellation, reconnect, mobile, dark mode, keyboard and reduced motion.

Record that evidence once and reference it from `add-trusted-execution-graph-workbench` task 9.5. Do not duplicate implementation or broaden the change while closing the acceptance gate.
