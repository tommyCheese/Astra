## ADDED Requirements

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
