## ADDED Requirements

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
