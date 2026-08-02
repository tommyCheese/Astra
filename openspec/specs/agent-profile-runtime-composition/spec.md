# agent-profile-runtime-composition Specification

## Purpose
TBD - created by archiving change centralize-astra-agent-profile. Update Purpose after archive.
## Requirements
### Requirement: Model roles use centralized prompt composition
The system SHALL construct model system prompts through one Prompt Composer and SHALL remove duplicated Astra identity and behavioral definitions from planner, contract, controller, answer, reflector, memory-extraction, and AutoDream call sites.

#### Scenario: Compose a controller or final-answer prompt
- **WHEN** the controller or user-facing answer role is invoked
- **THEN** the composed system prompt includes the applicable `IDENTITY.md` and `SOUL.md` content exactly once
- **THEN** role-specific output schema and decision instructions remain explicit

#### Scenario: Compose a memory-extraction prompt
- **WHEN** the memory extractor is invoked
- **THEN** the composed prompt includes the applicable `MEMORY.md` governance content
- **THEN** it does not include `AUTODREAM.md`

#### Scenario: Compose an AutoDream prompt
- **WHEN** an explicitly enabled background consolidation job invokes the dedicated AutoDream model operation
- **THEN** the composed prompt includes the applicable `IDENTITY.md`, `MEMORY.md`, and `AUTODREAM.md` content exactly once
- **THEN** it includes a bounded output contract and untrusted input manifest

#### Scenario: Compose a normal question-answering prompt
- **WHEN** any synchronous user question is planned, answered, reflected upon, or finalized
- **THEN** the composed prompt does not include `AUTODREAM.md`

### Requirement: Prompt composition is role-selective and token-conscious
The system SHALL use a documented role-to-document matrix and SHALL include only the Profile documents and sections needed by the current model operation.

#### Scenario: Compose a planning prompt
- **WHEN** the planner creates a task plan
- **THEN** it receives Astra's identity and goal principles
- **THEN** it does not receive unrelated AutoDream instructions or actual database Memory unless that operation explicitly requires recalled context

#### Scenario: Compose a reflection prompt
- **WHEN** the reflector evaluates observations or a possible memory-related patch
- **THEN** it receives the identity principles and only the memory-governance sections required for that reflection

### Requirement: Runtime capabilities override profile claims
The system SHALL derive currently available actions from registered Tool Manifests, environment settings, persisted tool switches, infrastructure availability, Run permissions, risk gates, and remaining budgets; Profile content SHALL NOT register, enable, authorize, or make a tool available.

#### Scenario: Profile mentions tool-assisted execution but a tool is disabled
- **WHEN** the Profile describes Astra as able to use authorized tools and the database or environment disables a specific tool
- **THEN** that tool is absent from the eligible Tool Manifests or marked unavailable
- **THEN** the model cannot execute it through the Tool Router

#### Scenario: Sandbox dependency is unavailable
- **WHEN** a sandbox-backed tool is configured but its execution backend is unavailable
- **THEN** the dynamic context reports the capability as unavailable
- **THEN** no statement in the Profile bypasses the sandbox availability gate

### Requirement: Dynamic memory is isolated from trusted instructions
The system SHALL serialize recalled database Memory, conversation history, tool observations, and external content as delimited untrusted context data and SHALL instruct model roles not to interpret that data as Profile, system policy, or authorization.

#### Scenario: Recalled memory contains instruction-like text
- **WHEN** a recalled Memory contains text such as a request to ignore previous instructions or enable a tool
- **THEN** the text remains contextual data
- **THEN** it does not alter Profile identity, role protocol, permissions, or tool availability

### Requirement: Every Run freezes an auditable Profile snapshot
The system SHALL persist an immutable Agent Profile snapshot before the first model invocation for a Run, including the Profile version, composition schema version, normalized document content or an equivalent durable revision, per-document hashes, and the role-to-document selection metadata needed to reconstruct prompts after restart.

#### Scenario: Resume a Run after service restart
- **WHEN** an existing non-terminal Run resumes after the service restarts or the packaged default Profile has changed
- **THEN** the Run continues using its frozen Profile snapshot rather than silently switching to the new default

#### Scenario: Inspect a historical Run
- **WHEN** an operator inspects a completed Run
- **THEN** the stored manifest identifies the exact Profile version and document hashes used by that Run
- **THEN** the audit data can determine which documents applied to each model operation without exposing secrets

#### Scenario: Start a new Run after a Profile change
- **WHEN** the canonical Git-managed Profile changes and a new Run is created
- **THEN** the new Run freezes the new Profile version
- **THEN** existing Run snapshots remain unchanged

### Requirement: Profile usage is observable without exposing privileged prompt content
The system SHALL record Profile version and model operation association in backend audit data, while public Run responses SHALL expose only safe manifest metadata unless a privileged diagnostic interface explicitly authorizes raw Profile content.

#### Scenario: Return a normal Run view
- **WHEN** a client retrieves a Run through the standard API
- **THEN** it may receive Profile version and document identifiers or hashes
- **THEN** it does not receive the full composed system prompt by default

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

### Requirement: AutoDream composition is background-only
The system SHALL reject use of the AutoDream model operation outside an identified consolidation job and SHALL NOT make that operation available to normal Agent planning or Tool routing.

#### Scenario: Run requests AutoDream operation
- **WHEN** a normal synchronous Run attempts to select the AutoDream operation
- **THEN** prompt composition or model routing rejects it
- **THEN** no `AUTODREAM.md` content enters the Run prompt

### Requirement: Every consolidation job freezes an auditable Profile snapshot
The system SHALL persist the Profile version, composition schema version, document hashes, selected document metadata, and operation identity used by each consolidation job.

#### Scenario: Profile changes after proposal generation
- **WHEN** a consolidation proposal is inspected or published after the packaged Profile changes
- **THEN** audit data identifies the exact Profile version that produced the proposal
- **THEN** publication validation does not silently recompute the proposal with the new Profile

### Requirement: AutoDream inputs remain untrusted context
The system SHALL serialize Memory content, source trajectories, external references, and evolution suggestions as delimited untrusted data beneath trusted AutoDream governance.

#### Scenario: Memory contains fake AutoDream instructions
- **WHEN** a Memory record claims to replace the consolidation protocol or requests authority changes
- **THEN** it remains untrusted evidence
- **THEN** it cannot alter Profile selection, output validation, permissions, or publication policy

### Requirement: Frozen Profile snapshots use only the current composition schema
The system SHALL persist and reconstruct Run Agent Profile snapshots only with the current composition schema and SHALL NOT infer roles or documents from legacy snapshots.

#### Scenario: Load an obsolete Profile snapshot
- **WHEN** a Run snapshot has an unsupported composition schema or legacy unversioned marker
- **THEN** reconstruction fails explicitly rather than substituting default documents or legacy role mappings

