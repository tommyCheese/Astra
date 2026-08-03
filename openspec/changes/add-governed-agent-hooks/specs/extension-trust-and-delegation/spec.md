## ADDED Requirements

### Requirement: Executable Hook sources are verified and immutable
Every executable Hook manifest and handler SHALL have a verified source identity, version, content digest, trust tier, maximum decision capabilities, maximum effects, and immutable installed content; Task Workspace content MUST NOT become an executable Hook discovery source.

#### Scenario: Installed handler changes after review
- **WHEN** a Hook script, endpoint identity, schema, selector, requested capability, configuration revision, or handler digest changes after trust was granted
- **THEN** new executions are blocked until policy accepts the new identity and affected pending Runs do not silently switch behavior

#### Scenario: Agent edits a Workspace Hook script
- **WHEN** an Agent creates or modifies a Hook configuration or script inside its Task Workspace
- **THEN** the file does not alter the active Hook Catalog or execute in the current Run

### Requirement: Managed policy controls Hook sources and scope
Administrators SHALL be able to require managed-only Hooks, restrict allowed handler types and HTTP origins, cap Hook effects and data labels, and prevent user, component, imported, or project-local definitions from widening managed policy.

#### Scenario: User Hook requests protected data
- **WHEN** a user-scoped Hook requests data labels or references prohibited by managed policy
- **THEN** the effective binding excludes that access or the Hook is disabled with a safe diagnostic

#### Scenario: Managed-only mode is enabled
- **WHEN** organization policy enables managed-only Hooks
- **THEN** user, imported, Skill, Agent, and project Hook candidates do not enter the executable Catalog unless delivered through an approved managed source

