## MODIFIED Requirements

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

## ADDED Requirements

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

