## 1. Concurrent Protocol and Persistence

- [x] 1.1 Add typed `delegate_tasks` decision payloads, bounded fan-out group identity, immutable Join specification, and validation reason codes
- [x] 1.2 Add persisted fan-out group/idempotency metadata and CAS-protected Join merge/consumption state with a database migration
- [x] 1.3 Keep existing child and Join rows readable and define migration defaults for unconsumed historical joins
- [x] 1.4 Add repository operations for idempotent group lookup, atomic Join state transitions, and ready/merging/consumed reconciliation queries
- [x] 1.5 Add protocol tests for malformed groups, group size bounds, immutable retry semantics, and backward-compatible deserialization

## 2. Atomic Bounded Fan-out

- [x] 2.1 Replace the fixed active-child rejection with a frozen-policy `max_parallel_children` check and structured quota diagnostics
- [x] 2.2 Refactor child contract creation and budget reservation to support caller-owned transactions without intermediate commits
- [x] 2.3 Implement fan-out preflight across every request for policy, depth, identity, dedupe, overlap, catalog, quota, budget, deadline, and adaptive-benefit validation
- [x] 2.4 Implement all-or-nothing creation of group, child identities, delegations, AgentExecutions, reservations, contexts, and immutable Join
- [x] 2.5 Map budget and concurrent state conflicts to stable delegation rejection results without leaking internal exceptions
- [x] 2.6 Add concurrency tests proving two allowed children can be active and a child beyond the frozen parallel/cumulative limits is rejected
- [x] 2.7 Add atomicity and retry tests proving failed or repeated groups never leave partial children, joins, identities, or double reservations

## 3. Isolated Child Runtime Factory

- [x] 3.1 Add a child runtime factory that creates an independent database Session and service graph per AgentExecution
- [x] 3.2 Create a per-child ModelClient wrapper and usage recorder while reusing only the shared HTTP transport and immutable Tool Registry
- [x] 3.3 Bind child Artifact, Evidence, Workspace, Sandbox, permission, continuation, and budget services to the child Session and lineage
- [x] 3.4 Add simultaneous model/tool execution tests proving usage, turns, events, artifacts, checkpoints, and failures remain attributed to the correct child
- [ ] 3.5 Add contention tests ensuring no database transaction is held across child model or tool waits

## 4. Run-scoped SubagentSupervisor

- [x] 4.1 Introduce a `SubagentSupervisor` boundary that composes fan-out operations, AgentCoordinator, worker factory, Join reconciler, and structured shutdown
- [x] 4.2 Integrate the Supervisor lifecycle into trusted `RunEngine` execution without making in-process tasks the authoritative child state
- [x] 4.3 Dispatch queued children with frozen Run/deployment/provider/tool/capability concurrency limits and dynamic node allowances
- [x] 4.4 Wake or poll the Supervisor when fan-out commits while preserving durable queue behavior across process restarts
- [x] 4.5 Integrate whole-Run and individual-child cancellation, kill-switch drain/fence behavior, and worker shutdown
- [x] 4.6 Integrate stale-child recovery before dispatch and reconcile safe checkpoints, committed results, unknown effects, and incompatible versions
- [x] 4.7 Add lifecycle tests for concurrent completion, cancellation, kill switch, stale heartbeat, restart, and structured shutdown

## 5. Swarm Runtime Built-in and Root Integration

- [x] 5.1 Add an always-loadable `swarm` Tool manifest with `astra.builtin`, `delegation_create`, and `astra.runtime` backend outside Sandbox-only application tools
- [x] 5.2 Add frozen subagent policy, remaining quota/budget, active group scopes, and eligible capability summaries to trusted root decision context
- [x] 5.3 Bind the root AgentExecution to the durable main Agent identity before delegation
- [x] 5.4 Dispatch `swarm` calls from AgentLoop to the Supervisor, complete the ToolCall after group acceptance, and record typed handles without waiting for children
- [x] 5.5 Exclude `swarm` from standard Runs, child Catalogs, ineligible cohorts, disabled policy, kill switch, and depth-one children
- [x] 5.6 Add behavior tests for beneficial independent fan-out, simple/sequential rejection, duplicate/overlap avoidance, runtime dispatch, and no plugin/Sandbox bypass
- [x] 5.7 Add persisted `swarm` Tool settings state and enforce its non-escalating enablement across policy compilation, Run creation, slash availability, root catalogs, and runtime dispatch
- [x] 5.8 Make the persisted Swarm switch the sole product enablement control and remove the inaccessible deployment execution toggle from ordinary eligibility

## 6. Join Reconciliation and Exactly-once Merge

