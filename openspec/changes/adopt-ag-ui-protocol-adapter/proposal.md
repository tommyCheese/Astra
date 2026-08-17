## Why

Astra already exposes durable SSE Run events, progressive answers, governed approvals, versioned plan graphs, and Subagent lineage, but its browser contract is application-specific and tightly coupled to the current React shell. Adopting AG-UI as a public interoperability projection now lets Astra keep its richer internal event model while gaining a standard agent-to-UI boundary, a smaller frontend transport surface, and a path to compatible external clients without rewriting the runtime or waiting for a Run to finish before rendering.

## Current Implementation Baseline (2026-08-12)

- The backend interface adapter, strict wire schemas, capability declaration, SSE encoder/projector, durable Run and interrupt bindings, sanitization, Snapshot/Delta support, structured Activities, resume and cancellation routes already exist under `backend/app/interfaces/ag_ui/` and the matching infrastructure packages.
- The isolated frontend transport, projection store, animation-frame batcher, Activity renderers and interrupt renderer already exist under `frontend/src/agui/`, with focused backend and frontend tests passing.
- The product root continues to render the established `AppContent`. `AgUiChatPage` is isolated to the development-only `/__dev/ag-ui` preview and additionally requires `VITE_AG_UI_ENABLED=true`; it exercises the transport/store, Activities, interrupts, explicit cancellation and transport close without replacing the product shell. Visual/feature parity and browser verification remain open.
- The backend AG-UI route and frontend preview are default-off again, matching the staged rollout contract. Production defaulting remains blocked until the existing chat shell consumes the transport-neutral projection without losing product behavior.
- Tool projection is deliberately incomplete: current start events emit placeholder `{}` arguments and results expose only a bounded status/error projection. The proposal still requires an explicitly reviewed public tool argument/output policy before those tasks can be closed.

## What Changes

- Add a versioned AG-UI HTTP/SSE endpoint that accepts validated `RunAgentInput` requests and emits protocol-conformant lifecycle, message, reasoning, tool, state, activity, interrupt, and error events.
- Keep persisted Astra `RunEvent` records and authoritative Run snapshots as the source of truth; introduce a bidirectional adapter that translates AG-UI inputs into existing application commands and projects safe Astra events into one or more AG-UI events.
- Add stable public identifiers, lifecycle correlation, capability discovery, sanitized public-state schemas, and Astra-namespaced Activity schemas for plan graphs, Subagent trees, verification, artifacts, and other structured work.
- Add Snapshot/Delta projection rules with schema versions, revisions, source cursors, RFC 6902 patches, resynchronization, and deterministic fallback to authoritative snapshots when a baseline is missing or invalid.
- Represent governed approvals and other user-input pauses through AG-UI interrupts while preserving Astra continuation-token, frozen-action, version, permission, and idempotency enforcement. Protocol Runs may span one paused internal Astra Run through an explicit correlation mapping.
- Refactor the React chat path behind a transport-neutral projection/store boundary. The UI renders the first usable event immediately, batches high-frequency deltas by animation frame, applies Activity patches defensively, and keeps existing Astra-specific components and generic fallbacks.
- Run the native Astra stream and AG-UI stream side by side behind an explicit feature flag until parity, recovery, security, latency, and conformance checks pass. No existing API is removed in this change.
- Pin the pre-1.0 AG-UI TypeScript dependencies to reviewed exact versions and isolate their types from Astra domain and persistence packages.

## Capabilities

### New Capabilities
- `ag-ui-protocol-interoperability`: Defines the public AG-UI request, event, capability, security, identifier, lifecycle, Snapshot/Delta, recovery, extension, and compatibility contract over Astra's existing runtime.

### Modified Capabilities
- `agent-chat-ui`: Makes the React chat consume a transport-neutral projected state, render AG-UI streams progressively, handle structured Activities and interrupts, and provide safe generic fallbacks without replacing Astra's product UI.
- `live-agent-process-streaming`: Adds the AG-UI projection of safe process, reasoning, tool, plan, and multi-Agent events alongside the existing native Run SSE stream.
- `low-latency-answer-streaming`: Extends the first-token, frame-batching, terminal convergence, and recovery guarantees to the AG-UI transport without requiring a complete RunView before rendering.
- `interactive-tool-approval`: Exposes persistent Astra approval pauses as correlated AG-UI tool-bound interrupts while retaining the frozen-action and continuation security contract.
- `subagent-observability`: Adds sanitized AG-UI Activity snapshots and deltas for Agent lineage, aggregate state, terminal updates, and resynchronization.

## Impact

- Backend API: new AG-UI routes, request schemas, event projector/encoder, public projection schemas, capability declaration, interrupt correlation, and conformance tests under the interface/application boundary.
- Frontend: exact-version `@ag-ui/core` and `@ag-ui/client` dependencies; a transport abstraction, protocol event store/reducers, Activity renderer registry, interrupt renderer, and migration of streaming orchestration out of the monolithic chat component.
- Persistence: existing `RunEvent` and Run snapshot models remain authoritative; small durable protocol-correlation and projection-version records may be added where restart-safe interrupt and resume behavior requires them.
- Security: AG-UI client-provided tools are not executable Astra capabilities by default; forwarded properties are allowlisted; public State, Activity, tool, error, artifact, and reasoning payloads are explicitly sanitized.
- Operations: feature flag, protocol/version telemetry, dual-stream parity metrics, malformed-event and resynchronization metrics, and a rollback path to the native Astra transport.
- Compatibility: current native endpoints and UI behavior remain available during migration; no breaking removal is proposed.
