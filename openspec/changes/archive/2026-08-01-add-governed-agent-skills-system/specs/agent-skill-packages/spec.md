## ADDED Requirements

### Requirement: Agent Skills compatible package format
The system SHALL accept a Skill directory whose required `SKILL.md` contains YAML frontmatter followed by Markdown instructions, SHALL require `name` and `description`, and SHALL support optional `license`, `compatibility`, `metadata`, `allowed-tools`, `scripts/`, `references/`, and `assets/` content without requiring an Astra-specific manifest.

#### Scenario: Import a minimal compatible Skill
- **WHEN** a user imports a directory whose name matches a valid frontmatter `name` and whose `SKILL.md` contains a non-empty valid `description`
- **THEN** the system accepts the package and inventories its instructions and bundled resources

#### Scenario: Preserve portable optional fields
- **WHEN** a compatible Skill includes optional standard frontmatter and resource directories
- **THEN** the system preserves those fields and resources so the package remains exportable in the open format

### Requirement: Strict and explainable package validation
The system MUST validate Skill names, directory-name agreement, frontmatter types and limits, UTF-8 decoding, file count, individual and total sizes, supported file kinds, and compatibility declarations before installation, and SHALL return path-specific diagnostics without executing package content.

#### Scenario: Invalid frontmatter
- **WHEN** `SKILL.md` is missing required metadata or contains a name that violates the open-format naming constraints
- **THEN** installation is rejected with a safe diagnostic identifying the invalid field

#### Scenario: Oversized package
- **WHEN** a package exceeds a configured file, byte, or instruction-token limit
- **THEN** installation is rejected before any content is added to the active Catalog

### Requirement: Package paths remain confined to the Skill root
The system MUST normalize every package path, reject absolute paths, traversal, device files, hard-link ambiguity, and symbolic links that resolve outside the Skill root, and MUST NOT read content outside that root while parsing or serving a Skill resource.

#### Scenario: Resource escapes through a symbolic link
- **WHEN** a bundled reference or script resolves outside the imported Skill directory
- **THEN** the package is rejected and the external target is not read

#### Scenario: Instruction references a traversal path
- **WHEN** an active Skill requests a resource path containing traversal outside its root
- **THEN** the resource read is denied and an audit event records a redacted reason

### Requirement: Canonical Skill identity and content digest
The system SHALL assign each Skill an origin-qualified identity that distinguishes Astra built-in and custom content, normalized version metadata when available, and a deterministic digest covering `SKILL.md` and every inventoried resource path and byte sequence.

#### Scenario: Equivalent package is re-imported
- **WHEN** the same normalized custom Skill content is imported again under the same identity
- **THEN** the system derives the same content digest and can report that no content update occurred

#### Scenario: Bundled script changes
- **WHEN** any byte of an inventoried script changes
- **THEN** the Skill content digest changes even if `SKILL.md` metadata is unchanged

### Requirement: Resources are inventoried without eager disclosure
The system SHALL store a bounded manifest of bundled resource paths, media types, sizes, and digests at import time, but SHALL NOT place script, reference, or asset bodies into model context until the active workflow requests a specific resource.

#### Scenario: Discover a Skill with many references
- **WHEN** the Skill enters an eligible Catalog
- **THEN** only its discovery metadata is exposed and reference bodies consume no model context

#### Scenario: Load one referenced file
- **WHEN** an active Skill requests one valid reference needed by the current task
- **THEN** only that resource is read into the bounded activation context and the read is auditable

### Requirement: Astra management metadata remains external to portable packages
The system SHALL store built-in/custom origin, Draft state, Published Revision history, enabled state, safety diagnostics, publication time, and pinned digest outside the portable Skill directory.

#### Scenario: Export an installed Skill
- **WHEN** the administrator exports a custom Skill or a readable built-in Skill
- **THEN** the exported Skill remains a valid open-format directory without Astra database state embedded in `SKILL.md`
