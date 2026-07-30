## ADDED Requirements

### Requirement: Composer displays live context-window status
The Chat Composer SHALL display the selected model's context capacity, estimated used and remaining Tokens, usage status, and active compression state inside the model selector, SHALL NOT render context status as a separate Composer row, and SHALL update those values when the conversation, model, draft, or context command result changes.

#### Scenario: View normal context usage
- **WHEN** a conversation and model are selected
- **THEN** a compact circular context indicator inside the model selector shows estimated usage against total capacity
- **THEN** assistive text exposes used, remaining, model, and estimate semantics

#### Scenario: Keep the Composer input area compact
- **WHEN** context-window status is available
- **THEN** the UI does not add a separate context row above the message input
- **THEN** the input height and control-row layout remain unchanged

#### Scenario: Approach automatic compression
- **WHEN** usage reaches a warning or automatic compression threshold
- **THEN** the indicator changes status with text in addition to color
- **THEN** the user can discover `/compact` as a manual action

#### Scenario: Context was compacted or cleared
- **WHEN** a context command or automatic compression changes the projection
- **THEN** the Composer refreshes the indicator without requiring a page reload
- **THEN** the UI identifies the latest context action

#### Scenario: Inspect exact circular-indicator values
- **WHEN** the user hovers the circular context indicator or opens the selected model control
- **THEN** exact used, total, remaining, source, and latest-action details are available
- **THEN** the indicator does not rely on color alone to convey warning or critical state

#### Scenario: Hover outside the circular indicator
- **WHEN** the user hovers the model name, strategy summary, or empty area of the selected model control
- **THEN** the circular indicator tooltip remains hidden
- **THEN** assistive technology can still read the context status from the selected model control

#### Scenario: Change the selected model configuration
- **WHEN** the selected model's effective context configuration changes
- **THEN** the circular indicator and context request update without a page reload
- **THEN** no standalone context row is introduced

#### Scenario: Present context details in user-facing language
- **WHEN** the Composer, model menu, or Model Settings displays context information
- **THEN** the UI describes the limit, remaining space, estimate, and latest action in user-facing language
- **THEN** internal catalog, fallback, verification, metadata, and command-registry labels are not exposed
