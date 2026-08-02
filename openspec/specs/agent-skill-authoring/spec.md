# agent-skill-authoring Specification

## Purpose
TBD - created by archiving change add-governed-agent-skills-system. Update Purpose after archive.
## Requirements
### Requirement: Astra provides a dedicated multi-file Skill workbench
The system SHALL provide a dedicated Skill management and authoring interface with a virtual file tree, multiple editor tabs, create/rename/move/delete operations, dirty-state tracking, undo/redo, keyboard navigation, and language-aware editing for `SKILL.md`, scripts, references, assets, JSON, YAML, Markdown, Python, JavaScript, TypeScript, and Shell text files.

#### Scenario: Edit a multi-file custom Skill
- **WHEN** the administrator opens a custom Skill Draft
- **THEN** the workbench presents its directory tree and opens each selected text resource in a URI-stable editor model
- **THEN** edits remain associated with the correct Draft file across tab switches

#### Scenario: Open a built-in Skill
- **WHEN** the administrator opens an Astra built-in Skill
- **THEN** the workbench presents its files in read-only mode
- **THEN** the administrator may clone it to a new custom identity before editing

### Requirement: Monaco is the bounded editing surface
The system SHALL integrate Monaco Editor as a browser code-editing component backed by Astra's virtual Skill filesystem, and MUST NOT expose an unrestricted terminal, VS Code extension host, arbitrary local filesystem, or direct process execution from the authoring interface.

#### Scenario: Edit a script
- **WHEN** the administrator edits a supported script file
- **THEN** the editor provides syntax highlighting, bracket matching, find/replace, and available language diagnostics without running the script

#### Scenario: Request script execution from the workbench
- **WHEN** the administrator chooses to test executable Skill content
- **THEN** the workbench creates an explicit sandboxed test Run
- **THEN** Monaco itself does not launch a process or terminal

### Requirement: Markdown and frontmatter have source and rendered views
The system SHALL provide source editing for the complete `SKILL.md`, a rendered Markdown preview, frontmatter diagnostics, and navigation from a diagnostic to the corresponding source range without making a lossy form representation the source of truth.

#### Scenario: Frontmatter contains an error
- **WHEN** the administrator introduces invalid YAML or an invalid required field
- **THEN** the workbench marks the exact source range and the preview indicates that the Draft is not publishable

#### Scenario: Preview valid instructions
- **WHEN** `SKILL.md` is valid
- **THEN** the preview renders the Markdown body safely without executing embedded HTML, scripts, or remote active content

### Requirement: Draft edits use optimistic concurrency and autosave
The system SHALL autosave custom Skill Draft changes with revision tokens, preserve unsaved local edits during transient failures, and reject stale writes with a recoverable file- or Draft-level conflict.

#### Scenario: Autosave succeeds
- **WHEN** the administrator pauses after editing a Draft file
- **THEN** the changed Draft is persisted with a new revision token and visible saved state

#### Scenario: Editor has a stale Draft revision
- **WHEN** a save uses an outdated revision token
- **THEN** Astra does not overwrite newer content
- **THEN** the workbench offers a three-way comparison or explicit recovery path

### Requirement: Publication creates an immutable validated revision
The system SHALL require an explicit publish action, run complete package and safety validation against one atomic Draft snapshot, show the change summary, and create a new immutable Published Revision only if the validated Draft revision is still current.

#### Scenario: Publish a changed Draft
- **WHEN** the administrator confirms publication of the current valid Draft
- **THEN** Astra records a content digest, publication time, file manifest, diagnostics, and predecessor revision
- **THEN** ordinary Runs begin using the new Published Revision only after publication succeeds

#### Scenario: Draft changes during publication
- **WHEN** the Draft revision changes after validation but before commit
- **THEN** publication fails with a stale-revision conflict and does not publish mixed file content

### Requirement: Workbench provides Diff and revision history
The system SHALL let the administrator compare the Draft with the active Published Revision, inspect historical Published Revisions, restore a historical revision into a new Draft, and export any permitted revision as a portable Skill directory.

#### Scenario: Restore an old revision
- **WHEN** the administrator restores a historical Published Revision
- **THEN** Astra creates or replaces the mutable Draft from that content
- **THEN** the historical revision and current active Published Revision remain immutable

### Requirement: Import and export preserve portable Skill content
The system SHALL support bounded folder or archive import into a custom Draft and portable directory or archive export, using the same path, size, format, and digest validation as platform-created content.

#### Scenario: Import a valid Skill archive
- **WHEN** the administrator imports a valid bounded archive
- **THEN** Astra creates a custom Draft and opens it in the workbench without automatically publishing or executing it

### Requirement: Draft testing is isolated from active publication
The system SHALL let the administrator test an exact Draft snapshot in either quick-response or trusted-execution mode, clearly identify the Run as a Draft test, apply normal tool and sandbox boundaries, and prevent the test snapshot from entering ordinary Run Catalogs.

#### Scenario: Test Draft in quick mode
- **WHEN** the administrator supplies a test request and selects quick response
- **THEN** Astra creates a test Run using the exact Draft snapshot without a TaskContract or DAG

#### Scenario: Test Draft in trusted mode
- **WHEN** the administrator supplies a test request and selects trusted execution
- **THEN** Astra resolves the Draft test Skill before TaskContract and DAG generation and runs the complete trusted lifecycle

#### Scenario: Edit Draft after test starts
- **WHEN** the administrator changes the Draft after a test Run has frozen its snapshot
- **THEN** the running test continues with the original test digest

