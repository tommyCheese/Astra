## 1. Protocol Baseline and Package Boundaries

- [x] 1.1 Record the reviewed AG-UI protocol/profile version and exact npm package versions in a backend/frontend compatibility fixture.
- [x] 1.2 Add exact-version `@ag-ui/core` and `@ag-ui/client` frontend dependencies and verify the existing frontend build remains reproducible.
- [x] 1.3 Create isolated backend AG-UI schema, input-adapter, projector, encoder, capability, and route modules without importing AG-UI concepts into Astra domain packages.
- [x] 1.4 Add architecture rules that allow AG-UI interface modules to depend on application/read-model contracts while forbidding runtime/domain packages from depending on AG-UI or React.
- [x] 1.5 Define golden JSON fixtures for supported `RunAgentInput`, capabilities, lifecycle, message, tool, reasoning, State, Activity, interrupt, and error shapes.

## 2. Durable Protocol Correlation

- [x] 2.1 Define a durable AG-UI protocol Run binding that correlates principal, thread, protocol Run, internal Task, internal Run, parent protocol Run, and lifecycle status.
- [x] 2.2 Define a durable interrupt binding that correlates interrupt ID, protocol Run, internal Run, approval or waiting record, schema/version data, expiry, and consumed outcome.
- [x] 2.3 Add the database migration, ORM records, repositories, uniqueness constraints, and indexes for protocol and interrupt bindings.
- [x] 2.4 Implement atomic create/read/consume operations that make duplicate Run requests and interrupt resolutions idempotent.
- [x] 2.5 Add repository tests for duplicate identifiers, wrong principal/thread, restart recovery, concurrent resolution, stale version, and consumed interrupt behavior.

## 3. Capabilities and Inbound Request Adapter

- [x] 3.1 Implement the versioned AG-UI capability declaration for supported SSE, messages, tools, reasoning summaries, State, Activities, multi-Agent observation, execution controls, and interrupts.
- [x] 3.2 Expose a feature-gated capability endpoint and verify disabled deployments do not advertise AG-UI support.
- [x] 3.3 Implement strict `RunAgentInput` validation, request size bounds, message normalization, and authorized `threadId` resolution without cross-user existence disclosure.
- [x] 3.4 Implement the versioned `forwardedProps.astra` allowlist for answer mode, plan execution, model, Skills, and Subagent mode.
- [x] 3.5 Reject or ignore unknown forwarded properties according to the profile and ensure they never enter model context or runtime configuration.
- [x] 3.6 Enforce that `RunAgentInput.tools` cannot register, authorize, expose, or execute an Astra backend tool.
- [x] 3.7 Map valid new-message inputs to existing Run creation services and valid resume inputs to existing continuation services without duplicating authoritative conversation history.
- [x] 3.8 Add API tests for valid input, malformed messages, oversized input, unauthorized thread, forged tool, unknown property, duplicate protocol Run, and disabled feature behavior.

## 4. Core HTTP/SSE Endpoint and Lifecycle Projection

- [x] 4.1 Implement the feature-gated `POST /api/ag-ui` streaming route with no-cache/no-buffer SSE headers and safe dependency/session lifetime handling.
- [x] 4.2 Implement an AG-UI SSE encoder and parser fixture that preserves event boundaries and rejects invalid public event objects in tests.
- [x] 4.3 Implement deterministic thread, protocol Run, message, tool-call, Activity, and interrupt identifier helpers.
- [x] 4.4 Implement per-stream projection state for source cursors, open message/tool lifecycles, emitted terminal state, public State, and Activity revisions.
- [x] 4.5 Project Astra Run creation and terminal facts into exactly one ordered `RUN_STARTED` and one `RUN_FINISHED` or `RUN_ERROR` outcome.
- [x] 4.6 Project `answer.started`, `answer.delta`, content completion, final correction, failure, and cancellation into a valid text-message lifecycle without duplicate content.
- [x] 4.7 Suppress internal-only events and make duplicate or replayed Astra source events idempotent within the public projection.
- [x] 4.8 Add golden-stream tests for successful answer, empty answer, corrected final content, pre-text failure, cancellation after partial text, duplicate source event, and native/AG-UI visible-result parity.

