## ADDED Requirements

### Requirement: ToolCalls record file creation, modification, and deletion
The system SHALL compare bounded Workspace manifests around write-capable ToolCalls and persist created, modified, and deleted file records with Run and ToolCall provenance.

#### Scenario: File is deleted
- **WHEN** an approved tool deletes a Workspace file
- **THEN** the system persists a deletion tombstone even though the file is absent from the final directory

#### Scenario: Tool creates an image
- **WHEN** an approved tool creates a valid image in the Workspace
- **THEN** the image appears in the Run change summary and may be promoted to a previewable Artifact

### Requirement: Workspace files and deliverable Artifacts are distinct
The system SHALL track all bounded Workspace files while exposing only security-checked, policy-eligible files as previewable or downloadable Artifacts.

#### Scenario: Dependency files are generated
- **WHEN** dependency installation creates cache, virtual-environment, or package-directory files
- **THEN** those files remain usable by the Task runtime but do not flood the normal user deliverable list

#### Scenario: Unsupported or unsafe preview
- **WHEN** a Workspace file cannot be safely rendered
- **THEN** the system shows safe metadata or a controlled download option instead of executing active content