- [x] 6.1 Reconcile changed Joins before each trusted root decision and validate successful child results against schema, completion, provenance, Artifact, Evidence, and lineage
- [x] 6.2 CAS a ready Join into merging, merge validated facts/claims/artifacts/evidence/conflicts/warnings, and mark it consumed with parent state version
- [x] 6.3 Make verified fact and Artifact promotion idempotent across retries and recovery
- [x] 6.4 Emit one sanitized parent Observation per consumed Join without child hidden reasoning, transcripts, secrets, or scratchpads
- [x] 6.5 Implement required, optional, and first-success failure behavior including safe loser cancellation and unsafe loser reporting
- [ ] 6.6 Add crash-point tests before merge, during promotion, and before consumed commit to prove exactly-once parent-visible results
- [x] 6.7 Add multi-child conflict, duplicate claim, missing evidence, invalid output, and partial failure tests

## 7. Plan Scheduling and Completion Barriers

- [x] 7.1 Make `consumer_plan_node_id` a scheduler dependency and keep waiting Join consumers unready while unrelated nodes remain selectable
- [x] 7.2 Release a consumer node only after its Join is consumed and surface blocked required joins through controlled replan/block paths
- [x] 7.3 Extend root CompletionGate to require mandatory descendants terminal, required/first-success Joins consumed, child budgets settled, approvals resolved, and blocking conflicts handled
- [x] 7.4 Ensure optional child failures produce policy-governed warnings without blocking unrelated mandatory completion
- [x] 7.5 Add scheduler tests for barrier-free root progress, required/optional/first-success branches, and no premature node completion
- [ ] 7.6 Add end-to-end tests proving the root cannot finalize while required children or merge consumption remain outstanding

## 8. Rollout, API, UI, and Observability

- [x] 8.1 Enforce disabled, shadow, allowlisted trusted-read-only, and later rollout eligibility against frozen Run metadata
- [x] 8.2 Extend Run events and sanitized projections with fan-out group, Join merge/consumption, concurrent overlap, queue, and per-child attribution metadata
- [x] 8.3 Update the Subagent panel and trusted execution graph to show simultaneous branches, Join waiting/merging/consumed state, per-child budget, and independent cancellation
- [x] 8.4 Preserve per-Run/per-Agent cursor ordering and authoritative snapshot reconciliation under overlapping child events
- [x] 8.5 Extend telemetry with attempted/accepted fan-out width, achieved overlap, queue latency, merge retries, attribution failures, and quota rejections
- [x] 8.6 Add frontend tests for two running children, mixed waiting/completed siblings, Join merging, cancellation, responsive layout, and accessibility
- [x] 8.7 Show `swarm` in Tool settings with a keyboard-accessible persisted switch, availability reason, search metadata, and future-Run feedback
- [x] 8.8 Remove non-actionable deployment enablement and existing-child lifecycle notices from the Swarm settings UI
- [x] 8.9 Make successful Tool setting changes silent while retaining loading and failure feedback

## 9. Subagent Slash Command

- [x] 9.1 Extend slash command schemas/catalog with Run-creation commands, availability reasons, and `/subagent <task>` metadata
- [x] 9.2 Add `subagent_mode = required` to Run creation and freeze it into trusted execution policy/profile
- [x] 9.3 Route `/subagent <task>` submission to trusted auto-plan Run creation without persisting the slash text
- [x] 9.4 Require at least one governed Swarm group before a required-subagent Run can complete successfully
- [x] 9.5 Preserve the command arguments on validation or Run-creation failure and keep Skill slash behavior unchanged
- [x] 9.6 Add backend and frontend tests for catalog availability, argument validation, successful Run creation, unavailable policy, draft recovery, keyboard operation, and accessibility

## 10. Verification and Release Readiness

- [x] 10.1 Run backend unit, integration, migration, protocol, concurrency, cancellation, recovery, permission, Swarm, and slash-command suites
- [x] 10.2 Run frontend unit tests, type checking, production build, and event-gap reconciliation tests
- [ ] 10.3 Add paired single-Agent versus concurrent-subagent benchmarks for breadth research, independent review, latency, tokens, cost, quality, and failure rate
- [x] 10.4 Validate SQLite two-worker contention behavior and document or enforce the supported production database boundary
- [ ] 10.5 Exercise shadow, trusted-read-only canary, kill-switch rollback, drain, and immutable-effect operational drills
- [x] 10.6 Update governed-subagent and slash-command documentation for Swarm built-in, bounded concurrency, `/subagent`, and the production supervision loop
- [x] 10.7 Run strict OpenSpec validation and record release-gate evidence before enabling execution for new trusted Runs
