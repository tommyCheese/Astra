## MODIFIED Requirements

### Requirement: Profile documents have non-overlapping responsibilities
The canonical documents SHALL keep stable product identity and goals in `IDENTITY.md`, behavioral values and communication principles in `SOUL.md`, memory governance in `MEMORY.md`, and the background-only memory-consolidation governance protocol in `AUTODREAM.md`.

#### Scenario: Review a capability-dependent statement
- **WHEN** a Profile document describes Astra's ability to act
- **THEN** it describes the ability as conditional on runtime-provided authorization
- **THEN** it does not assert that a particular tool, model, provider, sandbox, dependency, credential, scheduler, or network capability is currently available

#### Scenario: Review a user-specific fact
- **WHEN** Astra learns a user preference, workspace fact, Run observation, procedure, failure pattern, or source summary
- **THEN** that fact is stored as scoped database Memory or a governed evolution candidate with provenance and confidence
- **THEN** no canonical Profile document is modified

### Requirement: Static governance and dynamic state remain separate
The system SHALL treat Git-managed Profile documents as the authority for stable identity and governance, the database as the authority for actual Memory, consolidation jobs, evolution candidates, and Run history, and runtime capability resolution as the authority for currently executable actions.

#### Scenario: AutoDream protocol exists but scheduling is disabled
- **WHEN** `AUTODREAM.md` is active in the packaged Profile and runtime AutoDream scheduling is disabled
- **THEN** its presence does not schedule background work, mutate Memory, modify Profile documents, or grant any Tool permission

#### Scenario: AutoDream job is explicitly enabled
- **WHEN** an authorized background consolidation job runs
- **THEN** it uses the packaged `AUTODREAM.md` protocol through the dedicated model operation
- **THEN** its actual state, proposals, validation, publication, and model usage are persisted in database audit records

#### Scenario: Runtime state is persisted
- **WHEN** a Tool setting, Run result, model usage record, actual Memory, consolidation generation, or evolution candidate changes
- **THEN** the change is persisted through its database or runtime configuration path rather than written into a canonical Profile document

