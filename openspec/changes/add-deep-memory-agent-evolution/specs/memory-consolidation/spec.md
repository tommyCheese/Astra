## ADDED Requirements

### Requirement: AutoDream is explicitly enabled and bounded
The system SHALL keep AutoDream scheduling disabled by default and SHALL expose validated limits for scan interval, cooldown, minimum candidate count, records per job, model calls, and lease duration.

#### Scenario: Backend starts with AutoDream disabled
- **WHEN** the application starts with AutoDream scheduling disabled
- **THEN** it does not create or execute consolidation jobs
- **THEN** normal Memory extraction and recall continue independently

#### Scenario: Eligible namespace exceeds bounds
- **WHEN** an enabled scan finds more candidate records than one job permits
- **THEN** it selects a deterministic bounded working region
- **THEN** remaining records are left for later jobs

### Requirement: Consolidation input is immutable and reproducible
The system SHALL freeze an input manifest containing namespace, Memory IDs, versions, hashes, lifecycle state, and provenance references before consolidation begins.

#### Scenario: Source changes during a job
- **WHEN** an input Memory version or lifecycle state changes after the manifest is frozen
- **THEN** publication fails its expected-version validation
- **THEN** the job records a conflict without overwriting the newer state

### Requirement: Consolidation produces proposals before publication
The system SHALL persist consolidation output as a proposed generation containing additions, replacements, supersessions, links, and validation results before changing the active Memory projection.

#### Scenario: Model proposes a compact replacement set
- **WHEN** AutoDream identifies duplicates and a reusable procedure across multiple Sessions
- **THEN** it creates a proposed generation linked to every contributing Memory and source trajectory
- **THEN** active Memory remains unchanged until publication succeeds

#### Scenario: Proposal lacks source coverage
- **WHEN** a synthesized claim has no supporting source or input Memory
- **THEN** validation rejects or quarantines that operation
- **THEN** it is not published as active Memory

### Requirement: Publication is atomic and reversible
The system SHALL publish a validated consolidation generation in one transaction, SHALL supersede replaced versions without deleting them, and SHALL support audited rollback.

#### Scenario: Publication fails midway
- **WHEN** any replacement, source link, or lifecycle transition fails validation or persistence
- **THEN** no partial generation becomes active

#### Scenario: Roll back published generation
- **WHEN** an authorized operator rolls back a published generation
- **THEN** the system restores the prior active projection through audited lifecycle transitions
- **THEN** source evidence and both generation manifests remain intact

### Requirement: Consolidation is namespace and authority isolated
The system SHALL restrict every job to one explicit namespace and SHALL prohibit consolidation from modifying source evidence, canonical Profile content, permissions, credentials, Tool availability, installed Skills, or security policy.

#### Scenario: Consolidation output contains instruction-like authority change
- **WHEN** a proposed output requests a new permission, tool, credential, Profile rule, or security exception
- **THEN** validation rejects the operation
- **THEN** the request cannot be interpreted as an authorized evolution candidate

#### Scenario: Input references another namespace
- **WHEN** a source or Memory reference is outside the job namespace without an explicit authorized sharing grant
- **THEN** the job excludes it and records the isolation decision

### Requirement: Consolidation jobs are recoverable and observable
The system SHALL persist job state, lease, attempts, timestamps, input and output manifests, validation, failure reason, Profile version, model usage, publication result, and rollback relation.

#### Scenario: Process restarts during consolidation
- **WHEN** the backend starts and finds a running job with an expired lease
- **THEN** it marks the attempt interrupted or safely reacquires it according to its idempotency state
- **THEN** it does not publish duplicate generations

#### Scenario: Inspect completed job
- **WHEN** an authorized operator opens a consolidation job
- **THEN** the API exposes audit-safe inputs, outputs, validation, model usage, and active-generation effects

### Requirement: AutoDream can run deterministically without an external model
The system SHALL allow a deterministic consolidation implementation for validation, tests, and deployments without a configured consolidation model, using the same manifest, proposal, validation, and publication contracts.

#### Scenario: Deterministic duplicate consolidation
- **WHEN** two eligible Memory candidates have the same normalized stable key and equivalent content
- **THEN** the deterministic consolidator may propose one replacement linked to both sources
- **THEN** the proposal follows the same review and publication path as model output

