# agent-profile-runtime-composition Specification

## Purpose
TBD - created by archiving change centralize-astra-agent-profile. Update Purpose after archive.
## Requirements
### Requirement: Model roles use centralized prompt composition
The system SHALL construct model system prompts through one Prompt Composer and SHALL remove duplicated Astra identity and behavioral definitions from individual planner, contract, controller, answer, reflector, and memory-extraction call sites.

#### Scenario: Compose a controller or final-answer prompt
- **WHEN** the controller or user-facing answer role is invoked
- **THEN** the composed system prompt includes the applicable `IDENTITY.md` and `SOUL.md` content exactly once
- **THEN** role-specific output schema and decision instructions remain explicit

#### Scenario: Compose a memory-extraction prompt
- **WHEN** the memory extractor is invoked
- **THEN** the composed prompt includes the applicable `MEMORY.md` governance content
- **THEN** it does not include `AUTODREAM.md`

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

