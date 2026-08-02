## 1. Protocols and Configuration

- [x] 1.1 Add typed `DelegationRequest`, frozen `DelegationContract`, context item/manifest, budget envelope, join policy, child status/phase, and `SubagentResult` schemas
- [x] 1.2 Add stable validation and reason codes for incomplete scope, missing success criteria, invalid output schema, disallowed join policy, excessive depth, duplicate request, budget rejection, and non-beneficial delegation
- [x] 1.3 Add subagent feature flags, kill switch, rollout cohort, max children/depth/parallelism, parent reserve, round-trip, wall-time, token, call, and cost settings with conservative defaults
- [x] 1.4 Extend reasoning-policy compilation and Run snapshots with immutable effective subagent policy and model-routing upper bounds
- [x] 1.5 Add serialization/backward-compatibility tests for all new public and persisted schemas

## 2. Persistence and Migration

- [x] 2.1 Add `AgentExecutionRecord` with root/parent lineage, identity/delegation references, contract, context/catalog/budget snapshots, lifecycle state, checkpoint, result, heartbeat, fencing version, and timestamps
- [x] 2.2 Add agent-execution foreign keys or lineage fields to Plan revision, Plan node, NodeExecution, Turn, ToolCall, Approval, Artifact/Evidence, usage, and relevant event records
- [x] 2.3 Add uniqueness and indexes for one root execution per Run, parent request idempotency, active/heartbeat recovery scans, lineage traversal, and event projection
- [x] 2.4 Create Alembic migration that backfills existing Runs with root execution compatibility without rewriting historical result JSON
- [x] 2.5 Add `AgentExecutionRepository` CRUD, compare-and-swap transition, claim/heartbeat, fencing, checkpoint, descendant traversal, terminal barrier, and idempotent child creation methods
- [x] 2.6 Extend Run/conversation deletion and retention flows to clean up or age all Agent execution lineage in safe dependency order
- [x] 2.7 Add repository and migration tests for root-only compatibility, concurrent creation, stale workers, lifecycle constraints, lineage queries, retention, and rollback-safe reads

## 3. Delegation Authorization and Scope Attenuation

- [x] 3.1 Implement `DelegationContractService` to normalize, freeze, hash, deduplicate, validate scope overlap, and authorize `delegation_create`
- [x] 3.2 Extend identity/delegation creation to compute effective child scope as the intersection of parent authority, Task/Run policy, explicit scope, and server subagent policy
- [x] 3.3 Implement per-child Tool Catalog attenuation and immutable digest validation across resume
- [x] 3.4 Implement per-child Skill Catalog/revision attenuation and reject activation outside the delegated subset
- [x] 3.5 Implement child-specific Credential Grant issuance/revocation with short TTL and prohibit inheritance of parent secrets
- [x] 3.6 Extend Workspace and DataFlow scope computation for child read/write roots, data labels, allowed purposes, destinations, and private staging namespaces
- [x] 3.7 Ensure every child tool invocation supplies actual identity, agent execution, delegation chain, budget, DataFlow state, Workspace scope, and frozen effect plan to `authorize_invocation()`
- [x] 3.8 Reject self-approval, reviewer/executor identity overlap, privilege amplification, cross-Task delegation, over-depth recursion, and adapters that drop child execution context
- [x] 3.9 Add adversarial permission tests covering Tool/Skill/Credential/Network/Data/Workspace attenuation, nested delegation, approval scope, revocation, catalog drift, and protected resources

## 4. Context Isolation and Artifact-First Exchange

- [x] 4.1 Implement `SubagentContextComposer` with explicit facts, task contract, Artifact/Evidence refs, applicable Profile layers, child role protocol, attenuated catalogs, budget, and termination instructions
- [x] 4.2 Add context-item provenance, hash, data label, purpose, token estimate, size threshold, and permission checks
- [x] 4.3 Exclude full parent history, hidden reasoning, sibling scratchpads, unselected Memory, secrets, and unrelated tool traces by default
- [x] 4.4 Add child-local context compression/checkpointing without promoting scratchpad or local facts into shared Run state
- [x] 4.5 Add parent-to-child structured continuation answers with bounded round trips and versioned continuation tokens
- [x] 4.6 Add child Artifact/Evidence creation in private staging and result references without copying large payloads through model messages
- [x] 4.7 Implement explicit parent promotion/merge of verified child facts and Artifacts into shared Run state and public Task Workspace paths
- [x] 4.8 Add isolation, token-bound, data-purpose, path-boundary, large-artifact, context-compaction, and sibling-contamination tests

