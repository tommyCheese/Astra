## ADDED Requirements

### Requirement: Prompt composition has a distinct Skill instruction layer
The system SHALL compose active Skill instructions through the centralized Prompt Composer as individually delimited, source-identified procedural guidance after platform Profile and role protocol, and SHALL keep Skill content distinct from the current user request and untrusted runtime context.

#### Scenario: Compose a role with an active Skill
- **WHEN** a model operation requires an active Skill
- **THEN** the composed context identifies the Skill name, qualified identity, digest, built-in/custom origin, Published Revision or test snapshot, and root for relative resources
- **THEN** the Skill body is not represented as Agent Profile or role protocol

#### Scenario: Compose a role with no relevant Skill
- **WHEN** a model operation does not require any active Skill
- **THEN** the Prompt Composer omits full Skill instructions from that operation

### Requirement: Prompt hierarchy constrains Skill instructions
The system SHALL explicitly state that Skill instructions cannot override platform policy, Agent Profile, role protocol, explicit administrator intent, permission gates, or runtime capability facts, and SHALL keep conflicting Skill blocks individually attributable rather than applying load-order precedence.

#### Scenario: Skill contains instruction-like policy text
- **WHEN** a Skill claims to redefine Astra's identity, permissions, or trusted role protocol
- **THEN** Prompt Composer framing prevents that text from being treated as the corresponding higher-priority layer

### Requirement: Skill composition is token-conscious and auditable
The system SHALL include only activated Skills applicable to the current model operation, SHALL preserve each Skill's origin and revision boundary, and SHALL associate the composed operation with the frozen activation records without exposing full Skill contents in normal Run summaries.

#### Scenario: Inspect normal Run metadata
- **WHEN** a client retrieves a Run that used Skills
- **THEN** it may receive safe Skill identities, versions, digests, activation reasons, and resource names
- **THEN** it does not receive full instructions or script bodies unless the dedicated Skill or audit detail API is used
