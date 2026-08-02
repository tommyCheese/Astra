## MODIFIED Requirements

### Requirement: Context usage is observable before submission
The system SHALL return actual usage when reported by the Provider and a conservatively estimated fallback otherwise, together with available input Tokens, remaining Tokens, usage ratio, status, automatic compact threshold, compaction implementation, active window number, checkpoint state, and selected model for the current conversation and optional Composer draft.

#### Scenario: Draft changes
- **WHEN** the user changes the Composer draft
- **THEN** the displayed projected usage is refreshed to include that draft
- **THEN** the UI identifies whether each value is reported or estimated

#### Scenario: Model changes
- **WHEN** the user selects another model
- **THEN** its context window, output reserve, projected usage, and remaining capacity are recalculated using the same Astra compaction policy

#### Scenario: Conversation has an active checkpoint
- **WHEN** earlier context has been semantically compacted
- **THEN** context status identifies the implementation, window number, compacted item count, recent retained count, and Token usage before and after compaction

### Requirement: Model-visible history is projected independently of audit history
The system SHALL build model-visible conversation context from a versioned semantic checkpoint, canonical protected context, and retained recent Runs while retaining every original Run, message, Turn, ToolCall, Artifact and Evidence for display, audit, sharing and checkpoint regeneration.

#### Scenario: Read an uncompacted conversation
- **WHEN** no compact or clear action has affected a conversation
- **THEN** the projection contains eligible prior Runs within the runtime safety bound

#### Scenario: Read a compacted conversation
- **WHEN** earlier Runs have been compacted
- **THEN** the projection contains the cumulative semantic checkpoint and retained recent Runs
- **THEN** folded Runs remain present in conversation and audit history

#### Scenario: Read a legacy character summary
- **WHEN** a Task contains V1 `summary` and `folded_run_ids` without a V2 checkpoint
- **THEN** the runtime treats the legacy summary as unverified continuation input for the first V2 compaction
- **THEN** it does not promote legacy text to verified facts or delete the underlying Runs

### Requirement: Context is automatically compacted before overflow
The system SHALL evaluate projected context usage before creating a Run and SHALL use the shared Agent compaction lifecycle to fold eligible older history into a cumulative semantic checkpoint before the first model invocation when the configured threshold is reached; the compacted projection MUST achieve the policy recovery waterline or return a classified capacity failure.

#### Scenario: Projected usage crosses the threshold
- **WHEN** existing conversation context plus the new request reaches the automatic compact threshold
- **THEN** older eligible history is semantically compacted and recent Runs are retained within a Token budget
- **THEN** usage is recalculated before the Run is created

#### Scenario: Current request cannot fit after compaction
- **WHEN** canonical protected context, the current request and the minimum valid checkpoint still exceed the safe input budget
- **THEN** Run creation fails with a classified context-capacity error
- **THEN** no normal model invocation is started

#### Scenario: Provider has no compaction feature
- **WHEN** the selected Provider supports ordinary model generation but exposes no compaction endpoint or parameter
- **THEN** conversation compaction uses the Astra-managed semantic checkpoint path
- **THEN** the resulting checkpoint remains readable and portable across Providers

### Requirement: Users can compact context manually
The system SHALL allow `/compact` on an idle conversation to run the same semantic compaction engine used by automatic compaction, persist a versioned checkpoint and folded Run boundary without creating a user Run or deleting conversation records, and return refreshed context state.

#### Scenario: Compact a long conversation
- **WHEN** the user executes `/compact` on an idle conversation with eligible older Runs
- **THEN** the system persists a semantic checkpoint and retained recent Run set
- **THEN** refreshed context is at or below the configured recovery waterline unless a classified compaction error is returned

#### Scenario: Compact an already minimal conversation
- **WHEN** no older Run is eligible or current usage is already below the minimum compaction benefit
- **THEN** the command succeeds idempotently without an unnecessary model call
- **THEN** recent visible context remains unchanged

#### Scenario: Manual semantic compaction fails
- **WHEN** Astra semantic and allowed deterministic emergency compaction cannot produce a valid checkpoint
- **THEN** the command returns a classified error and leaves the prior projection active
- **THEN** no Runs or audit records are deleted or newly folded
