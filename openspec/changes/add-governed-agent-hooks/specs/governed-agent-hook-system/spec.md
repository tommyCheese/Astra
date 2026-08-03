## ADDED Requirements

### Requirement: Versioned Hook manifests and deterministic catalog
The system SHALL define a versioned Hook manifest and SHALL assemble enabled Hook bindings deterministically from verified sources using event type, event schema version, mode, selector, handler identity, decision capabilities, effective failure policy, timeout, data access, effect ceiling, priority, version, and digest.

#### Scenario: Equivalent catalogs are assembled
- **WHEN** the same verified Hook manifests and configuration revisions are assembled repeatedly
- **THEN** the Hook ordering, resolved bindings, effective policies, and catalog digest are identical

#### Scenario: Unsupported Hook protocol is declared
- **WHEN** a Hook manifest declares a protocol or event schema major version the host does not support
- **THEN** the binding is excluded and a safe incompatibility diagnostic is recorded

#### Scenario: Duplicate Hook identity conflicts
- **WHEN** two enabled sources contribute incompatible definitions for the same Hook identity and version
- **THEN** catalog assembly rejects the conflict rather than selecting by discovery order

### Requirement: Stable and least-privilege event envelopes
Every Hook occurrence SHALL use a schema-validated event envelope containing a unique event identity, source, type, schema version, occurrence time, correlation and causation identity, trace identity, applicable Run/Task/Conversation/AgentExecution references, attempt, scope, data labels, and an event-specific minimal payload.

#### Scenario: Tool event is dispatched
- **WHEN** the runtime produces a tool lifecycle occurrence
- **THEN** the Hook receives the documented tool-event projection and stable references without receiving an unrestricted repository, database session, secret value, or transcript path

#### Scenario: Event is redelivered
- **WHEN** an observation delivery is retried after a transport failure
- **THEN** the redelivery preserves the original source and event identity and increments delivery-attempt metadata

### Requirement: Admission and observation modes are separated
The system SHALL execute admission Hooks synchronously before their documented commit point and SHALL deliver observation Hooks only after the occurrence has become fact; observation Hooks MUST NOT change or roll back the originating action.

#### Scenario: Admission Hook denies an action
- **WHEN** a matching admission Hook returns a valid deny result before the action commit point
- **THEN** the action does not execute and the denial is recorded with Hook and event identity

#### Scenario: Observation Hook fails
- **WHEN** a post-action observation Hook times out or returns an invalid response
- **THEN** the completed action remains completed and the delivery enters retry or dead-letter processing

#### Scenario: Observation manifest requests blocking authority
- **WHEN** an observation Hook declares deny, ask, input patch, or stop authority
- **THEN** manifest validation rejects that authority for the observation binding

### Requirement: Restriction-only admission result composition
The admission dispatcher SHALL combine matching results using effective trust and deterministic order, SHALL apply deny before ask before accepted mutation or context before continue, and MUST treat any Hook allow result only as absence of an additional Hook restriction.

#### Scenario: Hook allow conflicts with platform deny
- **WHEN** a Hook returns allow or continue for an action denied by platform or managed policy
- **THEN** the action remains denied

#### Scenario: Multiple Hooks disagree
- **WHEN** one matching Hook returns ask and another matching Hook returns deny
- **THEN** the aggregate result is deny and no lower-priority result can widen it

#### Scenario: Mutations conflict
- **WHEN** two accepted Hook patches write incompatible values to the same protected input location
- **THEN** admission fails closed with an auditable patch-conflict reason

### Requirement: Event-specific decision capabilities are enforced
The system SHALL allow each event type only its documented decision capabilities and MUST prevent a Hook from modifying protected prompts, tool catalogs, permission state, validation outcomes, completion proof, or other canonical state outside those capabilities.

#### Scenario: Prompt Hook adds context
- **WHEN** a `prompt.before_accept` Hook returns context within its allowed labels, purpose, and token budget
- **THEN** the system attaches the context with Hook provenance while retaining the original user prompt as canonical input

#### Scenario: Subagent Hook attempts to widen delegation
- **WHEN** a `subagent.before_start` Hook proposes additional tools, credentials, data, network, depth, or budget beyond the parent contract
- **THEN** the widened fields are rejected and the child is not started under the proposal

#### Scenario: Completion Hook repeatedly blocks completion
- **WHEN** the same Hook reaches its per-Run completion-block limit
- **THEN** the Run enters the configured explicit blocked or failed disposition rather than invoking the Hook indefinitely

### Requirement: Hook handlers execute through bounded backends
The system SHALL support host-managed, isolated-command, and restricted-HTTP Hook handlers with bounded timeout, cancellation, input and output sizes, environment, filesystem, network, credentials, concurrency, and safe diagnostics.

#### Scenario: External command Hook runs
- **WHEN** an external command handler is invoked
- **THEN** it runs under its configured isolated runtime profile using structured JSON input and output without inheriting unrestricted API-process authority

