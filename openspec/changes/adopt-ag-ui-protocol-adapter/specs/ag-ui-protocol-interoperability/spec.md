## ADDED Requirements

### Requirement: Astra exposes a versioned AG-UI HTTP streaming contract
The system SHALL expose a feature-gated HTTP endpoint that accepts a validated AG-UI `RunAgentInput` and streams protocol events as SSE, SHALL advertise its supported AG-UI and Astra profile capabilities, and SHALL leave the native Astra Run APIs available during migration.

#### Scenario: Compatible client starts a message
- **WHEN** an authorized client submits a valid input with a thread identifier, protocol Run identifier, message history, and allowed Astra properties
- **THEN** the system correlates the request to an authorized Astra Task and internal Run
- **THEN** the response begins with `RUN_STARTED` and continues as an AG-UI event stream

#### Scenario: AG-UI feature is disabled
- **WHEN** a client calls the AG-UI endpoint while the feature flag is disabled
- **THEN** the system rejects the protocol request without changing the native Run endpoints or creating an internal Run

#### Scenario: Client discovers capabilities
- **WHEN** a client requests AG-UI capabilities
- **THEN** the system returns the supported transport, message, reasoning, tool, State, Activity, multi-Agent, execution-control, and human-in-the-loop features with an Astra profile version
- **THEN** omitted or unsupported features are not implied to be available

### Requirement: Inbound AG-UI input cannot expand Astra authority
The system MUST authenticate and authorize the target thread, SHALL validate `forwardedProps` against an explicit versioned allowlist, and MUST NOT convert arbitrary client-provided tools, state, messages, or extension properties into executable Astra capabilities or trusted runtime facts.

#### Scenario: Client provides an unregistered tool
- **WHEN** `RunAgentInput.tools` contains a tool that is not an independently governed Astra capability
- **THEN** the system does not register, expose to the model, authorize, or execute that tool

#### Scenario: Client submits an unknown runtime property
- **WHEN** `forwardedProps` contains an unknown or invalid Astra execution property
- **THEN** the system rejects or safely ignores it according to the advertised profile without passing it to the model or runtime

#### Scenario: Client targets another user's thread
- **WHEN** the authenticated principal is not authorized for the supplied `threadId`
- **THEN** the system rejects the request without disclosing whether the target Task or Conversation exists

### Requirement: Public events preserve valid AG-UI lifecycle ordering
The adapter SHALL project committed Astra facts into zero or more AG-UI events with deterministic identifiers and MUST enforce valid Run, text-message, reasoning-message, and tool-call lifecycle ordering, including exactly one protocol terminal outcome.

#### Scenario: Astra streams an answer
- **WHEN** Astra emits answer start, one or more deltas, content completion, and a successful terminal state
- **THEN** the client receives one `TEXT_MESSAGE_START`, ordered `TEXT_MESSAGE_CONTENT` events, one `TEXT_MESSAGE_END`, and one successful `RUN_FINISHED`

#### Scenario: Projector replays a duplicate source event
- **WHEN** the same committed Astra source event is encountered more than once in one projection lifecycle
- **THEN** the adapter does not duplicate visible content, tool results, Activity mutations, or terminal events

#### Scenario: Astra fails before producing text
- **WHEN** the internal Run fails before an assistant message starts
- **THEN** the protocol stream emits a sanitized `RUN_ERROR` and does not fabricate an empty completed assistant message

### Requirement: Public projections are sanitized and bounded
The system MUST construct AG-UI messages, reasoning, tool, State, Activity, error, and extension payloads from explicit public schemas, MUST enforce payload and text bounds, and MUST NOT expose hidden chain-of-thought, secrets, continuation tokens, private paths, unauthorized workspace data, raw internal exceptions, or unverified artifact links.

#### Scenario: Tool output contains a credential and private path
- **WHEN** a completed tool result contains credential metadata, an internal sandbox path, and a safe public result
- **THEN** only the allowed public result and safe status metadata appear in AG-UI events

#### Scenario: Provider emits hidden reasoning
- **WHEN** model transport returns provider-only reasoning or hidden chain-of-thought
- **THEN** the adapter emits no corresponding reasoning content and may emit only an allowed availability status

#### Scenario: Public payload exceeds its bound
- **WHEN** a tool result, error, reasoning summary, or custom Activity exceeds its public size limit
- **THEN** the system emits a bounded safe representation with explicit truncation metadata rather than an unbounded payload

