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

### Requirement: System commands do not become messages
The client and server SHALL execute system commands as host operations and MUST NOT add the slash text to conversation messages, create a Run, invoke a model, or bind a Skill.

#### Scenario: Execute compact from the Composer
- **WHEN** the user selects or submits `/compact`
- **THEN** the slash query is consumed by command execution
- **THEN** no user message or model Run is created

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