## 5. Public Sanitization and Bounds

- [x] 5.1 Define explicit public schemas and size limits for errors, reasoning summaries, tool previews/results, State, each Activity type, and custom extension envelopes.
- [x] 5.2 Implement reusable sanitizers for credentials, secrets, continuation tokens, permission internals, private paths, workspace data, exception traces, and unverified artifact links.
- [x] 5.3 Ensure deltas are computed only between sanitized public objects so removed sensitive fields cannot reappear through JSON Patch.
- [x] 5.4 Add bounded truncation metadata and fallback summaries for oversized text, tool output, errors, and Activity content.
- [x] 5.5 Add adversarial tests covering nested secrets, path variants, credential metadata, unsafe URLs, hidden reasoning, oversized structures, and sanitizer failures.

## 6. State, Activity Snapshot, and Delta Engine

- [x] 6.1 Define versioned public shared-State and `astra.plan`, `astra.agent_tree`, `astra.verification`, `astra.artifact`, and `astra.tool_activity` Activity schemas with accessible fallback fields.
- [x] 6.2 Represent mutable entity collections with stable `order` and `byId` structures and implement safe JSON Pointer escaping.
- [x] 6.3 Implement reducers that derive complete sanitized public State and Activity projections from authoritative Run snapshots and committed Astra events.
- [x] 6.4 Emit initial `STATE_SNAPSHOT`, `MESSAGES_SNAPSHOT`, and relevant `ACTIVITY_SNAPSHOT` baselines before dependent deltas on every new stream.
- [x] 6.5 Implement RFC 6902 diff generation with base revision, resulting revision, schema version, and source-event cursor metadata.
- [x] 6.6 Implement snapshot fallback for missing baselines, cursor gaps, schema/plan changes, permission changes, structural changes, diff failures, and patches above the configured size ratio.
- [x] 6.7 Add deterministic tests for first snapshot, compatible single-entity delta, plan revision replacement, missing baseline, cursor gap, unsafe patch path, large patch fallback, and reconnect snapshots.

## 7. Reasoning, Tool, and Error Projection

- [x] 7.1 Project allowed `reasoning.summary` events into a valid AG-UI reasoning-message lifecycle distinct from assistant answer content.
- [x] 7.2 Suppress provider-hidden reasoning and expose only bounded safe availability or truncation metadata where appropriate.
- [x] 7.3 Project a sanitized Astra tool start into `TOOL_CALL_START`, complete JSON `TOOL_CALL_ARGS`, and `TOOL_CALL_END` with stable correlation.
- [x] 7.4 Project tool completion, failure, and allowed public output into exactly one correlated `TOOL_CALL_RESULT` or safe failure representation.
- [x] 7.5 Add ordering and sanitization tests for streamed reasoning, unavailable reasoning, tool success/failure, malformed tool args, duplicate results, and results arriving after terminal state.

## 8. Interrupt, Resume, and Cancellation Compatibility

- [x] 8.1 Project pending tool approvals into tool-bound AG-UI interrupts after required public State and message snapshots.
- [x] 8.2 Generate each approval interrupt response schema from the backend decisions actually available for the frozen action, including omission of unsafe allow-similar behavior.
- [x] 8.3 Project non-tool user questions and confirmations into bounded `input_required` or `confirmation` interrupts with safe fallback messages.
- [x] 8.4 Resolve a new protocol Run's complete resume payload through durable bindings and existing Astra approval/continuation services without exposing server-held continuation tokens.
- [x] 8.5 Emit the resumed tool result against the original tool call and continue the new protocol lifecycle without regenerating the frozen action.
- [x] 8.6 Add an authorized Astra cancellation extension for correlated protocol Runs and advertise it separately from transport abort semantics.
- [x] 8.7 Add tests for approve once, reject, allow similar when safe, free-form input, multiple open interrupts, cancelled response, stale/expired/mismatched/replayed resume, restart while waiting, disconnect without cancellation, and explicit cancellation.

## 9. Astra Structured Activity Projectors

