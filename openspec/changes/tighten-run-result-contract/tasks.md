## 1. Backend Contract

- [x] 1.1 Define typed models for run-result evidence diagnostics, audit references, completion decisions, and structured errors
- [x] 1.2 Add a backward-compatible `RunResult` model that normalizes legacy missing or malformed optional fields and omits undocumented top-level keys
- [x] 1.3 Change `RunView.result` and repository serialization to return the normalized contract with deterministic verification compatibility

## 2. Frontend Contract

- [x] 2.1 Align TypeScript run-result, completion, evidence diagnostic, audit, and error types with the backend schema
- [x] 2.2 Verify answer, evidence, sources, caveats, and failure consumers use only documented result fields

## 3. Verification

- [x] 3.1 Add backend schema/repository tests for current, failed, legacy, malformed, nullable, and unknown-field result payloads
- [ ] 3.2 Add or update frontend tests for typed result rendering and error compatibility
- [ ] 3.3 Run backend tests, frontend lint/tests/build, and inspect OpenAPI to confirm `RunView.result` references the formal schema
