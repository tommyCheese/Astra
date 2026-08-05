# Astra Governed Subagent Runtime

## Scope and first supported pattern

Astra implements a governed supervisor/worker runtime. A root Agent creates bounded children through a frozen `DelegationContract`; children return typed `SubagentResult` values and cannot publish the final user answer.

Supervisor/worker is first because final-answer ownership, approval routing, budget reservation, result validation, and conflict resolution remain in one durable authority. It differs from:

- Plan DAG nodes, which share one Agent identity/context; a child owns an independent identity, contract, context, plan, lifecycle, budget, and result.
- Handoff, because ownership remains with the root.
- Group chat, because children share no transcript/scratchpad and do not freely message peers.
- Unbounded peer swarms, because Astra's built-in `swarm` is a supervised control-plane tool: fan-out, depth, tools, time, tokens, calls, cost, and concurrency are frozen and bounded.
- Remote Agent/A2A, because the first executor is local; future transports must preserve all Astra invariants.
- MCP, which exposes tools/resources rather than Agent lifecycle, identity, budget, join, and completion semantics.

## Execution walkthrough

1. The adaptive gate scores complexity, independence, context pressure, expected benefit, write-conflict risk, execution risk, and remaining budget.
2. The root Agent may call the always-loaded `swarm` built-in (`astra.builtin`, backend `astra.runtime`). The whole fan-out group, child identities/delegations, budget reservations, execution handles, and immutable Join are committed atomically; the ToolCall then returns handles while work continues in the background.
3. `DelegationContractService` validates/deduplicates each request, attenuates authority, and freezes Tool/Skill catalogs.
4. `SubagentContextComposer` builds an artifact-first manifest. Full parent history, hidden reasoning, sibling scratchpads, secrets, unrelated traces, and unselected memory are excluded.
5. The Run-scoped `SubagentSupervisor` uses `AgentCoordinator` to claim durable queued children with state-version, fencing-token, and cancellation-epoch checks. Run/deployment/provider/tool/capability semaphores bound total concurrency.
6. `LocalAstraAgentExecutor` runs an independent child plan and loop in an independent database Session and ModelClient wrapper. Authorization receives actual identity, delegation chain, frozen catalog/effect plan, DataFlow state, Workspace scope, and budget.
7. A child may wait for parent input, approval, or a resource without blocking unrelated root/sibling work. Continuations and approvals are version/token bound.
8. Before every trusted root decision, changed Joins are reconciled. Results are schema/provenance/Artifact/Evidence validated, CAS-claimed for merge, and emitted as sanitized observations without child hidden reasoning.
9. The root Completion Gate requires mandatory descendants terminal and required/first-success Joins consumed before top-level success.

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

Settings are conservative by default and frozen in each Run policy snapshot.

| Setting family | Purpose |
| --- | --- |
| dynamic `swarm` Tool state | Persisted user-facing product enablement switch |
| `agent_subagent_kill_switch` | Server-side emergency stop |
| `agent_subagent_rollout_cohort` | Shadow, administrator canary, trusted read-only, or later cohort |
| child/parent/depth/parallel settings | Fan-out and recursion bounds |
| parent token/model/tool/cost reserves | Capacity retained for root completion |
| child token/call/time/cost settings | Per-contract hard envelope |
| provider/tool/capability concurrency | Global backpressure |

Rollout order is shadow decisions without execution, administrator-only canary, trusted read-only, then staged general availability. Promotion requires deterministic protocol tests, behavior evals, and paired single-/multi-Agent quality, p50/p95 latency, token, cost, failure, recovery, cancellation, and safety gates. Safety failures or a doubled failure threshold activate the kill switch. Rollback returns to shadow and drains/fences children.

The trusted read-only production slice is the default rollout cohort. Operators may still override its safety envelope; for example:

```text
AGENT_SUBAGENT_KILL_SWITCH=false
AGENT_SUBAGENT_ROLLOUT_COHORT=trusted_read_only
AGENT_SUBAGENT_MAX_DEPTH=1
AGENT_SUBAGENT_MAX_PARALLEL_CHILDREN=2
```

Existing Runs keep their frozen policy snapshot. Restart the backend after changing deployment settings. `max_children_total` and `max_children_per_parent` cap cumulative creation; `max_parallel_children` caps simultaneous siblings; `max_depth=1` prevents children from creating grandchildren. The `swarm` manifest is excluded from child catalogs.

