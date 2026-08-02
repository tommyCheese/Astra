# explicit-skill-activation Specification

## Purpose
TBD - created by archiving change add-slash-skill-activation. Update Purpose after archive.
## Requirements
### Requirement: Slash-selected Skills are explicit Run bindings
The system SHALL represent every Skill selected through the Composer slash command as a deduplicated origin-qualified identity in the Run creation `skill_ids` field, and MUST NOT rely on parsing the user message to recover that binding.

#### Scenario: User selects one Skill
- **WHEN** the user selects an eligible Skill from the slash command panel and sends the message
- **THEN** the Run creation request contains that Skill's exact qualified identity in `skill_ids`
- **THEN** the slash query is absent from the user message content

#### Scenario: User selects multiple Skills
- **WHEN** the user selects multiple eligible Skills before sending
- **THEN** every selected qualified identity appears exactly once in `skill_ids`
- **THEN** the request preserves a stable selection order

### Requirement: Explicit Skill bindings activate before model execution
The system MUST validate and activate every explicitly bound Skill against the newly frozen Run Catalog before the first model operation, and a model decision MUST NOT skip, replace, or deactivate a user-selected Skill.

#### Scenario: All explicit Skills are eligible
- **WHEN** Run creation includes eligible `skill_ids`
- **THEN** the Run Skill snapshot records every identity as active with initiator `explicit`
- **THEN** the first applicable model operation receives the complete revision-bound instruction blocks

#### Scenario: Model would otherwise finalize directly
- **WHEN** an explicit Skill is bound and the model could answer without autonomously requesting activation
- **THEN** the host still activates and binds the Skill before allowing that answer operation

### Requirement: Explicit activation fails closed
The system SHALL reject Run creation when any explicitly selected identity is absent from the frozen Catalog, disabled, revoked, incompatible, unreconstructable, or outside the activation budget, and MUST NOT continue with a partially activated model Run.

#### Scenario: Selected Skill becomes unavailable before submission
- **WHEN** a Skill selected in the Composer is no longer eligible when the Run Catalog is frozen
- **THEN** Run creation returns a safe actionable validation error identifying the unavailable qualified identity
- **THEN** no model operation starts for that Run

#### Scenario: One of multiple selected Skills fails activation
- **WHEN** at least one explicit identity cannot be activated
- **THEN** the request fails atomically instead of running with the remaining subset

### Requirement: Explicit activation is revision-bound and auditable
The system SHALL associate each explicit selection with the exact frozen Published Revision and digest and SHALL emit enough ordered audit data to prove activation completed before the first model operation.

#### Scenario: Selected Skill is republished after Run creation
- **WHEN** an explicitly selected Skill receives a newer active revision after the Run has been created
- **THEN** the existing Run continues with the revision and digest frozen during its creation

#### Scenario: Inspect explicit activation
- **WHEN** an administrator inspects the Run audit
- **THEN** the audit identifies the selected qualified identity, frozen revision, digest, `explicit` initiator, activation result, and bound model operation

