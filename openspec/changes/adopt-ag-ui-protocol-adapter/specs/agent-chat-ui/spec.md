## ADDED Requirements

### Requirement: Chat presentation is independent of the active stream transport
The React chat SHALL consume stable projected messages, Activities, reasoning entries, interrupts, and Run state through a transport-neutral store, and components MUST NOT depend directly on Astra persistence events or raw AG-UI network events.

#### Scenario: Feature flag selects AG-UI
- **WHEN** the AG-UI frontend transport is enabled
- **THEN** the existing Astra chat components render projected AG-UI state without changing their visual product responsibilities

#### Scenario: Rollback selects native transport
- **WHEN** the AG-UI frontend transport is disabled after a rollout problem
- **THEN** the chat uses the native Astra transport and preserves available conversation, approval, plan, and cancellation behavior

### Requirement: Chat renders the first usable streamed state immediately
The React chat SHALL render assistant text from the first displayable content event and SHALL render a structured Activity from its first valid snapshot without waiting for `TEXT_MESSAGE_END`, `RUN_FINISHED`, or a terminal Run snapshot.

#### Scenario: Assistant answer is still streaming
- **WHEN** the client receives a text-message start and the first content delta
- **THEN** the assistant bubble becomes visible with the partial content while the message remains streaming

#### Scenario: Plan is still executing
- **WHEN** the client receives a valid `astra.plan` Activity snapshot before the Run completes
- **THEN** the current plan renders immediately and later node deltas update it progressively

### Requirement: Activity deltas fail safely and locally
The React projection store SHALL validate Activity type, schema version, baseline revision, and JSON Patch application before committing a delta, and SHALL isolate a failed Activity from messages and other Activities while awaiting an authoritative snapshot.

#### Scenario: Activity revision is discontinuous
- **WHEN** a delta base revision does not match the local Activity revision
- **THEN** the client keeps the last valid Activity visible, marks it as resynchronizing, and does not apply the incompatible patch
- **THEN** text streaming and unrelated Activities continue rendering

#### Scenario: Replacement snapshot arrives
- **WHEN** an authoritative compatible Activity snapshot arrives after a local patch failure
- **THEN** the client replaces the stale Activity, clears its resynchronizing state, and resumes applying compatible deltas

### Requirement: Unknown protocol extensions have accessible fallbacks
The chat SHALL render safe generic fallback content for unknown Activity types, Activity schema versions, and interrupt reasons, and MUST NOT crash, hide the whole conversation, or offer an action not described by the received safe schema.

#### Scenario: Unknown Activity arrives
- **WHEN** the client receives an Activity type for which no Astra renderer is registered
- **THEN** it shows the provided title, status, summary, and fallback text in an accessible generic card

#### Scenario: Unknown interrupt reason arrives
- **WHEN** an interrupt reason is not recognized but includes a message and response schema
- **THEN** the client renders a constrained generic input form or a non-actionable explanation according to that schema

