## MODIFIED Requirements

### Requirement: Run budgets follow the effective user policy
The system SHALL use the persisted immutable mode Profile to bound Agent turns, tool calls, reflections, and replans. Standard SHALL use its fixed quick Profile; trusted SHALL use the allowed trusted resource preferences. Deployment-level limits MAY lower these values but SHALL NOT raise a persisted limit.

#### Scenario: Standard uses the fixed quick budget
- **WHEN** a user creates a standard Run
- **THEN** the Agent Loop uses the fixed quick reasoning behavior and deployment hard limits without creating a Plan

#### Scenario: Trusted deep effort is capped by deployment safety
- **WHEN** a trusted deep Profile budget exceeds a deployment-level hard limit
- **THEN** the runtime uses the deployment limit and records the effective Profile without exceeding it

### Requirement: Planning strategies select distinct runtime paths
The system SHALL select planning behavior solely from answer mode. Standard SHALL use the no-Plan quick path, and trusted SHALL use the complete-plan-first path. The runtime MUST NOT dispatch on a requested planning strategy.

#### Scenario: Standard starts without planning
- **WHEN** the effective answer mode is standard
- **THEN** the Run enters the shared Agent Loop without a model contract or planning call
- **THEN** no canonical Plan records are created

#### Scenario: Trusted generates and activates a full plan
- **WHEN** the effective answer mode is trusted and Plan execution is automatic
- **THEN** the runtime requests a model-generated contract and complete Plan before executing the Agent Loop
- **THEN** the validated Plan is activated for ready-node scheduling

#### Scenario: Trusted generates a full plan and waits for confirmation
- **WHEN** the effective answer mode is trusted and Plan execution requires confirmation
- **THEN** the runtime requests and persists the same complete validated Plan
- **THEN** the runtime enters waiting_user without activating a Plan node or performing an external action

#### Scenario: Trusted repairs an unfinished plan
- **WHEN** an observation invalidates an unfinished trusted Plan branch and replan budget remains
- **THEN** the runtime may apply a validated PlanPatch and activate a new version
- **THEN** the behavior does not depend on an adaptive planning enum

### Requirement: Runtime behavior is covered by tests
The system MUST include behavioral tests for the two fixed mode paths and MUST include repository-wide contract checks proving that deleted planning and plan-only inputs are absent.

#### Scenario: Behavioral mode tests
- **WHEN** the backend test suite runs
- **THEN** it verifies that standard creates no contract or DAG and trusted creates a full DAG before external action
- **THEN** it verifies trusted bounded replan, approval behavior, and full completion enforcement

#### Scenario: Removed contract tests
- **WHEN** API and schema tests run
- **THEN** `planning_strategy`, `adaptive`, `direct`, `plan_only`, and the plan activation endpoint are rejected or absent as specified
