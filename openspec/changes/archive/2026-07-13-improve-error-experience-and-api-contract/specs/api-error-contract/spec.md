## ADDED Requirements

### Requirement: API errors use a safe structured envelope
The system SHALL return a JSON error envelope for every API failure response containing `error.type`, `error.code`, `error.message`, `error.retryable`, and `error.trace_id`.

#### Scenario: Unexpected backend failure
- **WHEN** an unhandled exception reaches an API boundary
- **THEN** the response has HTTP 500 and a `runtime.internal_error` type
- **THEN** the response does not expose exception text, stack traces, credentials, connection strings, or internal paths

#### Scenario: Trace identifier links response and logs
- **WHEN** the system returns a structured error response
- **THEN** it generates or propagates a trace identifier
- **THEN** the same trace identifier is included in structured server logs

### Requirement: Errors have stable categories and HTTP mappings
The system SHALL classify errors as validation, resource, state, policy, configuration, dependency, infrastructure, or runtime errors and SHALL map each code to a documented HTTP status and retryability.

#### Scenario: Invalid user input
- **WHEN** a request fails validation because required input is missing or malformed
- **THEN** the response uses a validation error code and HTTP 422
- **THEN** the error is marked user-actionable and not retryable without changing input

#### Scenario: Database is unavailable
- **WHEN** a request cannot reach the configured database
- **THEN** the response uses `infrastructure.database_unavailable` and HTTP 503
- **THEN** retryability reflects whether the service can reasonably recover without user data changes

### Requirement: Expected domain failures preserve meaning
The system SHALL map known domain failures to their correct category and MUST NOT convert all `ValueError` instances to the same HTTP status.

#### Scenario: Task is missing
- **WHEN** a request references a task that does not exist
- **THEN** the response uses `resource.task_not_found` and HTTP 404

#### Scenario: Run cannot resume
- **WHEN** a request attempts to resume a run that is not waiting for input
- **THEN** the response uses `state.run_not_waiting` and HTTP 409

### Requirement: Asynchronous Run failures use the same contract
The system SHALL persist a safe structured error envelope in failed or blocked Run results and SHALL publish an error event containing the same type, code, retryability, and trace identifier.

#### Scenario: Model provider fails after Run creation
- **WHEN** a background Run fails after the create endpoint has returned successfully
- **THEN** the Run result and its terminal event contain a structured error envelope
- **THEN** the failure is distinguishable from a successful answer with caveats
