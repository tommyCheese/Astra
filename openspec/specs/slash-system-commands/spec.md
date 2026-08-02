# slash-system-commands Specification

## Purpose
TBD - created by archiving change add-context-window-management. Update Purpose after archive.
## Requirements
### Requirement: The server exposes a system command catalog
The system SHALL expose a catalog of registered, user-invocable slash commands with stable names, localized descriptions, effects, and availability, and SHALL initially register only `compact` and `clear`.

#### Scenario: Discover preset commands
- **WHEN** a client requests the system command catalog
- **THEN** the response contains `/compact` and `/clear`
- **THEN** each entry explains its context effect

### Requirement: Only registered system commands can execute
The system MUST resolve command execution through the server registry and MUST reject unknown, disabled, or unavailable names without interpreting them as arbitrary server operations.

#### Scenario: Execute a registered command
- **WHEN** the user executes a command present and available in the catalog
- **THEN** the registry dispatches its predefined handler
- **THEN** the response identifies the executed command and its result

#### Scenario: Execute an unknown command
- **WHEN** a client requests execution of an unregistered command
- **THEN** the request fails with a classified command-not-found error
- **THEN** no context or conversation state changes

### Requirement: System commands are visible without becoming model input
The client and server SHALL execute host system commands without creating a Run, invoking a model, or binding a Skill, and SHALL preserve the executed slash invocation as a command-styled user timeline entry that is excluded from model context.

#### Scenario: Execute compact from the Composer
- **WHEN** the user selects or submits `/compact`
- **THEN** the slash query is consumed by command execution
- **THEN** a command-styled user timeline entry containing the `/compact` prefix is created
- **THEN** no model-visible user message or model Run is created

#### Scenario: Highlight a command prefix
- **WHEN** an executed command is shown in a user timeline entry
- **THEN** the slash command prefix is visually distinguished from its arguments

### Requirement: Commands support their declared argument mode
The command catalog SHALL distinguish commands with no arguments, optional arguments, and required arguments. `/compact` SHALL accept an optional direction string and expose a useful default direction, while `/clear` SHALL execute with no user-message body.

#### Scenario: Compact with the default direction
- **WHEN** the user selects `/compact` without writing a direction
- **THEN** the Composer stages the catalog-provided default direction
- **THEN** execution records the full command invocation in the timeline

#### Scenario: Clear without a message body
- **WHEN** the user selects or submits `/clear`
- **THEN** the context is cleared immediately without requiring additional user text

### Requirement: System commands coexist with Skill slash options
The Composer SHALL use one command-boundary detector and one accessible option list for registered system commands and eligible Skills, while preserving their distinct execution semantics.

#### Scenario: Open the root slash menu
- **WHEN** the user types `/` at a supported command boundary
- **THEN** matching system commands and eligible Skills are shown with distinguishable kinds

#### Scenario: Select a Skill option
- **WHEN** the user chooses a Skill result
- **THEN** the existing Skill token selection lifecycle is used
- **THEN** no system command executes

#### Scenario: Select a command option
- **WHEN** the user chooses a system command result
- **THEN** that command executes immediately against the current conversation
- **THEN** no Skill token is added

### Requirement: Command interaction is accessible and recoverable
The Composer SHALL support pointer and keyboard command selection, SHALL prevent duplicate submission while execution is pending, and SHALL provide success or classified failure feedback.

#### Scenario: Execute with Enter
- **WHEN** a system command option is active and the user presses Enter
- **THEN** the command executes instead of submitting the Composer
- **THEN** focus returns to the Composer after completion

#### Scenario: Command execution fails
- **WHEN** a system command fails
- **THEN** the UI presents the classified error
- **THEN** the command text remains available for retry