Users enable or disable **Swarm / 子 Agent** under Settings → Tools. This is the only product enablement switch; there is no separate deployment execution toggle. The UI switch cannot override the rollout cohort or emergency kill switch. Turning it off immediately makes `/subagent` unavailable, removes `swarm` from subsequent root decision contexts, and makes the Supervisor reject new fan-out from an already-running Run. Existing children are not implicitly cancelled.

## Single-Agent versus concurrent-subagent benchmark

Use the paired benchmark for release decisions instead of comparing unrelated historical Runs. It runs the same breadth-research and independent-review prompts in two controlled configurations:

- `single_agent`: trusted mode with Swarm disabled and `subagent_mode=auto`;
- `concurrent_subagent`: trusted mode with Swarm enabled and `subagent_mode=required`.

The order reverses on alternating repetitions. The report includes p50/p95 completion latency, model calls, total tokens, estimated cost, deterministic heading coverage, quality-pass rate, failure rate, and actual child count. A concurrent sample with fewer than two children and a single-Agent sample with any child are counted as failures. Provider usage must be complete by default.

Run this only against an isolated benchmark deployment because it temporarily changes the persisted global Swarm tool switch. The original switch value is restored in a `finally` block. Supply the actual model prices at collection time:

```bash
cd backend
python -m benchmarks.subagent_performance \
  --runs-per-case 3 \
  --input-cost-per-million 3.00 \
  --cached-input-cost-per-million 0.30 \
  --output-cost-per-million 15.00
```

The prices above are examples, not Astra defaults. Use the provider's current prices and record the provider, model, configuration, timestamp, and emitted JSON with the release evidence. Cost is estimated as uncached input, cached input, and output tokens multiplied by those supplied rates. Do not use incomplete-usage samples for a release comparison.

The deterministic benchmark contract is tested with:

```bash
cd backend
python -m pytest -q tests/test_subagent_performance_benchmark.py
python -m ruff check benchmarks/subagent_performance.py tests/test_subagent_performance_benchmark.py
```

## `/subagent` command

`/subagent <task>` is a Run-creation command, not a host-side context mutation. The UI removes the command prefix, preserves the current answer mode, freezes `subagent_mode=required`, and preserves the original draft if validation or Run creation fails. In quick mode it creates a standard Run without a canonical Plan or DAG. In trusted mode it creates a trusted Run with automatic Plan execution. A required-subagent Run in either mode cannot complete until it has created at least one governed Swarm group. The command remains visible but unavailable when Swarm is disabled, killed, shadow-only, or outside an executable rollout cohort.

## Lightweight quick Subagents

Eligible standard Runs can expose the same `swarm` built-in directly inside the quick Agent Loop. Quick Runs remain planless: they do not create a `TaskContract`, canonical Plan, trusted `AgentState`, or execution-graph placeholder. `subagent_mode=auto` keeps delegation opportunistic, while the explicit `/subagent` command makes at least one governed group mandatory.

Quick and trusted Runs share `SubagentSupervisor`, durable Agent executions, attenuated read-only catalogs, hierarchical budgets, Join reconciliation, cancellation, recovery, and sanitized result consumption. Quick mode applies a smaller depth-one budget envelope and basic final verification; trusted mode additionally uses its versioned Plan DAG, node evaluation, evidence requirements, and full Completion Gate. Do not add a quick-only child executor or Join implementation.

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

### Operational rollout drill

Before promotion, run the deterministic operational drill:

```bash
cd backend
python -m pytest -q tests/test_subagent_executor.py -k rollout_drill
```

The drill proves the complete rollback path: shadow records a would-delegate decision without creating a child; the trusted read-only canary creates a child with attenuated `network_read` authority; the kill switch rejects new delegation; drain fences and cancels the existing child; and an already-completed non-idempotent effect remains in both the cancellation report and the durable child error record. Treat any missing shadow event, newly accepted post-kill child, non-terminal drained child, or lost immutable-effect record as a failed rollout gate.

## UI, events, and privacy

Run/history APIs retain root-only compatibility and add a sanitized nested Agent tree. Main chat shows running/waiting/completed counts, budget, and wait reason. The trusted graph uses an outer Agent tree and per-Agent Plan DAG. Detail views expose permissions, capabilities, usage, tools, artifacts, result/error lineage, and cancellation impact—never hidden reasoning, raw sensitive inputs, secrets, or private scratchpads.

SSE carries Run/per-Agent ordering metadata. Clients suppress stale events, detect gaps, and reconcile from authoritative snapshots. High-frequency progress may be coalesced; terminal, approval, waiting, critical-error, and Artifact events remain immediate.
