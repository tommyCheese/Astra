## 1. Parallel Execution Data Model

- [x] 1.1 Add persistent NodeExecution schema with PlanNode, Plan version, attempt, dispatch batch, worker, phase, status, timestamps and state version
- [x] 1.2 Add resource lease and budget reservation schemas with execution ownership, fencing token, expiry and terminal release metadata
- [x] 1.3 Add NodeExecution references to AgentTurn, ToolCall, approval and recovery checkpoint projections
- [x] 1.4 Replace writable `AgentState.active_node_id` with versioned `active_executions` summaries while preserving legacy read migration
- [x] 1.5 Add database constraints and indexes preventing multiple current attempts for the same PlanNode and accelerating active-execution recovery scans
- [x] 1.6 Add migrations plus repository round-trip and constraint tests for executions, leases, reservations and legacy AgentState

## 2. Deterministic Batch Scheduler

- [x] 2.1 Extract ready-node calculation into a deterministic candidate projection with dependency rank, index and stable ID ordering
- [x] 2.2 Define trusted parallelism policy with server maximum, per-Run maximum and default `max_parallel_nodes=3`
- [x] 2.3 Implement atomic `claim_ready_batch` that transitions selected nodes, creates executions and emits one dispatch batch
- [x] 2.4 Enforce compare-and-swap guards so concurrent schedulers cannot claim the same node or exceed available slots
- [x] 2.5 Preserve a single-slot compatibility mode whose observable ordering matches the current serial scheduler
- [x] 2.6 Add scheduler tests for independent branches, slot exhaustion, stable ordering, fan-in eligibility, duplicate claim races and single-slot equivalence

## 3. RunCoordinator and NodeWorker

- [x] 3.1 Introduce a RunCoordinator that exclusively owns trusted Run scheduling, terminal decisions, cancellation and replan barriers
- [x] 3.2 Extract the active-node section of Agent Loop into a NodeWorker that executes exactly one NodeExecution attempt
- [x] 3.3 Give Coordinator and every concurrent Worker independent database sessions and prohibit shared-session concurrent access
- [x] 3.4 Build immutable node context snapshots containing the Plan version, node contract, dependency evidence, policy and accepted Run facts
- [x] 3.5 Scope decisions, turns, observations, evaluations, retries and tool outputs to the current NodeExecution
- [x] 3.6 Define a typed NodeExecutionResult and implement version-checked Coordinator merging of evidence, criteria, facts and node terminal state
- [x] 3.7 Detect concurrent fact conflicts and persist conflict Evaluations instead of applying last-writer-wins updates
- [x] 3.8 Add true-overlap integration tests using controlled tools that prove independent Workers execute concurrently and preserve result ownership

## 4. Concurrency Safety, Resources, and Budgets

- [x] 4.1 Derive normalized read, write and exclusive resource claims from frozen tool effect plans without exposing sensitive resource values
- [x] 4.2 Implement hierarchical resource conflict comparison for equal and ancestor/descendant workspace paths
- [x] 4.3 Implement versioned read/write leases with fencing, heartbeat, expiry and idempotent release
- [x] 4.4 Default unknown-resource, non-idempotent external write and exclusive-provider actions to deterministic serialization
- [x] 4.5 Add configurable provider and capability concurrency limits alongside the per-Run node limit
- [x] 4.6 Implement atomic reservation and settlement for turns, tool calls, model usage and other shared budgets
- [x] 4.7 Add race tests for read/read concurrency, write conflicts, unrelated writes, stale lease fencing, provider caps and budget exhaustion

## 5. Approval, Failure, Cancellation, and Replanning

- [x] 5.1 Bind approval requests and continuation payloads to NodeExecution ID, attempt, frozen action and expected state version
- [x] 5.2 Allow an execution waiting for approval to release its normal slot while independent safe branches continue
- [x] 5.3 Enter Run-level `waiting_user` only when no active or schedulable work remains and a necessary branch requires user input
- [x] 5.4 Propagate permanent node failure only to necessary descendants while allowing unrelated active branches to finish
- [x] 5.5 Implement per-attempt timeout and retry rules that automatically retry only actions proven safe and idempotent
- [x] 5.6 Propagate Run cancellation to every active Worker, stop new claims and persist terminal execution outcomes before final cancellation
- [x] 5.7 Implement `draining_for_replan` to stop claims, quiesce old-version executions and fence late commits before activating a new Plan
- [x] 5.8 Add integration tests for branch-scoped approval, approval rejection, isolated failure, fail propagation, cancellation races, timeout recovery and replan draining

