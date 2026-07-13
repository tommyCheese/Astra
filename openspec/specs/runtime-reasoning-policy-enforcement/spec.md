# runtime-reasoning-policy-enforcement Specification

## Purpose
TBD - created by archiving change enforce-user-reasoning-policy-at-runtime. Update Purpose after archive.
## Requirements
### Requirement: Run budgets follow the effective user policy
The system SHALL use the Run's persisted effective reasoning policy to bound Agent turns, tool calls, reflections, and replans. Deployment-level limits MAY lower these values but SHALL NOT silently raise a user-selected budget.

#### Scenario: Fast effort uses the fast budget
- **WHEN** a user creates a Run with fast reasoning effort
- **THEN** the Agent Loop stops at the effective fast turn, tool, and reflection limits

#### Scenario: Deep effort is capped by deployment safety
- **WHEN** a deep policy budget exceeds a deployment-level hard limit
- **THEN** the runtime uses the deployment limit and records the effective policy without exceeding it

### Requirement: Planning strategies select distinct runtime paths
The system SHALL distinguish direct, adaptive, and plan-first strategies during planning.

#### Scenario: Direct planning starts locally
- **WHEN** the effective planning strategy is direct
- **THEN** the Run uses a local single-step plan without a model planning call

#### Scenario: Adaptive planning defers expansion to the Agent
- **WHEN** the effective planning strategy is adaptive
- **THEN** the Run starts from a lightweight contract and permits the Agent to select tools, reflect, or replan based on observations

#### Scenario: Plan-first generates a full plan
- **WHEN** the effective planning strategy is plan-first
- **THEN** the runtime requests a model-generated contract and plan before executing the Agent Loop

### Requirement: Reflection obeys the user switch and trigger
The system SHALL route every automatic reflection through the effective reflection policy and SHALL enforce its reflection budget.

#### Scenario: Reflection is disabled
- **WHEN** reflection is disabled and a model or tool failure occurs
- **THEN** the runtime records the failure without invoking the reflector

#### Scenario: Failure-only reflection
- **WHEN** reflection is enabled with failure-only trigger and a tool or completion failure occurs
- **THEN** the runtime invokes reflection if budget remains

#### Scenario: Every-turn reflection
- **WHEN** reflection is enabled with every-turn trigger and a non-terminal Agent turn completes
- **THEN** the runtime invokes reflection if budget remains

#### Scenario: Adaptive reflection ignores ordinary successful turns
- **WHEN** reflection uses the adaptive trigger and a normal successful turn makes progress without conflict
- **THEN** the runtime does not invoke reflection

### Requirement: Runtime behavior is covered by tests
The system MUST include tests that assert policy choices change runtime behavior, not only request serialization or persisted fields.

#### Scenario: Behavioral policy tests
- **WHEN** the backend test suite runs
- **THEN** it verifies budget enforcement, planning path selection, disabled reflection, failure-only reflection, and every-turn reflection