#### Scenario: HTTP Hook targets an unapproved destination
- **WHEN** an HTTP handler resolves or redirects to a destination outside its effective origin and network policy
- **THEN** the request is denied before protected data or credentials are sent

#### Scenario: Handler exceeds output limit
- **WHEN** a command or HTTP handler produces an oversized response
- **THEN** the response is not admitted as a decision and only a bounded, redacted diagnostic is retained

### Requirement: Failure policy is constrained by Hook purpose and trust
The system SHALL derive effective failure behavior from the event category, requested manifest policy, trust tier, and managed policy; security, compliance, authorization, and mutation admission Hooks MUST fail closed, while observation Hooks SHALL continue the originating Run and use retry delivery.

#### Scenario: Security Hook times out
- **WHEN** a mandatory security admission Hook exceeds its deadline
- **THEN** the protected action is blocked with a classified Hook timeout and cannot be changed to fail-open by a lower-trust configuration

#### Scenario: Optional context Hook fails
- **WHEN** an administrator-configured context Hook has an effective fail-open-with-audit policy and returns invalid output
- **THEN** no partial output is injected, execution continues, and the failure is audited

### Requirement: Reliable observation delivery and replay
The system SHALL persist observation deliveries through a transactional outbox, SHALL claim work with fencing, SHALL retry with bounded backoff, and SHALL expose terminal dead-letter records and authorized replay lineage.

#### Scenario: Process crashes after event commit
- **WHEN** the process commits a canonical lifecycle occurrence and its outbox row but crashes before delivery
- **THEN** a recovery worker later delivers the observation without repeating the canonical action

#### Scenario: Delivery is claimed concurrently
- **WHEN** multiple workers attempt to deliver the same outbox item
- **THEN** fencing permits only the valid claimant to record the attempt and terminal result

#### Scenario: Operator replays dead letter
- **WHEN** an authorized operator replays a dead-letter delivery
- **THEN** the system preserves the original event identity, creates new delivery lineage, and does not re-execute the originating action

### Requirement: Run snapshots freeze Hook behavior
The system SHALL freeze every Run's resolved Hook bindings and behavior identities and SHALL validate those identities before pending admission, approval resume, recovery, or observation redelivery.

#### Scenario: Hook changes while Run waits
- **WHEN** a Run resumes after a bound admission handler, event schema, selector, decision capability, or failure policy digest has changed
- **THEN** the pending admission does not silently execute with the new behavior

#### Scenario: Old Run has no Hook snapshot
- **WHEN** a Run created before Hook support is recovered
- **THEN** the system treats its Hook set as empty and preserves legacy behavior unless managed migration policy explicitly blocks that recovery

### Requirement: Hook recursion and control-plane mutation are bounded
Hook-derived effects SHALL carry a causation chain and Hook depth, SHALL NOT recursively trigger user Hook dispatch by default, and MUST NOT register, enable, disable, approve, or widen the executing Hook's own authority.

#### Scenario: Hook action re-enters the same event
- **WHEN** a Hook-derived action would invoke the same Hook within the same causation chain
- **THEN** dispatch suppresses or rejects the re-entry and records the recursion guard

#### Scenario: Hook attempts self-enablement
- **WHEN** a handler attempts to alter its definition, trust, digest, enablement, grant, audit, or runtime policy
- **THEN** the action is denied as a protected control-plane mutation

### Requirement: Hook administration is inspectable and testable
Authorized users and administrators SHALL be able to inspect effective Hook sources, identities, scopes, selectors, capabilities, policies, health, executions, latency, failures, outbox and dead-letter state, and SHALL be able to perform a side-effect-free dry run before enablement.

#### Scenario: Administrator dry-runs a Hook
- **WHEN** an administrator supplies a synthetic event to the dry-run API
- **THEN** the system reports schema validation, matching, effective policy and simulated result parsing without executing the handler's real side effects

#### Scenario: User inspects a blocked Run
- **WHEN** a Hook denies, asks, times out, conflicts, or reaches a block limit
- **THEN** the Run timeline presents a safe explanation and the responsible Hook identity while preserving technical diagnostics under progressive disclosure

### Requirement: External Hook configuration import is explicit and non-executing
The system SHALL parse supported external Hook formats only into an inert review preview and MUST require explicit installation into a managed immutable source, capability review, runtime selection, and digest acceptance before enablement.

#### Scenario: Workspace contains Claude-style Hook config
- **WHEN** a Task Workspace contains a supported Claude Code or Copilot Hook configuration
- **THEN** the runtime does not execute it and may only present it as an untrusted import candidate

#### Scenario: Import contains unsupported semantics
- **WHEN** an external matcher, event, handler type, environment behavior, or exit-code meaning cannot be represented safely
- **THEN** the preview identifies the unmapped behavior and refuses automatic enablement

