## Context

Run creation currently raises FastAPI defaults or plain text failures. The route maps all `ValueError` instances to 404, unhandled database and configuration exceptions become bare 500 responses, and the frontend turns any non-success body into a string under the composer. Background failures are stored as an untyped summary/caveat and are not guaranteed to be distinguishable from an answer.

## Goals / Non-Goals

**Goals:**

- Establish one safe, typed error contract for synchronous API and asynchronous Run failures.
- Give users a clear dialog and recovery route for errors they can act on.
- Give clients and operators stable technical types and trace identifiers without leaking internals.
- Preserve HTTP semantics and make errors testable across route, engine, SSE, and UI boundaries.

**Non-Goals:**

- Do not expose server stack traces or secrets to browsers.
- Do not add external monitoring or an error-reporting SaaS.
- Do not make all failed Runs automatically retry; retries remain policy and error-code dependent.

## Decisions

### Decision 1: Use `ApiError` as the sole public error shape

Every error response uses:

```json
{"error":{"type":"infrastructure.database_unavailable","code":"DATABASE_UNAVAILABLE","message":"服务暂时无法访问数据存储，请稍后重试。","retryable":true,"trace_id":"req_...","details":{}}}
```

`type` is a dotted diagnostic category; `code` is a stable machine-facing identifier. `details` is optional and contains only allowlisted, user-safe data such as a field name or required action. The model rejects unknown or unsafe fields at the response boundary.

### Decision 2: Classify exceptions at one application boundary

Introduce domain exception classes with code/category/status/retryability and a FastAPI exception handler plus catch-all handler. Routes raise domain exceptions rather than selecting HTTP status ad hoc. The catch-all handler logs the original exception against a trace ID and returns only `runtime.internal_error`.

Mapping includes validation (422), resource (404), state/policy (409 or 403), configuration/dependency (503), infrastructure (503), and runtime (500). Authentication is reserved for a future auth layer.

### Decision 3: Run errors reuse `ApiError` but are not HTTP responses

The engine converts model, tool, configuration and unexpected background exceptions to `RunError` payloads with the same fields. It stores them at `result.error`, writes `run.error` events, and sets a strict terminal status. This allows polling and SSE clients to render one consistent experience regardless of when the failure occurred.

### Decision 4: UI maps codes to recovery dialogs

The client owns presentation mapping: user-actionable codes get targeted copy and actions; technical types get generic safe copy plus trace ID. The API never tells the browser to execute arbitrary UI behavior. A reusable modal receives `ApiError`, focus target and optional retry callback.

### Decision 5: Logs contain diagnostic context; responses do not

Structured logs include trace ID, error type/code, request path, run/task ID, cause class and safe operational context. The raw exception is available only to server logs. Error events omit raw exception text and credentials.

## Risks / Trade-offs

- [Risk] Existing clients expect `detail` or plain text. → Mitigation: update the local frontend atomically and document the breaking response change.
- [Risk] Over-classifying errors exposes implementation detail. → Mitigation: maintain an allowlisted taxonomy and map unknowns to `runtime.internal_error`.
- [Risk] Modal overload becomes disruptive. → Mitigation: use dialogs only for actionable/request failures; show task-level cards for background failures.
- [Risk] Trace IDs are mistaken for secrets. → Mitigation: use random opaque IDs with no encoded data.

## Migration Plan

1. Add error models, taxonomy, exception classes, handlers, and route/engine tests.
2. Convert Run endpoints and engine failures; publish Run error events while retaining existing success payloads.
3. Update API client and add dialog/card components behind the new error parser.
4. Enable the new contract for all endpoints, remove raw-text fallback after compatibility verification, and document codes.

Rollback: retain server logs and typed Run errors; frontend can fall back to a generic safe error dialog if an older response shape is encountered.

## Open Questions

- Should retry create a new Run or rerun the existing failed Run? Initial recommendation: new Run for create failures, explicit rerun endpoint later for resumable failures.
- Should operator-only diagnostic details be conditionally available in local development? Initial recommendation: logs only, to keep API behavior identical across environments.