## 6. Recovery, Completion, and Event Protocol

- [x] 6.1 Implement heartbeat-based recovery scanning for claimed, running, waiting, committing and result-unknown executions
- [x] 6.2 Resume recorded idempotent results from checkpoints without repeating external actions
- [x] 6.3 Route unknown non-idempotent outcomes to audited waiting or blocked states while recovering other safe branches
- [x] 6.4 Add CompletionGate barriers for active executions, unresolved approvals, pending necessary branches, fan-in nodes and unmerged budgets
- [x] 6.5 Add a single post-barrier synthesis phase that consumes accepted branch evidence and owns final answer streaming
- [x] 6.6 Define and emit ordered parallel graph events with Plan version, execution attempt and dispatch batch identifiers
- [x] 6.7 Extend authoritative Run and Plan graph snapshots with active executions, slot usage, wait reasons and overlap timestamps
- [x] 6.8 Sanitize concurrency metadata so resource summaries cannot expose credentials, raw tool inputs or host paths
- [x] 6.9 Add recovery, CompletionGate, event replay, stale-attempt, snapshot compatibility and payload-safety tests

## 7. Frontend Parallel Graph State

- [x] 7.1 Add frontend NodeExecution, parallelism summary, wait-reason, dispatch-batch and attempt types aligned with backend schemas
- [x] 7.2 Extend PlanGraphStreamState to apply multiple execution deltas with Plan and attempt guards
- [x] 7.3 Batch concurrent graph events to at most one visible update per animation frame and coalesce inconsistent-state snapshot refreshes
- [x] 7.4 Derive multiple running nodes, active branch edges, slot usage, resource waits and fan-in N/M progress from the authoritative graph state
- [x] 7.5 Preserve deterministic Dagre positions across execution-only updates and snapshot recovery
- [x] 7.6 Add reducer and selector tests for simultaneous starts, out-of-order completions, stale attempts, branch failure and reconnect snapshots

## 8. Parallel DAG Visualization

- [x] 8.1 Update TrustedExecutionGraph to render multiple running nodes and active branch edges without assuming one current node
- [x] 8.2 Add compact header summaries for active node count and used/total parallel slots
- [x] 8.3 Add accessible node badges for running, waiting resource, waiting approval, committing and cancelling phases
- [x] 8.4 Show safe wait-reason summaries and fan-in dependency progress directly on affected nodes
- [x] 8.5 Visualize branch-scoped failure and blocked propagation while preserving unrelated running branches
- [x] 8.6 Update focus-current navigation to cycle deterministically through multiple active nodes and retain center/zoom state
- [x] 8.7 Add coalesced live-region announcements for parallel starts, completions, waits and cancellation progress
- [x] 8.8 Add theme, high-contrast and reduced-motion styles that express concurrency without relying on continuous animation
- [x] 8.9 Preserve execution attempts, timing overlap and dispatch batches in node inspection and historical Run views
- [x] 8.10 Add component tests and complex fan-out/fan-in fixtures for multi-running, resource-wait, approval-wait, failure and cancellation states

## 9. Verification and Rollout

- [x] 9.1 Add configuration and metrics for requested concurrency, achieved concurrency, queue wait, resource conflicts and Worker recovery
- [x] 9.2 Run the new Coordinator at concurrency one and compare state, events, evidence and terminal results against the serial runtime
- [x] 9.3 Enable bounded concurrency for read-only conflict-free tools behind a feature flag while keeping side-effect tools exclusive
- [x] 9.4 Add elapsed-time tests proving parallel branches reduce wall-clock duration without weakening dependency ordering
- [x] 9.5 Run backend formatting, typing, migrations and full tests plus frontend typecheck, tests and production build
- [ ] 9.6 Complete browser verification for multi-running nodes, fan-in, resource waits, approval pauses, branch failures, cancellation, reconnect, mobile, dark mode, keyboard and reduced-motion
- [x] 9.7 Document configuration, operational metrics, recovery behavior, safety fallbacks and the concurrency-one rollback procedure
- [x] 9.8 Validate the OpenSpec change strictly and record rollout evidence before enabling parallel execution by default
