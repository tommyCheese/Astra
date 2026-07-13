## 1. Error Domain and API Boundary

- [x] 1.1 Define typed error models, stable taxonomy, code registry, HTTP mappings, retryability rules, and opaque trace ID generation.
- [x] 1.2 Add domain exception classes for validation, resource, state, policy, configuration, dependency, infrastructure, and runtime failures.
- [x] 1.3 Add FastAPI handlers for domain exceptions, request validation errors, database/connectivity failures, and unknown exceptions that always return the safe envelope.
- [x] 1.4 Add structured error logging with trace ID, request/run context, and original exception available only server-side.
- [x] 1.5 Replace route-local generic `ValueError` mappings with explicit domain errors and correct status codes.
- [x] 1.6 Add backend tests for envelope shape, trace propagation, status mapping, safe disclosure, and unknown exception fallback.

## 2. Run and Event Failure Contract

- [x] 2.1 Add a typed Run error field and error event schema compatible with the API error envelope.
- [x] 2.2 Convert model configuration/output, tool, policy, database, and unexpected engine failures into safe typed Run errors.
- [x] 2.3 Publish structured `run.error` terminal events and preserve trace IDs through polling and SSE payloads.
- [x] 2.4 Ensure failed, blocked, and waiting Run outputs cannot be rendered as successful answers.
- [x] 2.5 Add integration tests for background model failure, tool failure, blocked policy, and database/configuration failures.

## 3. Frontend Error Client and Dialogs

- [x] 3.1 Add typed frontend error parsing that consumes the envelope and retains safe message, code, type, retryability, trace ID, and details.
- [x] 3.2 Create an accessible reusable error dialog with focus restoration, close behavior, optional retry, and code-specific actions.
- [x] 3.3 Map user-actionable validation, state, policy, approval, and capability codes to targeted dialog messages and recovery actions.
- [x] 3.4 Map technical configuration, dependency, infrastructure, and runtime errors to safe generic dialogs with trace IDs and conditional retry.
- [x] 3.5 Render task-level failure cards for asynchronous Run errors and prevent final-answer rendering for failed Runs.
- [x] 3.6 Add frontend tests for typed error parsing, dialog accessibility, user-actionable recovery, technical error trace display, retryability, and Run failure cards.

## 4. Compatibility, Documentation, and Verification

- [x] 4.1 Document public error types, codes, status mappings, retry semantics, trace IDs, and safe-disclosure policy.
- [x] 4.2 Update API and Run client documentation with the new error contract and migration guidance for plain-text clients.
- [x] 4.3 Run backend lint, API/unit/integration tests, frontend type/build/tests, and validate representative 4xx, 5xx, and asynchronous Run failure paths.
