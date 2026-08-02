## ADDED Requirements

### Requirement: Deterministic eligible Skill Catalog
The system SHALL build an immutable eligible Skill Catalog for each Run from Astra-shipped built-in revisions and enabled custom Published Revisions that satisfy compatibility and runtime availability, with deterministic ordering and origin-qualified identities.

#### Scenario: Start a Run with eligible Skills
- **WHEN** a Run is created
- **THEN** its Catalog contains the applicable built-in revisions and globally shared enabled custom Published Revisions
- **THEN** identical inputs produce the same ordered Catalog and digest

#### Scenario: Duplicate unqualified names
- **WHEN** a custom Skill conflicts with another custom identity or the reserved Astra namespace
- **THEN** publication is rejected with an identity conflict
- **THEN** Catalog construction never resolves identity by last-discovered replacement

### Requirement: Three-tier progressive disclosure
The system SHALL disclose Skill content in three tiers: bounded discovery metadata in the initial model context, complete `SKILL.md` instructions only after activation, and individual bundled resources only when requested by an active Skill.

#### Scenario: Model considers available Skills
- **WHEN** a model role plans or decides how to address a request
- **THEN** it receives the name, description, qualified identity, and safe compatibility summary of eligible Skills without receiving full instructions

#### Scenario: Skill becomes active
- **WHEN** the runtime accepts a Skill activation
- **THEN** the complete validated `SKILL.md` body is added in a delimited Skill instruction block together with its root-relative resource inventory

### Requirement: Explicit and model-selected activation
The system SHALL activate a Skill when the user explicitly names an eligible Skill or when the model emits a structured activation decision based on Catalog metadata, and SHALL validate every activation against the frozen Run Catalog.

#### Scenario: User explicitly requests a Skill
- **WHEN** the user unambiguously names one eligible Skill for the task
- **THEN** the runtime activates that exact qualified Skill before the applicable workflow step

#### Scenario: Model selects a relevant Skill
- **WHEN** the model determines that a Catalog description matches the task
- **THEN** it emits the qualified Skill identity through the activation protocol
- **THEN** the runtime loads the instructions only after validating eligibility

#### Scenario: Requested Skill is unavailable
- **WHEN** a user or model requests a Skill absent from the frozen Catalog
- **THEN** activation is rejected with an availability explanation and no package content is loaded

### Requirement: Skill instructions have bounded precedence
The system SHALL treat active Skill content as origin-identified procedural guidance that cannot override platform policy, the frozen Agent Profile, role protocol, explicit current administrator intent, permission decisions, or trusted runtime invariants, and SHALL keep it distinct from untrusted observations and external data.

#### Scenario: Skill claims additional authority
- **WHEN** Skill instructions claim that a tool is pre-approved or that a platform rule may be ignored
- **THEN** the claim does not change tool eligibility, authorization, approval, sandbox, or completion behavior

#### Scenario: Skill conflicts with the current request
- **WHEN** an active Skill prescribes an output or action that conflicts with the administrator's explicit in-scope instruction
- **THEN** the model follows the explicit instruction unless doing so conflicts with higher-level policy

### Requirement: Deterministic multi-Skill composition
The system SHALL allow multiple eligible Skills to be active in one Run, preserve a separately delimited instruction block for each, order them deterministically, and record the reason and initiator for every activation.

#### Scenario: Two complementary Skills activate
- **WHEN** a task requires two non-exclusive Skills
- **THEN** both instruction blocks are available without merging or rewriting their source content
- **THEN** activation order and source identities are recorded

#### Scenario: Active Skills conflict
- **WHEN** active Skills provide incompatible procedural guidance
- **THEN** the runtime exposes each origin, identity, and conflict context to the model
- **THEN** the model does not infer that the later-loaded Skill automatically wins

### Requirement: Run freezes Skill identities and activation state
The system SHALL persist an immutable Run Skill snapshot containing the eligible Catalog digest, activated Skill identities, package digests, normalized discovery metadata, instruction content or a durable content reference, resource manifests, built-in/custom origin, publication revision, and activation history.

#### Scenario: Resume after a Skill update
- **WHEN** an existing Run resumes after an installed Skill changed
- **THEN** the Run continues from its frozen Skill content when the durable snapshot is available
- **THEN** it does not silently switch to the updated package

#### Scenario: Frozen content cannot be reconstructed safely
- **WHEN** required frozen Skill content is unavailable or fails its recorded digest
- **THEN** the affected Run fails closed before further Skill-guided execution

### Requirement: Skill context survives role changes and compaction
The system SHALL reconstruct only the active Skills relevant to each model operation and plan node, preserve activation identities across safe context compaction, and avoid repeatedly injecting inactive or already summarized resources.

#### Scenario: Continue a long-running task after compaction
- **WHEN** conversation or run context is compacted
- **THEN** the active Skill identities and required core instructions remain reconstructable from the Run snapshot
- **THEN** previously loaded resource bodies are reloaded only when still needed

### Requirement: Skill runtime activity is auditable
The system SHALL record Catalog creation, activation request, activation success or rejection, instruction disclosure, resource read, script-related tool invocation attribution, conflict, snapshot drift, and deactivation events with safe Skill identities and digests.

#### Scenario: Inspect a Skill-guided tool call
- **WHEN** a tool call was materially directed by an active Skill
- **THEN** the audit trail links the call to the Run, turn or node, Skill identity, activation record, and applicable permission decision
