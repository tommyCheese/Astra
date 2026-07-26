## 1. Plan Graph Projection Contract

- [x] 1.1 Define versioned backend `PlanGraphSnapshot`, node, edge, version-summary and diff schemas with stable PlanNode identifiers
- [x] 1.2 Extend Plan repository projections with explicit edges, dependency types, lineage, timestamps, expected outcomes, evidence and failure metadata
- [x] 1.3 Return the current trusted graph snapshot and lightweight Plan version summaries from RunView while keeping standard Run graph fields empty
- [x] 1.4 Add Run-scoped APIs to list Plan versions and lazily load a requested immutable version
- [x] 1.5 Add projection compatibility coverage for the existing `depends_on` representation without making it the new graph source of truth
- [x] 1.6 Add backend contract tests for trusted current/history snapshots, standard no-graph responses and stable node execution references

## 2. Version Lineage and Graph Differences

- [x] 2.1 Expose `supersedes_plan_id` and `lineage_node_id` through public Plan projections
- [x] 2.2 Enforce valid lineage when PlanPatch preserves completed nodes and evidence across versions
- [x] 2.3 Implement a deterministic adjacent-version diff service for nodes and edges without title-based matching
- [x] 2.4 Classify added, removed, unchanged, modified and inherited-completed nodes plus added and removed edges
- [x] 2.5 Add tests for branch replacement, preserved evidence, missing lineage degradation and immutable historical versions

## 3. Real-Time Graph Event Protocol

- [x] 3.1 Define public event payload schemas for graph snapshot, version creation/activation, node transition and revision lifecycle events
- [x] 3.2 Extend `plan.node.updated` with old/new status, Plan identity, version and safe execution references
- [x] 3.3 Emit explicit Plan activation and version replacement events with lineage summaries
- [x] 3.4 Ensure graph events are persisted, ordered and replayed through the existing SSE after-id protocol
- [x] 3.5 Sanitize graph event payloads so hidden reasoning, credentials, raw sensitive tool input and host paths cannot enter the stream
- [x] 3.6 Add event-order, replay, stale-version and payload-safety tests

## 4. Version-Bound Plan Revision

- [x] 4.1 Add a typed `plan_revision` continuation request carrying user intent, continuation token, expected Plan identity/version and expected state version
- [x] 4.2 Consume revision tokens once, reject stale requests and keep the original planned version unchanged on validation failure
- [x] 4.3 Generate a complete revised Plan from the user request and current contract, Plan and safe execution context
- [x] 4.4 Run the revised draft through cycle, dependency, reachability, capability, success-criteria and budget validation before persistence
- [x] 4.5 Persist a new planned version with lineage and return the Run to version-bound `plan_confirmation` using a fresh continuation token
- [x] 4.6 Emit revision started/completed/rejected events without executing any PlanNode or granting later tool effects
- [x] 4.7 Add tests for successful revision, invalid DAG, stale token, replay, revision failure recovery and subsequent exact-version confirmation

## 5. Frontend Graph State and Layout Foundation

- [x] 5.1 Add and lock `@xyflow/react` and `@dagrejs/dagre`, including production bundle and license checks
- [x] 5.2 Define complete frontend Plan graph, version, edge, diff and event types aligned with backend schemas
- [x] 5.3 Implement `PlanGraphStreamState` with snapshot replacement, event deduplication, version guards and stale-event rejection
- [x] 5.4 Batch graph deltas to at most one visible state update per animation frame and merge inconsistent-event snapshot refreshes
- [x] 5.5 Implement shared pure selectors for ready state, progress, active path, unmet dependencies, blocked propagation and node Trace associations
- [x] 5.6 Implement a deterministic Dagre layout adapter with stable rank/index/node-key ordering and coordinate reuse on status-only changes
- [x] 5.7 Implement lineage-based client diff projection and historical-version cache
- [x] 5.8 Add reducer, selector, diff, layout stability, reconnect and version-gap unit tests

## 6. Trusted Execution Graph Workbench

- [x] 6.1 Build the shared `TrustedExecutionGraph` shell for planning, confirming, executing, waiting, terminal and historical states
- [x] 6.2 Build accessible Plan node cards and directed dependency edges for pending, ready, running, completed, failed, blocked, skipped and superseded states
- [x] 6.3 Add fit-view, zoom, pan, focus-current-node, reset and full-screen workbench controls without enabling semantic node dragging
- [x] 6.4 Add progress and Plan version headers that distinguish current, planned, active, completed and superseded versions
- [x] 6.5 Build the node inspector for plan intent, dependencies, expected outcome, success criteria, capabilities, risk, optionality and blocking reason
- [x] 6.6 Project selected-node AgentTurns, ToolCalls, Reflections, Evaluations, approvals, Artifacts, evidence and failures into layered Trace and Evidence sections
- [x] 6.7 Preserve the cross-node chronological ProcessTimeline as a secondary expandable run record
- [x] 6.8 Add version selection and visual node/edge difference overlays without letting historical state replace the live current graph

## 7. Chat, Confirmation, and Revision Integration

- [x] 7.1 Replace the trusted waiting Plan ordered list with the shared graph workbench and keep version-bound execute/cancel actions beneath it
- [x] 7.2 Add the natural-language “调整计划” flow with submitting, validation error, stale version and regenerated-version states
- [x] 7.3 Replace trusted terminal PlanNode audit rows with the completed graph and node inspector
- [x] 7.4 Embed live trusted graph updates before the final answer while retaining the planning placeholder until the canonical Plan exists
- [x] 7.5 Keep standard Run process panels and audit details graph-free with no synthetic version, nodes or edges
- [x] 7.6 Ensure tool effect approvals appear at the associated node while remaining semantically independent from Plan confirmation
- [x] 7.7 Update conversation snapshots and history restoration so trusted graphs, selections and version summaries reload deterministically
- [x] 7.8 Add integration tests for confirm, revise, execute, approval, failure, replan, completion, history reload and standard/trusted separation

## 8. Responsive, Accessible, and Visual Quality

- [x] 8.1 Implement compact inline graph sizing and a wider modal workbench for dense Plans without clipping nodes or actions
- [x] 8.2 Add an equivalent structured node list with dependencies, status and version for screen readers and non-canvas interaction
- [x] 8.3 Add keyboard node navigation, focus restoration, inspector labelling and polite live status announcements
- [x] 8.4 Add light, dark and high-contrast graph tokens that do not rely on color alone
- [x] 8.5 Disable continuous graph motion under reduced-motion while preserving a static current-node indication
- [x] 8.6 Validate fan-out/fan-in, long labels, large Plans, mobile widths and browser zoom through visual regression fixtures
- [x] 8.7 Add lazy loading and rendering guards so the graph library does not delay standard chat startup or high-frequency answer streaming

## 9. Documentation and Verification

- [x] 9.1 Update API/OpenAPI documentation for graph snapshots, version history, graph events and Plan revision continuations
- [x] 9.2 Document the distinction between Plan Graph, Runtime Trace and Evidence, including the hidden-chain-of-thought boundary
- [x] 9.3 Remove the obsolete trusted linear Plan confirmation and audit rendering paths after the shared workbench is fully integrated
- [x] 9.4 Run backend Ruff and full pytest, frontend typecheck/tests/production build, migration checks and strict OpenSpec validation
- [ ] 9.5 Complete browser verification for live SSE updates, disconnect recovery, revision diff, full-screen navigation, mobile, dark mode, keyboard and reduced-motion