## 5. Local Child Agent Execution

- [x] 5.1 Define the internal `AgentExecutor` adapter contract over DelegationContract, ContextManifest, runtime handles, events, checkpoint, and SubagentResult
- [x] 5.2 Implement `LocalAstraAgentExecutor` by composing the existing Agent loop under an AgentExecution namespace instead of forking the parent mutable state
- [x] 5.3 Give each child an independent task contract, plan revisions, node executions, model context, reflection state, Completion Gate, and usage attribution
- [x] 5.4 Implement Runtime operations for `delegate_task`, `inspect_delegation`, `collect_delegation_results`, structured parent response, and `cancel_delegation`
- [x] 5.5 Enforce single-child, read-only, depth-one execution behind a disabled-by-default feature flag as the first functional slice
- [x] 5.6 Add deterministic mock-model tests for child planning, multi-turn tools, local completion, waiting_parent, blocked, failed, warning, schema-invalid, and artifact-producing outcomes
- [x] 5.7 Add end-to-end API test proving an existing root-only Run remains unchanged when delegation is disabled or rejected

## 6. Hierarchical Budgeting and Scheduling

- [x] 6.1 Implement atomic hierarchical reservation and settlement for tokens, model calls, tool calls, wall time, cost, children, depth, and concurrency while preserving the parent reserve
- [x] 6.2 Add Run- and deployment-level semaphores for Agent executions, model providers, tools, and capabilities so child node parallelism cannot multiply beyond global limits
- [x] 6.3 Implement `AgentCoordinator` claim, heartbeat, fencing, queue, backpressure, and dynamic per-child node allowance using independent database sessions
- [x] 6.4 Reuse canonical resource claims and leases across root and child nodes, with unknown/non-idempotent effects remaining exclusive
- [x] 6.5 Implement adaptive delegation gate inputs for complexity, independence, context pressure, write conflicts, estimated benefit, risk, and remaining budget
- [x] 6.6 Add sibling overlap/deduplication plus explicit independent-review relationships
- [x] 6.7 Add deterministic concurrency tests for atomic budget races, slot multiplication, provider caps, resource conflicts, fair progress, queueing, and measurable execution overlap
- [x] 6.8 Add load tests for configured maximum children/nodes and verify database pool, SSE buffer, memory, provider rate, and cancellation behavior stay bounded

## 7. Fan-in, Result Merge, and Completion

- [x] 7.1 Implement child `SubagentResult` normalization, output-schema validation, Artifact/Evidence existence, provenance, usage, and child Completion Gate checks
- [x] 7.2 Implement required, optional, and first-success join sets with dependency-scoped waiting, loser cancellation safety, and durable join state
- [x] 7.3 Implement parent Result Merger for verified fact promotion, sibling result deduplication, conflict sets, open issues, warnings, and Artifact promotion
- [x] 7.4 Extend root Completion Gate with descendant terminal barrier, required-join validation, child result lineage, conflict checks, and no-premature-success enforcement
- [x] 7.5 Implement failure handling for required/optional children, safe retry with preserved attempts, re-delegation, exhausted strategies, and unrelated sibling continuation
- [x] 7.6 Add fan-in tests for partial success, optional failure, first-success race, conflicting claims, invalid child evidence, missing Artifacts, retries, and top-level terminal semantics

## 8. Cancellation, Approval, and Recovery

- [x] 8.1 Add Run and per-child cancellation epochs, descendant propagation, new-claim fencing, cooperative cancellation, sandbox termination, and immutable-effect reporting
- [x] 8.2 Bind child approval requests to identity, agent execution, frozen input/effect hash, catalog digest, continuation token, and exact grant scope
- [x] 8.3 Allow unrelated root/sibling work to continue while one child waits for approval, resource, or parent input
- [x] 8.4 Extend recovery scanning to Agent executions and restore compatible checkpoints, committed results, joins, budget reservations, and event cursors
- [x] 8.5 Handle result-unknown non-idempotent calls, stale workers, duplicate completion, code/Profile/Skill/Catalog version drift, and incompatible checkpoints fail closed
- [x] 8.6 Add cancellation/approval/recovery tests for parent-child races, restart at each lifecycle phase, stale fencing tokens, unknown effects, exact-once budget settlement, and no duplicate child/tool creation

