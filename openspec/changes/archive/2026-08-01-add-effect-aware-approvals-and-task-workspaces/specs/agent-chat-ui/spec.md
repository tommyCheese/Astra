## ADDED Requirements

### Requirement: Execution mode descriptions reflect effect policy
The chat UI SHALL explain execution modes in terms of side effects and approval behavior rather than whether tools are called.

#### Scenario: Plan-only description
- **WHEN** the user views the plan-only option
- **THEN** the UI explains that Astra may research and analyze with safe tools but will not perform any persistent or external side-effect action

### Requirement: Approval panels describe actions and scopes
The chat UI SHALL present pending approvals using a human-readable action summary, affected resources, risk reason, working directory, network scope, and available grant scopes.

#### Scenario: Approve a file creation
- **WHEN** a tool proposes creating `reports/summary.md`
- **THEN** the panel states that a persistent file will be created and offers allow-once plus any safe Run- or Task-scoped grant proposals

#### Scenario: Approval panel stays user-facing
- **WHEN** an approval has internal permissions, URIs, working-directory metadata, or a long command preview
- **THEN** the default panel shows the human-readable action, affected file or service, practical risk, approval scopes, and the exact Bash command when applicable, without exposing raw permission identifiers or internal resource URIs

#### Scenario: Explicit Task grant
- **WHEN** a Task-scoped proposal is available
- **THEN** its button clearly states that permission continues across later requests in the current Task

#### Scenario: Similar scope is unsafe
- **WHEN** the backend cannot produce a narrow resource and invocation matcher
- **THEN** the UI omits similar Run and Task actions and offers only allow-once and reject

### Requirement: Results show Workspace changes and deliverables
The chat UI SHALL show meaningful files created, modified, and deleted by the current Run and SHALL provide safe previews or downloads for eligible deliverables.

#### Scenario: Run produces multiple file types
- **WHEN** a Run creates source, Markdown, data, and image files
- **THEN** the result groups them coherently and previews supported files without hiding the rest of the change summary
