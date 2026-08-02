# agent-profile-management Specification

## Purpose
TBD - created by archiving change centralize-astra-agent-profile. Update Purpose after archive.
## Requirements
### Requirement: Canonical Agent Profile is version-controlled and packaged
The system SHALL define the default Astra Agent Profile with `IDENTITY.md`, `SOUL.md`, `MEMORY.md`, and `AUTODREAM.md` under the backend application package, SHALL track those files in Git, and SHALL include them in source, wheel, test, and container release artifacts without relying on the process working directory.

#### Scenario: Load packaged profile outside the repository root
- **WHEN** the backend is started from a working directory other than `backend`
- **THEN** the Profile loader resolves all canonical documents from Python package resources
- **THEN** the loaded content is identical to the Git-managed source documents

#### Scenario: Build a distributable backend package
- **WHEN** the backend package or container artifact is built
- **THEN** all required Profile documents are present in the artifact

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

### Requirement: Canonical documents are validated before use
The system SHALL validate required document presence, UTF-8 decoding, supported schema metadata, non-empty required sections, normalized line endings, and configured size limits before composing any model prompt.

#### Scenario: Required document is missing or malformed
- **WHEN** a required Profile document is unavailable, empty, undecodable, or fails schema validation
- **THEN** the system raises a typed configuration error before issuing a model request
- **THEN** it does not silently substitute a partial or stale identity

### Requirement: Profile versions are deterministic
The system SHALL derive a deterministic Profile version from the normalized content and composition schema version, and SHALL expose per-document hashes in a stable manifest.

#### Scenario: Identical content is loaded twice
- **WHEN** two processes load byte-equivalent Profile documents with the same composition schema
- **THEN** they produce the same Profile version and per-document hashes

#### Scenario: A canonical document changes
- **WHEN** any normalized canonical document content changes
- **THEN** the derived Profile version changes

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

