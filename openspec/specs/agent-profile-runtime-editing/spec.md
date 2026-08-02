# agent-profile-runtime-editing Specification

## Purpose
TBD - created by archiving change expose-agent-profile-runtime-config. Update Purpose after archive.
## Requirements
### Requirement: Runtime exposes the active editable Agent Profile
The system SHALL expose the active Agent Profile through the local Runtime configuration API with its source, deterministic version, and the complete editable content of `IDENTITY.md`, `SOUL.md`, `MEMORY.md`, and `AUTODREAM.md`.

#### Scenario: No user override exists
- **WHEN** a local user reads Runtime configuration before saving a Profile override
- **THEN** the response contains the packaged default documents
- **THEN** the Profile source is identified as `default`

#### Scenario: A user override is active
- **WHEN** a local user reads Runtime configuration after activating a valid override
- **THEN** the response contains the normalized active override documents and their derived version
- **THEN** the Profile source is identified as `user`

### Requirement: Users can atomically validate and activate Profile documents
The system SHALL accept only a complete set of editable Profile documents, SHALL validate the full set with the canonical Agent Profile loader, and SHALL atomically persist and activate the normalized Profile.

#### Scenario: Save a valid Profile
- **WHEN** a local user submits all required Profile documents with valid metadata, required sections, and size limits
- **THEN** the system persists the normalized documents as one Runtime configuration update
- **THEN** subsequent reads and newly created Runs use the newly derived Profile version

#### Scenario: Reject an invalid Profile
- **WHEN** any submitted document is missing, malformed, empty, oversized, or violates a required section or metadata rule
- **THEN** the system returns a typed validation error
- **THEN** neither the persisted nor in-memory active Profile changes

### Requirement: Users can restore the packaged default Profile
The system SHALL allow a local user to remove the active user override and immediately restore the packaged Profile as the source for subsequent Runs.

#### Scenario: Restore defaults
- **WHEN** a local user confirms restoring the default Profile
- **THEN** the persisted user override is removed atomically
- **THEN** the Runtime API reports the packaged Profile with source `default`

### Requirement: Runtime settings provide a Profile editor
The Runtime settings UI SHALL provide separate Markdown editing controls for each Profile document, SHALL indicate unsaved changes and validation failures, and SHALL provide save and restore-default actions.

#### Scenario: Edit and save a document
- **WHEN** a user changes one Profile document and selects save
- **THEN** the UI submits the complete document set
- **THEN** a successful response replaces the editor state and displays the active version

#### Scenario: Save fails validation
- **WHEN** the server rejects the edited document set
- **THEN** the UI preserves the user's unsaved text and presents the validation error

