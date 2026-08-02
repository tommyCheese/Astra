## ADDED Requirements

### Requirement: Chat composer presents pending tool approvals
The system SHALL render a recoverable approval card immediately above the chat input whenever the current Run has a pending tool approval.

#### Scenario: Display a pending approval
- **WHEN** a Run view or event reports a pending approval
- **THEN** the UI shows the tool name, safe command or input preview, requested permission, impact scope, and the available approval decisions

#### Scenario: Decide an approval
- **WHEN** the user selects `仅本次`, `允许类似命令`, or `拒绝`
- **THEN** the UI submits the corresponding decision once, disables duplicate actions while pending, and refreshes or resumes the Run from the server response

#### Scenario: Similar approval is unsafe
- **WHEN** the pending approval does not include a backend-generated similar matcher
- **THEN** the UI omits the `允许类似命令` action

#### Scenario: Approval card on a narrow viewport
- **WHEN** the chat is displayed on a mobile viewport
- **THEN** the approval summary and all available decisions remain readable and operable without overlapping the composer

