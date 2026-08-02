## 1. Concurrent Protocol and Persistence

- [ ] 1.1 Add typed `delegate_tasks` decision payloads, bounded fan-out group identity, immutable Join specification, and validation reason codes
- [ ] 1.2 Add persisted fan-out group/idempotency metadata and CAS-protected Join merge/consumption state with a database migration
- [ ] 1.3 Keep existing child and Join rows readable and define migration defaults for unconsumed historical joins
- [ ] 1.4 Add repository operations for idempotent group lookup, atomic Join state transitions, and ready/merging/consumed reconciliation queries
- [ ] 1.5 Add protocol tests for malformed groups, group size bounds, immutable retry semantics, and backward-compatible deserialization

## 2. Atomic Bounded Fan-out

- [ ] 2.1 Replace the fixed active-child rejection with a frozen-policy `max_parallel_children` check and structured quota diagnostics
- [ ] 2.2 Refactor child contract creation and budget reservation to support caller-owned transactions without intermediate commits
- [ ] 2.3 Implement fan-out preflight across every request for policy, depth, identity, dedupe, overlap, catalog, quota, budget, deadline, and adaptive-benefit validation
- [ ] 2.4 Implement all-or-nothing creation of group, child identities, delegations, AgentExecutions, reservations, contexts, and immutable Join
- [ ] 2.5 Map budget and concurrent state conflicts to stable delegation rejection results without leaking internal exceptions
- [ ] 2.6 Add concurrency tests proving two allowed children can be active and a child beyond the frozen parallel/cumulative limits is rejected
- [ ] 2.7 Add atomicity and retry tests proving failed or repeated groups never leave partial children, joins, identities, or double reservations

## 3. Isolated Child Runtime Factory

- [ ] 3.1 Add a child runtime factory that creates an independent database Session and service graph per AgentExecution
- [ ] 3.2 Create a per-child ModelClient wrapper and usage recorder while reusing only the shared HTTP transport and immutable Tool Registry
- [ ] 3.3 Bind child Artifact, Evidence, Workspace, Sandbox, permission, continuation, and budget services to the child Session and lineage
- [ ] 3.4 Add simultaneous model/tool execution tests proving usage, turns, events, artifacts, checkpoints, and failures remain attributed to the correct child
- [ ] 3.5 Add contention tests ensuring no database transaction is held across child model or tool waits

## 4. Run-scoped SubagentSupervisor

- [ ] 4.1 Introduce a `SubagentSupervisor` boundary that composes fan-out operations, AgentCoordinator, worker factory, Join reconciler, and structured shutdown
- [ ] 4.2 Integrate the Supervisor lifecycle into trusted `RunEngine` execution without making in-process tasks the authoritative child state
- [ ] 4.3 Dispatch queued children with frozen Run/deployment/provider/tool/capability concurrency limits and dynamic node allowances
- [ ] 4.4 Wake or poll the Supervisor when fan-out commits while preserving durable queue behavior across process restarts
- [ ] 4.5 Integrate whole-Run and individual-child cancellation, kill-switch drain/fence behavior, and worker shutdown
- [ ] 4.6 Integrate stale-child recovery before dispatch and reconcile safe checkpoints, committed results, unknown effects, and incompatible versions
- [ ] 4.7 Add lifecycle tests for concurrent completion, cancellation, kill switch, stale heartbeat, restart, and structured shutdown

## 5. Root Agent Delegation Decisions

- [ ] 5.1 Extend Agent decision parsing and model prompts with first-class `delegate_tasks` for eligible trusted root Agents only
- [ ] 5.2 Add frozen subagent policy, remaining quota/budget, active group scopes, and eligible capability summaries to trusted root decision context
- [ ] 5.3 Bind the root AgentExecution to the durable main Agent identity before delegation
- [ ] 5.4 Handle `delegate_tasks` in AgentLoop by submitting the atomic group to the Supervisor and recording a typed delegated observation
- [ ] 5.5 Reject delegation from standard Runs, child executions, ineligible cohorts, disabled policy, kill switch, and depth-one children
- [ ] 5.6 Add behavior tests for beneficial independent fan-out, simple/sequential rejection, duplicate/overlap avoidance, and no ordinary-Tool bypass

