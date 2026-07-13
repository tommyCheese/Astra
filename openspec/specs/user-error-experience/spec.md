# user-error-experience Specification

## Purpose
TBD - created by archiving change improve-error-experience-and-api-contract. Update Purpose after archive.
## Requirements
### Requirement: User-actionable errors open an accessible dialog
The frontend SHALL present validation, state, policy, approval, and supported capability errors in an accessible error dialog with a concise explanation and an action appropriate to the error code.

#### Scenario: Empty goal is submitted
- **WHEN** the user submits an empty task goal
- **THEN** the frontend shows a dialog explaining that a goal is required
- **THEN** closing the dialog returns focus to the goal input

#### Scenario: Policy denies an action
- **WHEN** an API or Run error indicates a policy denial
- **THEN** the dialog explains that the current execution policy disallows the action
- **THEN** it offers an appropriate action such as changing mode, revising the request, or dismissing the dialog

### Requirement: Technical errors are safe and diagnosable
The frontend SHALL show a generic technical-failure dialog for configuration, dependency, infrastructure, and runtime errors and SHALL display the safe message and trace identifier without exposing raw internal details.

#### Scenario: Database is unavailable
- **WHEN** the create Run request returns `infrastructure.database_unavailable`
- **THEN** the dialog explains that the service cannot currently access required storage
- **THEN** it provides a retry action only when `retryable` is true and displays the trace identifier for support

### Requirement: Failed Runs remain visible in conversation
The frontend SHALL render a task-level failure card or dialog when a Run terminal result contains a structured error envelope, including its user-safe message, status, retryability, and trace identifier.

#### Scenario: Background model failure
- **WHEN** a Run transitions to failed after task creation
- **THEN** the conversation displays the Run failure rather than a successful assistant answer
- **THEN** the user can retry or start a revised task according to the error contract

### Requirement: API client preserves structured errors
The frontend API client SHALL parse structured error envelopes and expose their code, type, retryability, trace identifier, and safe message to callers.

#### Scenario: Response body is an error envelope
- **WHEN** an API request receives a non-success response containing the documented envelope
- **THEN** the client throws or returns a typed error derived from that envelope
- **THEN** UI code does not need to parse raw response text

