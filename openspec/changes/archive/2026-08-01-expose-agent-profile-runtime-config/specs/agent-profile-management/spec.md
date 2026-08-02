## MODIFIED Requirements

### Requirement: Static governance and dynamic state remain separate
The system SHALL treat Git-managed Profile documents as the packaged default for stable identity and governance, an optional validated local Runtime override as the active authority for subsequent new Runs, the database as the authority for actual memories and run history, and runtime capability resolution as the authority for currently executable actions. Every Run SHALL retain the complete immutable Profile snapshot that was active when the Run was created or first bound.

#### Scenario: AutoDream protocol exists but no worker is enabled
- **WHEN** `AUTODREAM.md` is present in either the packaged or active user Profile
- **THEN** its presence does not schedule background work, mutate memories, modify Profile documents, or grant any tool permission

#### Scenario: Runtime state is persisted
- **WHEN** a tool setting, run result, model usage record, or actual Memory changes
- **THEN** the change is persisted through its existing database or runtime configuration path rather than written into a Profile document

#### Scenario: A new Run starts after a Profile update
- **WHEN** a valid user Profile is activated before a new Run is created
- **THEN** the new Run freezes the active user Profile as its immutable snapshot
- **THEN** existing and running Runs keep their previously frozen Profile snapshots

#### Scenario: No user Profile has been activated
- **WHEN** a new Run is created without a local Runtime Profile override
- **THEN** the Run freezes the Git-managed packaged Profile
