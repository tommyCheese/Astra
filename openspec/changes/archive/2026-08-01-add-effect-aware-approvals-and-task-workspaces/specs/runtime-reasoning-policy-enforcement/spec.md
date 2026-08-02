## ADDED Requirements

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
