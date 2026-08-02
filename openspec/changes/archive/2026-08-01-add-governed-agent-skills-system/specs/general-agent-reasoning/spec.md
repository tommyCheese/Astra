## ADDED Requirements

### Requirement: Quick reasoning activates Skills inside the lightweight loop
The system SHALL expose bounded frozen Skill discovery metadata to the quick controller, SHALL accept structured Skill activation without creating trusted planning records, and SHALL continue the quick loop with the activated instructions and applicable resources.

#### Scenario: Quick controller automatically selects a Skill
- **WHEN** the quick controller determines that an eligible Skill description matches the request
- **THEN** it emits a structured activation for a frozen Skill identity
- **THEN** the next quick decision can use the validated Skill instructions without a TaskContract or DAG

### Requirement: Trusted reasoning resolves Skills before contract and planning
The system SHALL complete an explicit Skill-resolution phase for trusted Runs before generating the TaskContract and initial canonical Plan, and SHALL include each selected Skill identity and revision in trusted state.

#### Scenario: Trusted request matches a Skill
- **WHEN** Skill resolution selects one or more Skills for a trusted request
- **THEN** the TaskContract and complete initial DAG are generated with access to their frozen instructions
- **THEN** the Plan records applicable Skill identities for relevant nodes and success criteria

### Requirement: Trusted nodes receive attenuated Skill subsets
The system SHALL bind each trusted Plan node to the subset of active Skills required for that node and SHALL reconstruct only those Skill instruction and resource contexts in its NodeExecution.

#### Scenario: Parallel nodes use different Skills
- **WHEN** two ready nodes require different Skills
- **THEN** each NodeExecution receives only its declared Skill subset
- **THEN** neither node receives unrelated Skill resources solely because they are active elsewhere in the Run

### Requirement: Late trusted Skill activation requires a Plan revision
The system SHALL treat activation of a previously inactive frozen-Catalog Skill after trusted Plan persistence as a semantic Plan change and SHALL require a valid PlanPatch or replan before using it for executable nodes.

#### Scenario: Observation reveals a needed Skill
- **WHEN** a trusted observation shows that an inactive frozen-Catalog Skill is needed
- **THEN** the runtime activates it only together with a validated revision of the unfinished DAG
- **THEN** completed nodes and accepted evidence remain immutable

### Requirement: Trusted completion verifies Skill-derived criteria
The system SHALL map mandatory Skill workflow checks that are accepted into the TaskContract or Plan to stable success criteria and SHALL evaluate them through the trusted Completion Gate; Skill text alone MUST NOT mark a Run complete.

#### Scenario: Skill prescribes final validation
- **WHEN** trusted planning incorporates a mandatory validation step from an active Skill
- **THEN** the Plan links that step to a success criterion and evidence requirement
- **THEN** completion remains blocked until the normal trusted verification outcome satisfies it
