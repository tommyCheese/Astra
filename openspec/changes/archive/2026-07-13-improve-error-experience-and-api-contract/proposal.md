## Why

Astra currently exposes unhandled backend failures as bare 500 responses and displays request failures as inline text. Users cannot tell whether they can correct the problem, retry it, or contact an operator, while clients and operators lack a stable technical error classification.

## What Changes

- Introduce a versioned, safe API error envelope with category, stable code, user-safe message, retryability, and trace identifier.
- Classify validation, resource state, approval, policy, configuration, dependency, infrastructure, and internal runtime failures at a single backend boundary.
- Present user-actionable errors as accessible frontend dialogs with the relevant next action instead of an inline generic notice.
- Represent asynchronous Run failures with the same error contract in run results and events so they remain visible after task creation succeeds.
- Add trace-aware structured logging and tests that prevent raw exception text, credentials, or stack traces from reaching clients.

## Capabilities

### New Capabilities

- `api-error-contract`: Defines stable HTTP and asynchronous Run error envelopes, classification, trace IDs, and safe disclosure rules.
- `user-error-experience`: Defines frontend dialogs, user-actionable recovery actions, and technical-failure presentation.

### Modified Capabilities

<!-- No archived main specs exist yet. -->

## Impact

- FastAPI application setup, API routes, engine/run result schemas, SSE events, frontend API client and chat error UI.
- Existing error responses change from plain text or FastAPI default detail objects to the documented error envelope; frontend callers must consume it.
- Adds no external service dependency; structured logs use the existing application logging path.