## 9. Events, APIs, and Frontend

- [x] 9.1 Extend Run events and snapshots with agent execution lineage, parent, node, Run/Agent sequences, causation, status/phase, join, wait reason, budget summary, and sanitized result metadata
- [x] 9.2 Add event batching/backpressure that coalesces high-frequency progress while immediately emitting terminal, approval, waiting-user, critical-error, and Artifact events
- [x] 9.3 Extend Run/history/API result schemas and frontend types with backward-compatible root execution and nested child projections
- [x] 9.4 Update `processStream.ts` to reduce concurrent Agent events monotonically, detect local/global cursor gaps, replay safely, and correct from authoritative snapshots
- [x] 9.5 Add a compact main-chat subagent summary with running/waiting/completed counts, budget, key wait reason, and reduced-motion/accessibility support
- [x] 9.6 Extend the trusted execution graph with a collapsible outer Agent tree and per-Agent inner Plan DAG, preserving responsive pane/window behavior
- [x] 9.7 Add child detail and control UI for delegation summary, creation reason, required/optional status, permissions/capabilities, usage, tools, Artifacts, result, error, lineage, and cancellation impact
- [x] 9.8 Add i18n, keyboard, screen-reader, high-contrast, sensitive-data redaction, historical replay, empty/error, and large-tree UI coverage
- [x] 9.9 Add frontend unit/integration/browser tests for concurrent events, reconnect gaps, stale-event suppression, expand/collapse, cancellation, approval attribution, and no hidden-reasoning leakage

## 10. Metrics, Evaluation, and Rollout

- [x] 10.1 Add usage and telemetry for delegation decisions/reasons, fan-out/depth, overlap, duplicate work, child outcomes, merge failures, tokens/cost, latency, cancellation, recovery, and permission denial
- [x] 10.2 Add privacy-safe cohort snapshots and baseline comparison without logging conversation content, hidden reasoning, secrets, or raw sensitive tool inputs
- [x] 10.3 Build deterministic protocol suites for contracts, identity, scope attenuation, budgets, lifecycle, joins, completion, events, cancellation, and recovery
- [x] 10.4 Build delegation behavior evals for should-delegate decisions, decomposition coverage/overlap, tool matching, schema adherence, conflict handling, bounded parent round trips, and stopping
- [x] 10.5 Build paired end-to-end benchmarks comparing identical model/policy single-Agent and multi-Agent runs on breadth research, multi-source comparison, independent file review, and alternative analysis tasks
- [x] 10.6 Add negative benchmarks for simple questions, strong sequential workflows, shared-write-heavy coding, low budgets, and high-risk effects
- [x] 10.7 Define release gates for quality, p50/p95 latency, token/cost, failure, recovery, cancellation, and safety metrics plus automatic alert and kill-switch thresholds
- [x] 10.8 Run shadow delegation decisions before execution, then administrator-only canary, trusted read-only cohort, and staged general rollout with documented rollback

## 11. Documentation and Future Compatibility

- [x] 11.1 Update the system detailed design, execution walkthrough, permission model, operations guide, configuration reference, and UI help with root/child semantics and troubleshooting
- [x] 11.2 Document why supervisor/worker is the first supported pattern and distinguish it from DAG nodes, handoff, group chat, swarm, remote agents, MCP, and A2A
- [x] 11.3 Document `AgentExecutor` adapter invariants so future SDK or A2A adapters cannot bypass Astra authorization, budget, Workspace, Evidence, result validation, events, or audit
- [x] 11.4 Add operational runbooks for fan-out/cost spikes, stuck children, stale heartbeat, provider saturation, approval deadlock, cancellation lag, recovery failure, and kill-switch drain
- [x] 11.5 Run backend test suites, migrations from representative historical databases, frontend checks/build/browser tests, OpenSpec validation, and container smoke tests before enabling any cohort
