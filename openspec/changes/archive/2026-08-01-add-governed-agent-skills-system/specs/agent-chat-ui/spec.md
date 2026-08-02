## ADDED Requirements

### Requirement: Astra provides a separate shared Skill management surface
The desktop experience SHALL provide a dedicated Skill route separate from chat that lists globally shared built-in and custom Skills by origin, lifecycle state, active revision, compatibility, and diagnostic state.

#### Scenario: Open the Skill library
- **WHEN** the administrator navigates to Skill management
- **THEN** the UI distinguishes immutable Astra built-ins from editable custom Skills
- **THEN** it does not present user ownership, tenant, workspace, sharing-scope, or Publisher controls

### Requirement: Custom Skills open in the authoring workbench
The Skill management surface SHALL open custom Skills in the multi-file authoring workbench and SHALL support create, import, edit, test, publish, disable, export, revision history, restore-to-Draft, and recoverable removal actions.

#### Scenario: Review a Draft before publication
- **WHEN** the administrator opens a changed custom Skill
- **THEN** the UI shows dirty and saved state, validation findings, requested tools, scripts, resource inventory, Draft-versus-Published Diff, and publish readiness

#### Scenario: View a built-in Skill
- **WHEN** the administrator opens a built-in Skill
- **THEN** files are read-only and the primary customization action creates a custom clone

### Requirement: Composer supports Skill selection in both modes
The chat Composer SHALL offer automatic Skill selection and explicit Skill selection for both quick response and trusted execution, and SHALL explain that Skill selection does not change the chosen answer mode.

#### Scenario: Select a Skill in quick mode
- **WHEN** the administrator explicitly selects a Skill while quick response is active
- **THEN** the Composer displays the selection without showing trusted Plan controls

#### Scenario: Select a Skill in trusted mode
- **WHEN** the administrator explicitly selects a Skill while trusted execution is active
- **THEN** the Composer indicates that the Skill will be resolved before TaskContract and Plan generation

### Requirement: Chat shows Skill activation and use
The chat timeline SHALL show compact events when a Skill is activated, rejected, conflicts, loads a resource, materially guides an action, or causes a trusted Plan revision, with details available in the audit view.

#### Scenario: Skill activates automatically
- **WHEN** the model activates a Skill based on its description
- **THEN** the timeline identifies the Skill origin and revision and explains that it was selected for the current task

#### Scenario: Skill-guided action needs approval
- **WHEN** a Skill-guided tool call pauses for approval
- **THEN** the approval UI attributes the recommendation to the Skill while explaining that Astra runtime policy, not the Skill, controls authorization

### Requirement: Skill diagnostics are actionable
The UI SHALL distinguish invalid format, incompatible runtime, Draft-only state, disabled or revoked revision, digest drift, missing tool, budget exhaustion, stale editor revision, publication conflict, and failed Draft test states and SHALL present a corrective action when one exists.

#### Scenario: Required tool is missing
- **WHEN** an active Skill cannot continue because its required tool is unavailable
- **THEN** the chat explains the capability gap without presenting the Skill as successfully executable

### Requirement: Historical Skill use remains inspectable
The audit view SHALL show the frozen Skill identities, origins, Published or Draft-test digests, activation initiators, relevant model operations, resource reads, attributed tool calls, Plan bindings, policy outcomes, and revocation events used by a historical Run.

#### Scenario: Custom Skill changed after completion
- **WHEN** the administrator inspects a completed Run after the custom Skill was republished
- **THEN** the audit view continues to identify the exact frozen revision used by that Run
