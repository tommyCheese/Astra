## ADDED Requirements

### Requirement: Context window is initialized from the selected model
The system SHALL resolve a context-window capacity from an Astra-maintained catalog backed by provider official documentation, SHALL NOT accept user-provided context-window or maximum-output overrides, SHALL use a conservative documented fallback for unknown models, and SHALL expose the resolved identity, capacity, official documentation URL, source, and verification state to clients.

#### Scenario: Select a known model family
- **WHEN** a user selects a model whose family exists in the server window catalog
- **THEN** the context state uses that family's configured window capacity
- **THEN** the response identifies the selected provider and model

#### Scenario: Select an unknown model
- **WHEN** a model does not match a catalog entry
- **THEN** the context state uses the conservative fallback capacity
- **THEN** context management remains available

#### Scenario: Attempt to override an official model limit
- **WHEN** a client submits context-window or maximum-output configuration with a status, command, or Run request
- **THEN** the request contract rejects or ignores the unsupported field
- **THEN** automatic compaction and capacity rejection continue to use the server catalog

### Requirement: Model context capability configuration is backward compatible
The system SHALL represent context capabilities per model, SHALL preserve existing provider configurations that contain comma-separated model IDs, SHALL discard obsolete manual capacity fields during migration, and SHALL make context capacity read-only in Model Settings.

#### Scenario: Load a legacy provider configuration
- **WHEN** the client loads a saved provider whose models are stored as a string
- **THEN** each parsed model ID becomes a model profile in automatic mode
- **THEN** the models remain selectable without user intervention

#### Scenario: Load a legacy manual profile
- **WHEN** saved browser configuration contains manual context-window or maximum-output fields
- **THEN** the model ID remains available
- **THEN** the obsolete capacity fields are removed and the official catalog value is displayed

#### Scenario: Inspect an official model capability
- **WHEN** a known model is shown in Model Settings
- **THEN** its context window and maximum output are read-only
- **THEN** the UI identifies the official source and offers its documentation URL

### Requirement: Context usage is observable before submission
The system SHALL return estimated used Tokens, available input Tokens, remaining Tokens, usage ratio, status, auto-compact threshold, and whether a summary is active for the current conversation, selected model, and optional Composer draft.

#### Scenario: Draft changes
- **WHEN** the user changes the Composer draft
- **THEN** the displayed projected usage is refreshed to include that draft
- **THEN** the UI labels the value as an estimate

#### Scenario: Model changes
- **WHEN** the user selects another model
- **THEN** the displayed window capacity and usage ratio are recalculated for that model

### Requirement: Model-visible history is projected independently of audit history
The system SHALL build model-visible conversation context from the persisted summary and currently visible completed Runs, while retaining all original Runs and messages for conversation display, audit, and sharing.

#### Scenario: Read an uncompacted conversation
- **WHEN** no compact or clear action has affected a conversation
- **THEN** the context projection contains its eligible prior Runs up to the runtime safety bound

#### Scenario: Read a compacted conversation
- **WHEN** earlier Runs have been compacted
- **THEN** the projection contains the persisted summary and retained recent Runs
- **THEN** folded Runs remain present in conversation history

### Requirement: Context is automatically compacted before overflow
The system SHALL evaluate projected context usage before creating a Run and SHALL compact eligible older history before the first model invocation when the configured automatic threshold is reached.

#### Scenario: Projected usage crosses the threshold
- **WHEN** an existing conversation plus the new request reaches the automatic compact threshold
- **THEN** older visible Runs are folded into a bounded summary
- **THEN** usage is recalculated before the Run is created

#### Scenario: Current request cannot fit after compaction
- **WHEN** compaction cannot bring the projected request within the safe input budget
- **THEN** Run creation fails with a classified context-capacity error
- **THEN** no model invocation is started

### Requirement: Users can compact context manually
The system SHALL allow `/compact` to immediately fold eligible older context into a bounded summary without creating a model Run or deleting conversation records.

#### Scenario: Compact a long conversation
- **WHEN** the user executes `/compact` on an idle conversation with eligible older Runs
- **THEN** the system persists a new context summary and folded Run set
- **THEN** it returns refreshed context state with a lower or equal estimated usage

#### Scenario: Compact an already minimal conversation
- **WHEN** no older Run is eligible for folding
- **THEN** the command succeeds idempotently
- **THEN** recent visible context remains unchanged

### Requirement: Users can clear model context manually
The system SHALL allow `/clear` to exclude all existing Runs and summaries from subsequent model context while preserving the conversation and its records.

#### Scenario: Clear a conversation
- **WHEN** the user executes `/clear` on an idle conversation
- **THEN** subsequent context state excludes all pre-command Runs and has no active summary
- **THEN** the historical messages remain visible to the user

#### Scenario: Continue after clear
- **WHEN** a new Run is created after `/clear`
- **THEN** that Run becomes eligible context for later requests
- **THEN** Runs from before `/clear` remain excluded

### Requirement: Context mutations are concurrency safe
The system MUST reject manual context mutations while the conversation has an active or waiting Run and MUST make repeated compression operations idempotent.

#### Scenario: Execute a command during an active Run
- **WHEN** the conversation contains a non-terminal Run
- **THEN** `/compact` and `/clear` fail with a classified state error
- **THEN** persisted context state is unchanged