- [x] 9.1 Implement `astra.plan` snapshots and stable-node deltas for plan versions, node status, attempts, evidence references, failures, and active executions.
- [x] 9.2 Implement `astra.agent_tree` snapshots and deltas for safe lineage, aggregate counts, objectives, waits, budgets, terminal summaries, and cancellation availability.
- [x] 9.3 Ensure Agent-tree resynchronization preserves terminal precedence after concurrent Agent event gaps or reconnects.
- [x] 9.4 Implement bounded verification, artifact, and tool-activity projections with authorized URLs and generic fallback text.
- [x] 9.5 Add projector tests for parallel plans, plan revision, concurrent children, stale running events, private sibling context, unverified artifacts, verification warnings, and generic-client fallback fields.

## 10. Frontend Transport and Projection Store

- [x] 10.1 Define a transport-neutral frontend interface for starting a message, receiving projected events/state, resolving interrupts, explicitly cancelling a Run, and closing a transport stream.
- [x] 10.2 Wrap `@ag-ui/client` behind the AG-UI transport and keep the current native transport as a feature-flagged rollback implementation.
- [x] 10.3 Implement normalized stores/reducers for connection state, protocol Run lifecycle, messages, reasoning, tools, Activities, capabilities, and pending interrupts.
- [x] 10.4 Render the first text content and first valid Activity snapshot immediately without waiting for message or Run completion.
- [x] 10.5 Batch subsequent text, reasoning, and compatible Activity updates by animation frame while flushing critical terminal, interrupt, error, and artifact events immediately.
- [x] 10.6 Validate Activity type, schema, base revision, and JSON Patch application before immutable state commit; isolate failed Activities and recover them from replacement snapshots.
- [x] 10.7 Preserve partial text during disconnect and reconcile messages, State, Activities, and terminal status from authoritative recovery snapshots without duplication.
- [x] 10.8 Add reducer tests for first-content latency behavior, frame batching, patch success/failure, revision gap, replacement snapshot, duplicate event, disconnect, cancellation, interrupt, and final convergence.

## 11. React Component Integration

- [x] 11.1 Move stream orchestration and protocol-event routing out of the main chat component into the transport and projection-store boundary without changing established visual behavior.
- [x] 11.2 Add an Activity renderer registry for Astra plan, Agent tree, verification, artifact, and tool-activity view models using existing specialized components where possible.
- [x] 11.3 Add an accessible generic Activity fallback for unknown types or unsupported schema versions.
- [x] 11.4 Add interrupt renderers for tool approval, input-required, confirmation, and safe generic response-schema forms.
- [x] 11.5 Ensure the Composer sends projected user intent, blocks incompatible input while interrupts are open, distinguishes transport close from explicit cancellation, and remains usable after terminal outcomes.
- [x] 11.6 Add browser tests for progressive answer rendering, live plan updates, Subagent updates, approval/resume, unknown Activity, unknown interrupt, narrow viewport, keyboard operation, reconnect, and native-transport rollback.

## 12. Conformance, Performance, Rollout, and Documentation

- [x] 12.1 Add protocol conformance tests that validate every emitted event against the pinned AG-UI schemas and assert lifecycle ordering across all golden streams.
- [x] 12.2 Add fault-injection tests for database restart, projector-cache loss, SSE disconnect, source cursor gap, malformed patch, delayed terminal event, duplicate resume, and concurrent cancellation.
- [x] 12.3 Measure first-content adapter overhead, event sizes, snapshot-to-delta ratios, frame commits, resynchronizations, and dual-stream visible-outcome parity against explicit acceptance thresholds.
- [x] 12.4 Add metrics and structured logs for protocol/profile version, active streams, projection errors, suppressed unsafe events, payload truncation, patch fallback, interrupt outcomes, reconnects, and cancellation.
- [x] 12.5 Document the supported profile, capability semantics, Astra extension schemas, security boundaries, cancellation behavior, recovery guarantees, feature flag, and rollback procedure.
- [x] 12.6 Enable development-only AG-UI rollout, complete security/accessibility/latency review, and record evidence before enabling any production cohort.
- [x] 12.7 Make AG-UI the first-party default only after conformance, parity, recovery, and latency gates pass; retain native transport removal for a separate breaking proposal.
