## Why

The run detail API currently exposes `result` as an unbounded `dict[str, Any]`, so its documented contract cannot tell clients which answer, evidence, verification, completion, and failure fields are valid. Tightening this boundary now prevents backend/frontend drift while preserving the historical run records already stored in the database.

## What Changes

- Introduce a formal `RunResult` API schema covering successful answers, evidence and sources, verification metadata, completion decisions, and structured failure details.
- Change `RunView.result` from an arbitrary dictionary to the typed result schema so OpenAPI and generated/client-side types describe the real response.
- Normalize legacy persisted result payloads at the API boundary, supplying safe defaults for fields introduced after older runs were written.
- Consolidate verification output under `result.verification_report` and remove the duplicated top-level run field.
- Reject or deliberately retain only documented extension fields instead of silently exposing arbitrary keys.
- Align frontend types and rendering with the formal contract without changing the current answer, evidence, source, caveat, and error experience.
- Add contract and compatibility tests for successful, partial, failed, and historical results.

## Capabilities

### New Capabilities

- `run-result-contract`: Defines the stable, typed, backward-compatible result returned for an agent run.

### Modified Capabilities

None.

## Impact

- Backend Pydantic schemas, run serialization/API boundaries, and OpenAPI output.
- Frontend run/result TypeScript types and consumers of the former duplicated verification field.
- Backend and frontend tests for run detail responses and historical persistence compatibility.
- No database migration is required; stored JSON remains readable and is normalized when exposed.
