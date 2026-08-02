## ADDED Requirements

### Requirement: Composer exposes a Skill slash command panel
The Chat Composer SHALL open a searchable Skill command panel when the user types `/` at a command boundary and SHALL list only enabled Skills with an active eligible Published Revision.

#### Scenario: Open the panel at a command boundary
- **WHEN** the user types `/` at the beginning of the Composer or after whitespace
- **THEN** the UI opens a Skill command panel anchored to the Composer
- **THEN** options identify each Skill by name, description, origin, and selected state

#### Scenario: Filter Skill options
- **WHEN** the user types characters after the slash without crossing a whitespace boundary
- **THEN** the UI filters options by name, description, and qualified identity
- **THEN** a no-results state is shown without modifying the current Skill selections

#### Scenario: Slash is ordinary text
- **WHEN** a slash occurs inside a URL, filesystem path, or non-command token
- **THEN** the Skill panel remains closed and the text is preserved unchanged

### Requirement: Slash Skill selection is keyboard and pointer accessible
The Skill command panel SHALL support pointer selection and listbox keyboard navigation, and SHALL preserve normal Composer submission behavior when the panel is closed.

#### Scenario: Select with the keyboard
- **WHEN** the panel is open and the user navigates with Arrow, Home, or End keys and presses Enter
- **THEN** the highlighted Skill is selected
- **THEN** Enter does not submit the Composer
- **THEN** focus returns to the message input

#### Scenario: Cancel slash selection
- **WHEN** the panel is open and the user presses Escape
- **THEN** the panel closes without changing selected Skills
- **THEN** the typed slash text remains available as ordinary message text

### Requirement: Selected Skills remain visibly highlighted in the Composer
The Composer SHALL render every selected Skill as a persistent high-contrast token outside the plain-text message value, and selected state MUST remain understandable without relying on color alone.

#### Scenario: Select a Skill
- **WHEN** the user chooses a Skill from the slash panel or existing attachment menu
- **THEN** the slash query range is removed from the message
- **THEN** a highlighted token with the Skill name, icon, selected semantics, and remove control appears in the Composer

#### Scenario: Remove a selected Skill
- **WHEN** the user activates a token's remove control
- **THEN** only that Skill is removed and the remaining message and Skill tokens are preserved

#### Scenario: Remove the last token with Backspace
- **WHEN** the message input is empty, its caret is at the beginning, and the user presses Backspace
- **THEN** the last selected Skill token is removed

#### Scenario: View selections on supported layouts
- **WHEN** selected Skill tokens are shown in light theme, dark theme, narrow view, or reduced-motion mode
- **THEN** each token, focus indicator, label, and remove action remains readable and operable

### Requirement: Skill tokens have a one-Run draft lifecycle
The Composer SHALL treat selected Skill tokens as part of the unsent message draft, SHALL consume them only after successful Run creation, and SHALL retain them when submission fails.

#### Scenario: Run creation succeeds
- **WHEN** a message with selected Skill tokens creates a Run successfully
- **THEN** the submitted user message contains only the intended message text
- **THEN** the Composer clears the message, slash state, and selected Skill tokens

#### Scenario: Run creation fails
- **WHEN** network, validation, or Skill activation failure prevents Run creation
- **THEN** the Composer retains the message and selected Skill tokens for correction and retry

#### Scenario: Start a new conversation
- **WHEN** the user explicitly starts a new conversation before submitting the draft
- **THEN** the previous conversation's selected Skill tokens are cleared
