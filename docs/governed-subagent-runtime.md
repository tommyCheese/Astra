# Astra Governed Subagent Runtime

## Scope and first supported pattern

Astra implements a governed supervisor/worker runtime. A root Agent creates bounded children through a frozen `DelegationContract`; children return typed `SubagentResult` values and cannot publish the final user answer.

Supervisor/worker is first because final-answer ownership, approval routing, budget reservation, result validation, and conflict resolution remain in one durable authority. It differs from:

- Plan DAG nodes, which share one Agent identity/context; a child owns an independent identity, contract, context, plan, lifecycle, budget, and result.
- Handoff, because ownership remains with the root.
- Group chat, because children share no transcript/scratchpad and do not freely message peers.
- Swarm, because fan-out, depth, tools, time, tokens, calls, cost, and concurrency are bounded.
- Remote Agent/A2A, because the first executor is local; future transports must preserve all Astra invariants.
- MCP, which exposes tools/resources rather than Agent lifecycle, identity, budget, join, and completion semantics.

## Execution walkthrough

1. The adaptive gate scores complexity, independence, context pressure, expected benefit, write-conflict risk, execution risk, and remaining budget.
2. `DelegationContractService` validates/deduplicates the request, attenuates authority, freezes Tool/Skill catalogs, creates identity/delegation, reserves hierarchical budget, and persists the child atomically.
3. `SubagentContextComposer` builds an artifact-first manifest. Full parent history, hidden reasoning, sibling scratchpads, secrets, unrelated traces, and unselected memory are excluded.
4. `AgentCoordinator` claims with state-version, fencing-token, and cancellation-epoch checks. Run/deployment/provider/tool/capability semaphores bound total concurrency.
5. `LocalAstraAgentExecutor` runs an independent child plan and loop. Authorization receives actual identity, delegation chain, frozen catalog/effect plan, DataFlow state, Workspace scope, and budget.
6. A child may wait for parent input, approval, or a resource without blocking unrelated root/sibling work. Continuations and approvals are version/token bound.
7. Results are schema/provenance/Artifact/Evidence validated. Durable required, optional, and first-success joins drive fan-in; only verified facts/artifacts are promoted.
8. The root Completion Gate requires descendant terminal state and required joins before top-level success.

## Permission and data boundaries

Effective authority is the intersection of parent authority, Task/Run policy, explicit delegated scope, and server policy. Children cannot amplify Tool, Skill, credential, network, Workspace, data-label, purpose, or destination scope. Credentials use child-specific short-lived grants; parent secrets are never copied.

Artifacts/evidence begin in private staging. Shared/public promotion is explicit, provenance checked, and parent controlled. Approval records bind identity, Agent execution, delegation, frozen input/effect hashes, catalog digest, continuation token, and exact grant scope. Agent/delegation-chain identities cannot self-approve.

## `AgentExecutor` adapter invariants

Every local, SDK, process, container, or A2A adapter must:

1. accept only a frozen contract, context manifest, runtime handles, and compatible checkpoint;
2. preserve Task/Run/AgentExecution/identity/delegation/causation lineage on plans, turns, model/tool calls, approvals, artifacts, evidence, usage, and events;
3. use Astra authorization and frozen catalogs without side-channel tool/Skill activation;
4. enforce hierarchical budgets, cancellation epochs, fencing, Workspace/DataFlow scope, and resource leases;
5. checkpoint only child-local resumable state and never expose hidden reasoning/secrets;
6. treat unknown non-idempotent effects as `result_unknown`, never replayable work;
7. return typed results and pass provenance, Artifact/Evidence, join, and Completion Gate validation;
8. emit sanitized events and settle reservations exactly once;
9. fail closed on contract, context schema, code/Profile/Skill/Tool Catalog, or checkpoint incompatibility;
10. make transport failure unable to bypass cancellation, audit, approval, or completion semantics.

An adapter missing any invariant is incompatible even if its protocol calls the worker an “agent.”

## Configuration and rollout

Settings are disabled/conservative by default and frozen in each Run policy snapshot.

| Setting family | Purpose |
| --- | --- |
| `agent_subagent_execution_enabled`, `agent_subagent_kill_switch` | Global enable and emergency stop |
| `agent_subagent_rollout_cohort` | Shadow, administrator canary, trusted read-only, or later cohort |
| child/parent/depth/parallel settings | Fan-out and recursion bounds |
| parent token/model/tool/cost reserves | Capacity retained for root completion |
| child token/call/time/cost settings | Per-contract hard envelope |
| provider/tool/capability concurrency | Global backpressure |

Rollout order is shadow decisions without execution, administrator-only canary, trusted read-only, then staged general availability. Promotion requires deterministic protocol tests, behavior evals, and paired single-/multi-Agent quality, p50/p95 latency, token, cost, failure, recovery, cancellation, and safety gates. Safety failures or a doubled failure threshold activate the kill switch. Rollback returns to shadow and drains/fences children.

## Operations and troubleshooting

### Fan-out or cost spike

Activate the kill switch, inspect `/api/runs/{run_id}/subagents/metrics`, verify cohort/policy snapshots, and reduce child/depth/concurrency/cost limits. Immutable completed effects remain reported.

### Stuck child or stale heartbeat

Inspect Agent tree, phase, wait reason, leases, approval, heartbeat, and fencing. Recovery requeues only compatible safe checkpoints. Stale workers cannot heartbeat, checkpoint, complete, or claim after fencing changes.

### Provider saturation

Inspect provider semaphore usage, queue time, dynamic child-node allowance, and rate-limit events. Reduce parallelism before raising provider capacity; never bypass backpressure.

### Approval deadlock

Verify identity/delegation/catalog/input/effect/token bindings and that the reviewer is outside the execution chain. Child approval waits do not stop the Run. Reject stale requests and create a new frozen request instead of mutating one.

### Cancellation lag

Cancellation first increments durable epochs/fencing, then cancels workers and terminates sandboxes. Reconcile in-flight non-idempotent effects before any retry.

### Recovery failure or version drift

Do not force-resume. Preserve joins/reservations/events, inspect incompatibility, and re-delegate from a new contract only after effect reconciliation.

### Kill-switch drain

Stop new delegation, increment Run/child epochs, cancel descendants, terminate interruptible sandboxes, settle unused reservations, retain immutable-effect reports, and wait for descendant terminal barriers.

## UI, events, and privacy

Run/history APIs retain root-only compatibility and add a sanitized nested Agent tree. Main chat shows running/waiting/completed counts, budget, and wait reason. The trusted graph uses an outer Agent tree and per-Agent Plan DAG. Detail views expose permissions, capabilities, usage, tools, artifacts, result/error lineage, and cancellation impact—never hidden reasoning, raw sensitive inputs, secrets, or private scratchpads.

SSE carries Run/per-Agent ordering metadata. Clients suppress stale events, detect gaps, and reconcile from authoritative snapshots. High-frequency progress may be coalesced; terminal, approval, waiting, critical-error, and Artifact events remain immediate.
