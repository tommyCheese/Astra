# runtime-reasoning-policy-enforcement Specification

## Purpose
TBD - created by archiving change enforce-user-reasoning-policy-at-runtime. Update Purpose after archive.
## Requirements
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
The system MUST include behavioral tests for the two fixed mode paths and MUST include repository-wide contract checks proving that deleted planning and plan-only inputs are absent.

#### Scenario: Behavioral mode tests
- **WHEN** the backend test suite runs
- **THEN** it verifies that standard creates no contract or DAG and trusted creates a full DAG before external action
- **THEN** it verifies trusted bounded replan, approval behavior, and full completion enforcement

#### Scenario: Removed contract tests
- **WHEN** API and schema tests run
- **THEN** `planning_strategy`, `adaptive`, `direct`, `plan_only`, and the plan activation endpoint are rejected or absent as specified

### Requirement: Model calls follow the effective Run reasoning effort
The system SHALL bind the Run's persisted effective reasoning effort to every model operation in that Run and SHALL resolve Provider-specific request parameters before dispatch.

#### Scenario: Planning and execution share one immutable effort
- **WHEN** a Run starts with a persisted effective reasoning effort
- **THEN** contract, plan, decision, reflection, finalization, and memory operations use that same effort unless an operation-specific capability rule safely lowers it

#### Scenario: Missing bound policy uses a compatible default
- **WHEN** a model client is invoked by a test or legacy caller without a bound Run policy
- **THEN** the adapter uses balanced effort without changing the public caller contract

### Requirement: Provider adaptation is covered by behavioral tests
The system MUST include tests that inspect dispatched request bodies and invocation metadata, not only standalone mapping return values.

#### Scenario: Request body tests
- **WHEN** the backend model-client tests run
- **THEN** they verify supported parameters are sent, unsupported parameters are omitted, and incompatible JSON mode is removed

#### Scenario: Policy binding tests
- **WHEN** RunEngine executes a Run with a persisted effort
- **THEN** tests verify that model operations receive the effective effort snapshot

### Requirement: Runtime binds an immutable effective model thinking configuration

The system SHALL resolve and persist the selected model's effective thinking configuration before model operations begin, and SHALL use that immutable configuration for all model operations belonging to the Run.

#### Scenario: Explicit model thinking configuration is present
- **WHEN** a Run is created with a supported explicit model thinking selection
- **THEN** the runtime binds that effective selection before planning, decisions, reflection, finalization, and memory model calls
- **THEN** later preference changes do not alter the active Run

#### Scenario: Run continues after waiting for the user
- **WHEN** a Run resumes after plan confirmation, approval, or requested user input
- **THEN** the runtime restores the Run's persisted effective model thinking configuration
- **THEN** a newly selected composer preference does not silently replace the active Run configuration

### Requirement: Runtime separates Agent orchestration effort from model thinking

The system SHALL use Agent reasoning effort to bound orchestration resources and SHALL use an explicit model thinking configuration to control Provider model-call reasoning parameters when both are present.

#### Scenario: Deep Agent effort uses low model thinking
- **WHEN** a Run requests deep Agent reasoning effort and explicitly selects low model thinking
- **THEN** the runtime applies deep Agent turn, tool, reflection, and verification budgets
- **THEN** model calls use the selected low model thinking parameters

#### Scenario: Fast Agent effort uses high model thinking
- **WHEN** a Run requests fast Agent reasoning effort and explicitly selects high model thinking
- **THEN** the runtime retains fast Agent orchestration limits
- **THEN** model calls use the selected high model thinking parameters

### Requirement: Answer mode does not override an explicit model thinking selection

The system SHALL preserve the explicit effective model thinking selection in both standard and trusted Runs. Answer mode SHALL determine orchestration and assurance behavior, not silently raise or lower the selected model thinking depth.

#### Scenario: Standard Run uses high model thinking
- **WHEN** a standard Run explicitly selects high model thinking
- **THEN** compatible model calls use high thinking parameters
- **THEN** the Run still skips trusted planning and retains standard assurance behavior

#### Scenario: Trusted Run disables optional model thinking
- **WHEN** a trusted Run explicitly disables thinking for a model that supports disabling it
- **THEN** compatible model calls omit or disable extended thinking parameters
- **THEN** trusted planning, approval, safety, reflection policy, and strict validation remain enforced

#### Scenario: Trusted Run uses high model thinking across operations
- **WHEN** a trusted Run explicitly selects high model thinking
- **THEN** every compatible model operation in that Run uses the effective high selection
- **THEN** each invocation records its applied configuration for usage and latency analysis

### Requirement: Model thinking settings do not control public process events

The runtime SHALL produce public phase and concise reasoning-summary events according to the Agent execution protocol regardless of whether Provider model thinking is disabled, low, or high. The runtime MUST NOT forward hidden chain-of-thought into those events.

#### Scenario: Model thinking is disabled
- **WHEN** a Run executes with model thinking disabled
- **THEN** the runtime continues to emit applicable phase, public reasoning summary, tool, reflection, and verification events

#### Scenario: Provider reports reasoning tokens
- **WHEN** a Provider reports hidden reasoning-token usage
- **THEN** the runtime may record numeric usage metadata
- **THEN** the runtime MUST NOT copy hidden reasoning content into public process events

### Requirement: Execution modes govern side effects rather than tool availability
The runtime SHALL combine platform policy, ToolSpec maximum permissions, the invocation ActionEffectPlan, existing grants, and the selected execution mode before every tool execution.

#### Scenario: Plan-only performs research
- **WHEN** a plan-only Run needs permitted read-only tools or non-persistent temporary computation to understand the task
- **THEN** the runtime may execute those actions and use their evidence to produce a concrete plan

#### Scenario: Plan-only encounters a side effect
- **WHEN** a plan-only Run proposes persistent workspace creation, modification, deletion, external write, sensitive-data release, or another side effect
- **THEN** the runtime does not execute or request interactive approval, records an `effect_blocked_by_mode` observation, and describes the action in the final plan

#### Scenario: Request-approval performs a safe query
- **WHEN** a request-approval Run proposes a platform-permitted action with no persistent or external side effect
- **THEN** the action executes without an approval request

#### Scenario: Request-approval proposes a side effect
- **WHEN** a request-approval Run proposes a side-effecting action without a matching grant
- **THEN** the runtime freezes the action and requests user approval before execution

#### Scenario: Auto-approval proposes a platform-permitted side effect
- **WHEN** an auto-approval Run proposes a side-effecting action that passes all platform, resource, budget, and Sandbox checks
- **THEN** the runtime may execute it without an interactive approval request

#### Scenario: Any mode proposes a prohibited action
- **WHEN** platform policy prohibits the required permission or resource scope
- **THEN** the action is rejected in every execution mode, including auto-approval

