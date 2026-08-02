## Context

`add-governed-subagent-runtime` established durable child executions, delegated identities, attenuated catalogs, hierarchical budgets, a local child executor, coordinator, joins, recovery, cancellation, telemetry, and UI projection. Its first functional slice deliberately rejects a second active child, and the root `AgentLoop` does not call those components. Consequently Astra has governance primitives but not a production supervisor/worker loop, and independent work is serialized at the point where subagents should provide value.

The first production integration remains constrained to trusted Runs, read-only child tools, delegation by the root Agent only, and `max_depth = 1`. Concurrent execution must preserve frozen Run policy, parent reserves, lineage, approvals, fencing, recovery, and final-answer ownership.

## Goals / Non-Goals

**Goals:**

- Make bounded concurrent children the normal governed behavior whenever the frozen Run policy allows more than one active child.
- Let the trusted root Agent create a coherent fan-out group and durable join through a first-class semantic decision.
- Dispatch children concurrently without sharing mutable database or model-attribution state.
- Let the root continue unrelated Plan work and wait only at the node that consumes a join.
- Validate, merge, promote, and consume child results exactly once before root completion.
- Roll out and roll back without changing historical root-only or phase-one Runs.

**Non-Goals:**

- Recursive child delegation, depth greater than one, or allocation of descendant subtree quotas.
- Child write authority, remote Agent transports, standard-mode delegation, peer messaging, or child-owned final answers.
- Unbounded fan-out or using model instructions as a substitute for server-side quotas.

## Decisions

### 1. Replace the single-active guard with bounded concurrency

The runtime will reject creation only when the direct parent's active-child count has reached the frozen `max_parallel_children` limit. Cumulative Run and per-parent creation limits, parent reserves, hierarchical budget reservations, adaptive benefit gating, scope-overlap checks, and deployment/provider/capability semaphores remain mandatory.

The alternative of keeping a single active child during production integration was rejected because it makes the target architecture serial and prevents representative testing of attribution, scheduling, joins, cancellation, and recovery.

### 2. Expose delegation as a first-class root decision

The trusted controller will gain a `delegate_tasks` decision containing one bounded fan-out group, one to `max_parallel_children` typed `DelegationRequest` values, and one immutable join specification. Delegation remains a runtime semantic capability and will not be registered as a third-party Tool.

The existing singular `delegate_task` operation remains the internal primitive for inspection and compatibility. A new batch orchestration method will preflight all requests, reserve all quotas and budgets, create all children, and create the join in one transaction. Partial groups are not observable when preflight or persistence fails.

Sequential singular model decisions were rejected as the primary fan-out interface because they spend extra root turns, can expose half-created groups, and make join membership ambiguous.

### 3. Add a Run-scoped `SubagentSupervisor`

`RunEngine` will own a structured `SubagentSupervisor` for the duration of a trusted Run. The supervisor composes runtime operations, `AgentCoordinator`, a child runtime factory, join reconciliation, result validation/merge, and shutdown/cancellation. `AgentLoop` submits delegation intent and consumes sanitized observations; it does not own worker tasks or heartbeats.

Directly awaiting a child inside `AgentLoop` was rejected because it blocks unrelated root work. Letting `AgentLoop` manage raw `asyncio.Task` objects was rejected because lifecycle, recovery, and cancellation would become process-local rather than durable.

### 4. Isolate mutable child execution context

Every concurrent child receives a separate database session, repository/service graph, usage recorder, and model-client wrapper bound to its own `agent_execution_id`. Children may share immutable Tool Registry snapshots and the underlying HTTP connection pool, but MUST NOT share mutable model attribution or transaction state.

This is required because the current model-client binding is mutable; sharing one wrapper across concurrent children could attribute usage and events to the wrong execution.

### 5. Reconcile joins before each root decision

Before assembling each trusted root decision context, the supervisor evaluates joins whose members changed. Successful results pass schema, completion, provenance, Artifact, Evidence, and lineage validation, then the merger detects duplicates and conflicts. Verified facts and Artifacts are explicitly promoted through the exchange service.

Joins gain an exactly-once consumption lifecycle (`waiting`, `ready`, `merging`, `consumed`, `blocked`) or equivalent CAS-protected consumption metadata. The parent observation records the join, sources, facts, claims, evidence, conflicts, warnings, and open issues but never child hidden reasoning or private scratchpads.

### 6. Make join waiting dependency-scoped

`consumer_plan_node_id` becomes an executable scheduling barrier. A waiting join prevents only its consumer node from completing or releasing dependent nodes. Other ready root nodes remain schedulable. A blocked required join blocks its consumer branch; an optional failure produces a warning according to policy; a first-success join becomes ready after the first validated success and cancels only safe losers.

Globally pausing the Run for every child was rejected because it removes the benefit of supervision and violates the existing barrier-free design.

### 7. Strengthen root completion and rollout eligibility

Trusted root completion requires mandatory descendants to be terminal, required and first-success joins to be consumed, child budget reservations to be settled, required approvals to be resolved, and blocking merge conflicts to be handled. Standard Runs cannot delegate in this change because their basic completion path does not enforce these barriers.

Rollout cohort eligibility will be enforced, not merely recorded. Initial execution is trusted, read-only, depth-one, and allowlisted; `shadow` records decisions without creating children. The kill switch blocks new groups and drains or fences existing workers according to cancellation policy.

## Risks / Trade-offs

- **Concurrent model attribution can cross execution boundaries** → construct per-child model wrappers and usage recorders; test simultaneous calls with distinct execution IDs.
- **Batch creation can partially persist** → preflight and create the fan-out group, reservations, children, and join in one transaction with stable group/request idempotency keys.
- **Root and child writers can contend on SQLite** → keep transactions short, never hold a session across model/tool waits, use independent sessions, and include contention tests; production deployments should use a database suited to concurrent writers.
- **Ready joins can be merged twice after recovery** → CAS the join into `merging`, make promotions idempotent, and record the consumed parent state version.
- **The root can duplicate active child work** → expose active group scopes in decision context and retain dedupe-key and sibling-overlap rejection.
- **Provider saturation can erase latency gains** → enforce deployment, Run, provider, tool, and capability semaphores and report queue/overlap metrics.
- **Optional or first-success cancellation can hide committed effects** → retain the read-only initial scope and use existing immutable-effect/result-unknown safeguards.
- **A nominal canary could execute for unintended users** → enforce cohort eligibility against frozen Run metadata before exposing `delegate_tasks`.

## Migration Plan

1. Add schemas and persistence for fan-out group identity and exactly-once join consumption while keeping existing rows readable.
2. Add the supervisor and child runtime factory behind the disabled feature flag; run protocol and concurrency tests with `max_parallel_children = 2`.
3. Integrate `delegate_tasks`, dependency-scoped join barriers, reconciliation, and Completion Gate checks for trusted Runs.
4. Run shadow evaluation, then an allowlisted trusted read-only canary with depth one and bounded total/per-parent limits.
5. Promote by policy after quality, latency, cost, failure, cancellation, recovery, and safety gates pass.
6. Roll back by enabling the kill switch, preventing new groups, draining or fencing active children, preserving immutable effects and lineage, and returning future Runs to shadow or disabled policy.

## Open Questions

- Whether the first canary allowlist is sourced from authenticated actor roles or a deployment-level Run selector.
- Whether join consumption is represented by new status values or explicit merge/consumption columns; either choice must be CAS-protected and migration-safe.
- Whether SQLite remains a supported environment for two concurrent child workers or is limited to development after contention benchmarks.
