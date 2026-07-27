## ADDED Requirements

### Requirement: Skill sources are limited to built-in and custom
The system SHALL model exactly two Skill origins: immutable built-in Skills shipped by Astra and globally shared custom Skills uploaded or created by the single fully privileged administrator, and SHALL NOT require user, tenant, workspace, project, publisher, ownership, or sharing-policy entities.

#### Scenario: Astra starts with built-in Skills
- **WHEN** an Astra release contains valid built-in Skill packages
- **THEN** the packages enter the shared Catalog with origin `builtin` and their release-bound revisions are immutable

#### Scenario: Administrator uploads a custom Skill
- **WHEN** the administrator uploads a valid Skill package
- **THEN** Astra creates a globally visible custom Skill Draft without asking for a publisher role, tenant, owner, or sharing scope

### Requirement: Built-in Skills are release-managed and protected
The system MUST reserve the Astra built-in identity namespace, MUST NOT allow uploaded or platform-edited content to replace a built-in revision, and SHALL update built-in Skills only as part of an Astra release.

#### Scenario: Custom Skill uses a reserved built-in identity
- **WHEN** an uploaded or created Skill attempts to use a reserved Astra identity
- **THEN** validation rejects the conflicting identity without changing the built-in Skill

#### Scenario: Administrator wants to modify a built-in Skill
- **WHEN** the administrator chooses to customize a built-in Skill
- **THEN** Astra creates a new custom Skill Draft with a non-reserved identity
- **THEN** the original built-in revision remains unchanged

### Requirement: Custom Skill publication is an explicit revision transition
The system SHALL let the administrator create, upload, edit, publish, disable, re-enable, export, and recoverably remove custom Skills; editing SHALL affect only a mutable Draft, while publishing SHALL create a new immutable Published Revision with a deterministic digest.

#### Scenario: Save an edited Draft
- **WHEN** the administrator saves one or more edited files
- **THEN** Astra updates the Draft revision
- **THEN** the active Published Revision and existing Runs remain unchanged

#### Scenario: Publish a valid Draft
- **WHEN** the administrator explicitly publishes a Draft that passes required validation
- **THEN** Astra creates and activates a new immutable Published Revision
- **THEN** later ordinary Runs may freeze that exact revision

#### Scenario: Disable a custom Skill
- **WHEN** the administrator disables a custom Skill
- **THEN** it is excluded from new Run Catalogs while historical Run snapshots remain readable

### Requirement: Skill declarations never grant runtime authority
The system MUST treat `allowed-tools`, compatibility text, Markdown instructions, script comments, and metadata as requested or descriptive capabilities only; actual tools, credentials, filesystem, network, process, data, and approval permissions SHALL come exclusively from Astra runtime policy and the current Run.

#### Scenario: Skill declares an allowed shell command
- **WHEN** `allowed-tools` or Skill instructions list a shell command
- **THEN** the command remains subject to tool eligibility, effect analysis, approval behavior, sandbox, workspace, credential, and budget gates

#### Scenario: Skill asks for an unavailable tool
- **WHEN** an active Skill requires a tool not present in the frozen Tool Catalog
- **THEN** the runtime reports the capability gap and does not synthesize or bypass the missing tool

### Requirement: Skill-driven execution uses the standard invocation pipeline
The system SHALL execute commands and bundled scripts only through registered Tool Provider Plugin executors and the standard Invocation Pipeline, and MUST NOT import Skill code into the Astra API process or execute it merely because the Skill was opened, edited, activated, or published.

#### Scenario: Active Skill includes a Python script
- **WHEN** the workflow decides to run the bundled script
- **THEN** the script is materialized or referenced as immutable input to an eligible sandboxed executor
- **THEN** the resulting effects, approval behavior, output envelope, artifacts, and audit records use the standard pipeline

#### Scenario: Skill is activated but no execution is needed
- **WHEN** Skill instructions can be followed without running bundled code
- **THEN** activation alone causes no script, command, network, or external side effect

### Requirement: Skill resource access is least-privilege and immutable
The system SHALL expose a Published Revision or explicit test snapshot as read-only, digest-checked input and SHALL require a normal authorized action to copy or transform an asset into a Task Workspace or Artifact.

#### Scenario: Script attempts to modify its frozen Skill revision
- **WHEN** a Skill-driven sandbox process attempts to write to the frozen Skill root
- **THEN** the write is denied and the revision digest remains unchanged

#### Scenario: Copy a template to the workspace
- **WHEN** an active Skill directs the Agent to instantiate a bundled template
- **THEN** the source remains immutable and the workspace copy is tracked as a normal workspace change

### Requirement: Safety diagnostics are bounded and fail closed
The system SHALL inspect uploaded and edited custom packages for unsafe paths, executable content, obfuscation indicators, unexpected binaries, instruction patterns that request policy bypass or secret exfiltration, and undeclared runtime requirements; required validation SHALL pass before publication, and inspection MUST NOT execute package content.

#### Scenario: Draft contains a critical unsafe condition
- **WHEN** required validation finds a configured critical condition
- **THEN** publication and Draft test execution are blocked with an actionable diagnostic

#### Scenario: Required scanner is unavailable
- **WHEN** publication or Draft testing requires a scanner that is unavailable
- **THEN** the operation fails closed while editing and non-executing preview remain available

### Requirement: Delegated execution attenuates Skill access
The system SHALL give each trusted Plan node or delegated Agent only the subset of active Skills and Skill resources required for its task and SHALL NOT allow it to activate a Skill absent from its parent Run's frozen Catalog.

#### Scenario: Plan node does not require an active Skill
- **WHEN** a Plan node is unrelated to one of the Run's active Skills
- **THEN** that node's model context and resource access omit the unrelated Skill

#### Scenario: Delegated execution requests a non-catalog Skill
- **WHEN** delegated execution requests a Skill outside its attenuated Catalog
- **THEN** activation is denied and cannot expand the parent Run's frozen capability set

### Requirement: Skill use is quota and revocation aware
The system SHALL enforce configured limits for eligible Catalog size, active Skills, instruction tokens, resource reads, bytes, script invocations, execution time, artifacts, and total Skill-attributed budget, and SHALL allow emergency revocation to prevent new high-risk actions.

#### Scenario: Activation exceeds the context budget
- **WHEN** activating another Skill would exceed the Run's Skill instruction budget
- **THEN** activation is rejected or the runtime chooses a smaller eligible set with an explicit diagnostic

#### Scenario: Custom Skill is revoked during a Run
- **WHEN** the active Published Revision is emergency-revoked after a Run snapshot was created
- **THEN** the snapshot remains inspectable but new Skill-attributed executable or external actions are blocked
