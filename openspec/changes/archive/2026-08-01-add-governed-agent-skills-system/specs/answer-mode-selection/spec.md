## ADDED Requirements

### Requirement: Both answer modes support frozen Skills without changing mode semantics
The system SHALL allow both `standard` quick-response Runs and `trusted` trusted-execution Runs to use built-in or custom Skill revisions, while preserving the fixed planning, execution, and verification lifecycle of the selected answer mode.

#### Scenario: Quick Run uses a Skill
- **WHEN** a `standard` Run explicitly or automatically activates a Skill
- **THEN** it follows the Skill through the quick Agent Loop without creating a TaskContract, Plan, PlanNode, PlanEdge, or trusted Completion Gate
- **THEN** shared tool, effect, approval, sandbox, artifact, cancellation, and error boundaries remain active

#### Scenario: Trusted Run uses a Skill
- **WHEN** a `trusted` Run requires one or more Skills
- **THEN** the system resolves and loads their frozen instructions before TaskContract and initial Plan DAG generation
- **THEN** trusted planning and full verification incorporate the applicable Skill workflow

### Requirement: Skill activation cannot switch answer mode
The system MUST NOT allow Skill instructions, compatibility declarations, scripts, or activation decisions to change a Run from quick response to trusted execution or from trusted execution to quick response.

#### Scenario: Quick Skill recommends trusted execution
- **WHEN** an active Skill appears to require a long, multi-deliverable, or strongly verified workflow
- **THEN** Astra may present a recommendation to start a trusted Run
- **THEN** the current quick Run does not create a DAG or silently switch modes

### Requirement: Draft tests explicitly select quick or trusted mode
The system SHALL require every Skill Draft test Run to select `standard` or `trusted` and SHALL label the Run as using an unpublished test snapshot without weakening the selected mode's safety boundaries.

#### Scenario: Start a Draft test
- **WHEN** the administrator starts a Skill Draft test from the workbench
- **THEN** the request identifies the answer mode and exact Draft digest before the first model operation