### Requirement: State and Activity projections establish recoverable baselines
The system SHALL emit versioned public State and Activity snapshots before dependent deltas, SHALL use stable entity identifiers and revisions, and SHALL emit RFC 6902 deltas only when the baseline is known and compatible.

#### Scenario: Plan Activity first becomes visible
- **WHEN** a Trusted Run produces a public plan for the first time on a protocol stream
- **THEN** the system emits an `ACTIVITY_SNAPSHOT` with type `astra.plan`, schema version, Activity revision, stable node identifiers, and fallback text before any plan delta

#### Scenario: One plan node changes status
- **WHEN** the client has the current plan Activity revision and one stable node changes status without a structural plan revision
- **THEN** the system may emit an `ACTIVITY_DELTA` whose patch applies to the prior public projection and identifies its base and resulting revisions

#### Scenario: Baseline cannot be proven
- **WHEN** the source cursor has a gap, the schema or plan version changes, projection cache is absent, permissions change, or a patch would be unsafe
- **THEN** the system emits an authoritative replacement snapshot instead of the uncertain delta

#### Scenario: Client reconnects
- **WHEN** an AG-UI stream reconnects without a proven cross-connection baseline
- **THEN** the system sends current public message, State, and relevant Activity snapshots before new live deltas

### Requirement: Astra-specific structured work has portable fallbacks
The system SHALL represent plans, Subagent lineage, verification, artifacts, and other structured Astra work through versioned `astra.*` Activities or custom events that include enough generic title, summary, status, and fallback text for clients without Astra-specific renderers.

#### Scenario: Generic client receives a plan
- **WHEN** a compatible AG-UI client does not implement the `astra.plan` renderer
- **THEN** it can still present a safe human-readable plan title, status, and fallback summary without interpreting Astra's graph schema

#### Scenario: Client receives an unknown Astra extension version
- **WHEN** a client does not support an Activity schema version
- **THEN** it does not apply incompatible deltas and can render the generic fallback or request a supported snapshot

### Requirement: Interrupts preserve Astra continuation security
The adapter SHALL represent eligible durable approval and input pauses as AG-UI interrupt outcomes, SHALL persist protocol-to-internal correlation needed across restart, and MUST resolve them only through existing Astra approval or continuation services with frozen-action, version, expiry, and idempotency checks.

#### Scenario: Tool call waits for approval
- **WHEN** Astra persists a pending approval and enters `waiting_user`
- **THEN** the stream emits required public State and message snapshots followed by `RUN_FINISHED` with a `tool_call` interrupt bound to the original tool call
- **THEN** the response schema exposes only decisions actually supported for that frozen action

#### Scenario: User resumes an interrupted approval
- **WHEN** a new protocol Run on the same thread resolves the current interrupt with a valid payload
- **THEN** the adapter correlates it to the paused internal Run and invokes the existing approval service exactly once
- **THEN** the new stream emits the continued result without regenerating the frozen action

#### Scenario: Resume is stale or replayed
- **WHEN** a resume references a consumed, expired, mismatched, or stale interrupt or state version
- **THEN** the system emits a safe protocol error and does not execute the tool or continuation again

### Requirement: Transport abort and runtime cancellation are distinct
The integration SHALL distinguish closing an AG-UI HTTP stream from cancelling the durable Astra Run and SHALL provide or advertise an explicit authorized cancellation operation whose result converges with the native Run cancellation contract.

#### Scenario: Browser disconnects unexpectedly
- **WHEN** the AG-UI response connection closes without an authorized cancellation command
- **THEN** the system does not claim that the durable internal Run was cancelled solely because transport ended

#### Scenario: User explicitly cancels
- **WHEN** an authorized AG-UI client invokes the advertised cancellation operation for an active correlated Run
- **THEN** Astra performs its normal idempotent Run cancellation and subsequent snapshots or events expose the cancelled terminal state

### Requirement: Protocol upgrades are isolated and verified
The system SHALL pin reviewed pre-1.0 AG-UI dependencies to exact versions, SHALL publish an integration profile version, and MUST pass protocol ordering, sanitization, interrupt, snapshot/delta, and frontend fixture tests before changing the supported SDK or profile.

#### Scenario: AG-UI dependency is upgraded
- **WHEN** maintainers propose a different AG-UI package version
- **THEN** golden streams and compatibility tests identify any event, schema, capability, or interrupt behavior change before deployment

