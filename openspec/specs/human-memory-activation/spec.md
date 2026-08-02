# human-memory-activation Specification

## Purpose
TBD - created by archiving change require-human-memory-activation. Update Purpose after archive.
## Requirements
### Requirement: Extracted Memory requires human activation
The system SHALL store every newly extracted ordinary Memory record as `candidate` and SHALL NOT make it eligible for recall until a human operator explicitly activates it.

#### Scenario: Extractor creates a review candidate
- **WHEN** the model returns a valid sourced Memory candidate and Memory writing is enabled
- **THEN** the system stores the record with `status=candidate`
- **THEN** the system records candidate creation in the Run and Memory audit trails
- **THEN** the system does not automatically transition the record to `active`

#### Scenario: Candidate is excluded from recall
- **WHEN** a later request searches a matching namespace before human activation
- **THEN** the candidate is excluded by lifecycle filtering and is not injected into model context

### Requirement: Human operator can activate a candidate
The system SHALL provide an explicit human activation operation that accepts the expected state version, actor, and reason, validates the candidate and its accessible provenance, and records the transition audit.

#### Scenario: Successful human activation
- **WHEN** a local operator confirms a candidate using its current state version and supplies an audit reason
- **THEN** the system transitions the candidate to `active`
- **THEN** the audit identifies the human actor and reason
- **THEN** the record becomes eligible for subsequent recall subject to normal scope, threshold, validity, and budget filters

#### Scenario: Activation uses stale state
- **WHEN** an operator activates a candidate with an outdated state version
- **THEN** the system rejects the request as a state conflict
- **THEN** no Memory lifecycle state is changed

#### Scenario: Candidate has no accessible source
- **WHEN** an operator attempts to activate a candidate whose sources are all inaccessible or revoked
- **THEN** the system rejects activation
- **THEN** the candidate remains inactive

### Requirement: Human operator can reject a candidate
The system SHALL allow a human operator to reject a candidate by transitioning it to `revoked` with an actor and reason, while retaining its sources and audit history.

#### Scenario: Reject pending candidate
- **WHEN** a local operator rejects a candidate using its current state version and supplies a reason
- **THEN** the candidate becomes `revoked`
- **THEN** it remains excluded from recall and its audit history remains inspectable

### Requirement: Candidate replacement preserves the active version until approval
The system SHALL keep an existing active stable-key version eligible for recall while a changed replacement waits as a candidate, and SHALL supersede the prior active version atomically only when the replacement is human-activated.

#### Scenario: Changed stable-key value awaits review
- **WHEN** extraction produces changed content for a stable key that already has an active version
- **THEN** the changed content is stored as a candidate replacement
- **THEN** the existing active version remains active and recallable

#### Scenario: Replacement is activated
- **WHEN** a local operator activates a valid replacement candidate and its base active version is unchanged
- **THEN** the system atomically marks the base version `superseded` and the candidate `active`
- **THEN** subsequent recall considers the new version instead of the superseded version

### Requirement: Pending Memory has a dedicated confirmation list
The system SHALL provide a human confirmation list containing candidate Memory records and SHALL expose their content, scope, kind, confidence, importance, provenance, version relationship, and audit state before a decision.

#### Scenario: Operator opens pending confirmation list
- **WHEN** the operator opens the pending Memory confirmation view
- **THEN** the system lists candidate records separately from active and historical records
- **THEN** selecting a candidate exposes activation and rejection actions

#### Scenario: Operator records a decision reason
- **WHEN** the operator confirms activation or rejection
- **THEN** the UI requires a non-trivial reason and submits it with the current state version and local operator identity

