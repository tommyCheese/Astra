# interactive-tool-approval Specification

## Purpose
TBD - created by archiving change add-interactive-tool-approvals. Update Purpose after archive.
## Requirements
### Requirement: Request-approval mode pauses before tool execution
The runtime SHALL create a persistent approval request and enter `waiting_user` before executing every resolved tool call in `request_approval` mode unless a valid Run-scoped grant matches the frozen action.

#### Scenario: Unapproved tool call is proposed
- **WHEN** the model proposes an eligible tool call in `request_approval` mode and no grant matches it
- **THEN** the system freezes the tool name and normalized input, records an awaiting-approval ToolCall, emits an approval request, and does not invoke the tool

#### Scenario: Auto-approval skips only the interactive gate
- **WHEN** the same action is proposed in `auto_approval` mode
- **THEN** the runtime may execute it without an approval request only after all registration, permission, risk, backend, and sandbox checks pass

### Requirement: Approval decisions are bound to the frozen action
The system MUST atomically accept exactly one `approve_once`, `allow_similar`, or `reject` decision for a pending approval and MUST bind an approval to the frozen tool action displayed to the user.

#### Scenario: Approve once
- **WHEN** the user selects `approve_once` with the current continuation token
- **THEN** the runtime executes the exact frozen ToolCall once without asking the model to regenerate it

#### Scenario: Reject an action
- **WHEN** the user selects `reject`
- **THEN** the runtime marks the ToolCall rejected, never invokes the tool, and supplies an approval-result observation to the Agent

#### Scenario: Replay or stale token
- **WHEN** a client repeats a consumed decision or submits an invalid continuation token
- **THEN** the system rejects the request without executing the tool

### Requirement: Similar-action grants are narrow and Run-scoped
The system SHALL generate, persist, and evaluate similar-action matchers on the backend, SHALL scope each grant to one Run and tool, and MUST NOT accept a model-supplied matcher.

#### Scenario: Matching later action
- **WHEN** the user selects `allow_similar` and a later frozen action in the same Run matches the generated rule
- **THEN** the runtime executes the later action without another interactive approval

#### Scenario: Action outside grant scope
- **WHEN** an action belongs to another Run, another tool, or does not match the stored rule
- **THEN** the runtime requests a new approval

#### Scenario: Complex shell action
- **WHEN** a Bash command contains shell control, redirection, expansion, or cannot be safely tokenized
- **THEN** the system does not offer a similar-command grant and requires exact one-time approval

### Requirement: Pending approvals survive refresh and restart
The system SHALL expose a safe pending-approval view and SHALL recover the frozen action from persistent state after client refresh or service restart.

#### Scenario: Refresh while waiting
- **WHEN** the client reloads a Run with a pending approval
- **THEN** the Run view contains the approval identifier, safe action preview, available decisions, permission impact, and continuation token needed to restore the approval card

