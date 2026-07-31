## ADDED Requirements

### Requirement: Local users can manage enforced Memory runtime settings
The system SHALL expose a validated local Runtime API for Memory write enablement, cross-Session recall mode, recall item and token budgets, minimum confidence and relevance score, AutoDream enablement, scan interval, and minimum candidate count. The system SHALL persist valid updates atomically and apply them to subsequent runtime work.

#### Scenario: Enable shadow cross-Session recall
- **WHEN** a local user saves cross-Session mode `shadow`
- **THEN** subsequent recall records candidate and selection decisions without injecting selected Memory into model context
- **THEN** the persisted setting survives application restart

#### Scenario: Reject invalid Memory settings
- **WHEN** a user submits a value outside the configured safe bounds or an unsupported recall mode
- **THEN** the system returns a typed validation error
- **THEN** neither persisted nor in-memory Memory settings change

### Requirement: AutoDream lifecycle follows runtime configuration
The system SHALL start the AutoDream scanner when a valid runtime update enables it and SHALL stop the scanner when a runtime update disables it without deleting persisted consolidation jobs or source evidence.

#### Scenario: Enable AutoDream after application startup
- **WHEN** AutoDream is disabled at startup and a local user enables it
- **THEN** the background scanner starts without restarting the application

#### Scenario: Disable a running AutoDream scanner
- **WHEN** a local user disables AutoDream
- **THEN** the scanner stops scheduling new work
- **THEN** persisted jobs and audit records remain available