## 6. Join Reconciliation and Exactly-once Merge

- [ ] 6.1 Reconcile changed Joins before each trusted root decision and validate successful child results against schema, completion, provenance, Artifact, Evidence, and lineage
- [ ] 6.2 CAS a ready Join into merging, merge validated facts/claims/artifacts/evidence/conflicts/warnings, and mark it consumed with parent state version
- [ ] 6.3 Make verified fact and Artifact promotion idempotent across retries and recovery
- [ ] 6.4 Emit one sanitized parent Observation per consumed Join without child hidden reasoning, transcripts, secrets, or scratchpads
- [ ] 6.5 Implement required, optional, and first-success failure behavior including safe loser cancellation and unsafe loser reporting
- [ ] 6.6 Add crash-point tests before merge, during promotion, and before consumed commit to prove exactly-once parent-visible results
- [ ] 6.7 Add multi-child conflict, duplicate claim, missing evidence, invalid output, and partial failure tests

## 7. Plan Scheduling and Completion Barriers

- [ ] 7.1 Make `consumer_plan_node_id` a scheduler dependency and keep waiting Join consumers unready while unrelated nodes remain selectable
- [ ] 7.2 Release a consumer node only after its Join is consumed and surface blocked required joins through controlled replan/block paths
- [ ] 7.3 Extend root CompletionGate to require mandatory descendants terminal, required/first-success Joins consumed, child budgets settled, approvals resolved, and blocking conflicts handled
- [ ] 7.4 Ensure optional child failures produce policy-governed warnings without blocking unrelated mandatory completion
- [ ] 7.5 Add scheduler tests for barrier-free root progress, required/optional/first-success branches, and no premature node completion
- [ ] 7.6 Add end-to-end tests proving the root cannot finalize while required children or merge consumption remain outstanding

## 8. Rollout, API, UI, and Observability

- [ ] 8.1 Enforce disabled, shadow, allowlisted trusted-read-only, and later rollout eligibility against frozen Run metadata
- [ ] 8.2 Extend Run events and sanitized projections with fan-out group, Join merge/consumption, concurrent overlap, queue, and per-child attribution metadata
- [ ] 8.3 Update the Subagent panel and trusted execution graph to show simultaneous branches, Join waiting/merging/consumed state, per-child budget, and independent cancellation
- [ ] 8.4 Preserve per-Run/per-Agent cursor ordering and authoritative snapshot reconciliation under overlapping child events
- [ ] 8.5 Extend telemetry with attempted/accepted fan-out width, achieved overlap, queue latency, merge retries, attribution failures, and quota rejections
- [ ] 8.6 Add frontend tests for two running children, mixed waiting/completed siblings, Join merging, cancellation, responsive layout, and accessibility

## 9. Verification and Release Readiness

- [ ] 9.1 Run backend unit, integration, migration, protocol, concurrency, cancellation, recovery, and permission suites
- [ ] 9.2 Run frontend unit tests, type checking, production build, and event-gap reconciliation tests
- [ ] 9.3 Add paired single-Agent versus concurrent-subagent benchmarks for breadth research, independent review, latency, tokens, cost, quality, and failure rate
- [ ] 9.4 Validate SQLite two-worker contention behavior and document or enforce the supported production database boundary
- [ ] 9.5 Exercise shadow, trusted-read-only canary, kill-switch rollback, drain, and immutable-effect operational drills
- [ ] 9.6 Update governed-subagent documentation to replace single-active semantics with bounded concurrency and describe the production supervision loop
- [ ] 9.7 Run strict OpenSpec validation and record release-gate evidence before enabling execution for new trusted Runs
