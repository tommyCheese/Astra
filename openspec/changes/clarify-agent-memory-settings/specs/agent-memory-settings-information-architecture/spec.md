## ADDED Requirements

### Requirement: Settings separate Agent instructions from runtime infrastructure
The settings UI SHALL provide an Agent category for Profile documents and SHALL keep sandbox environment and dependency management in the Runtime category. The Agent category SHALL explain that Profile text guides model behavior but does not enable capabilities or override enforced runtime settings.

#### Scenario: User edits memory principles
- **WHEN** a user wants to change `MEMORY.md`
- **THEN** the editor is available under Agent settings rather than Runtime infrastructure
- **THEN** the UI states that the document does not enable Memory or bypass runtime controls

### Requirement: Memory settings follow user tasks and progressive disclosure
The Memory category SHALL separate runtime controls, stored Memory management, AutoDream maintenance, and detailed activity audit. The default stored-Memory view SHALL prioritize content, scope, lifecycle, source summary, and revocation while technical recall scores, state versions, history, and raw metadata remain available in the audit view.

#### Scenario: User checks what Astra remembers
- **WHEN** a user opens the stored Memory view
- **THEN** the user can inspect content, type, scope, status, sources, and revoke an eligible record without first interpreting raw audit JSON

#### Scenario: Operator diagnoses a recall
- **WHEN** an operator opens the activity and audit view
- **THEN** the UI exposes recall selection, exclusion reasons, score components, lifecycle events, versions, and provenance metadata

### Requirement: AutoDream is presented as Memory maintenance
The settings UI SHALL present AutoDream as “整理与合并”, explain that it processes stored Memory within a namespace, and keep publication and rollback actions scoped to consolidation generations.

#### Scenario: User reviews an AutoDream job
- **WHEN** a user opens the Memory maintenance view
- **THEN** the UI presents consolidation jobs without implying that AutoDream is another Memory type or that proposals already changed active Memory

### Requirement: Agent evolution is isolated as an experiment
The settings UI SHALL place evolution candidates under an Experimental Agent Improvement category and SHALL scope the production-promotion-disabled notice to that view only.

#### Scenario: User opens Memory settings
- **WHEN** a user views Memory settings or stored Memory
- **THEN** no global production-promotion-disabled badge is displayed

#### Scenario: User opens Agent Improvement
- **WHEN** a user views an evolution candidate
- **THEN** the UI explains that production application is disabled and the candidate is non-executable

