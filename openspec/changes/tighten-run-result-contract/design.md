## Context

`RunRecord.result` is intentionally stored as JSON because a run accumulates answer, evidence, verification, completion, and failure information over time. The API currently mirrors that JSON through `RunView.result: dict[str, Any] | None`, leaving OpenAPI and frontend consumers without an enforceable contract. Existing databases may contain older payloads with missing collection fields, partially shaped nested objects, or now-undocumented top-level keys, so changing persistence or requiring a one-time data migration would be unnecessarily risky.

## Goals / Non-Goals

**Goals:**

- Define one documented schema for all externally visible run result fields.
- Make OpenAPI and frontend types accurately describe success, warning, blocked, and failed results.
- Keep historical JSON records readable by applying defaults and safe coercion at the API boundary.
- Ensure unknown top-level result keys do not leak into API responses.
- Preserve the current UI behavior and persisted database format.

**Non-Goals:**

- Replacing the JSON database column with relational tables.
- Fully typing every internal runner state, tool payload, audit detail, or provider-specific metadata.
- Redesigning the answer UI or changing agent completion behavior.
- Migrating existing result rows in place.

## Decisions

### Introduce a dedicated boundary model

`RunResult` will compose existing `Finding`, `SourceReference`, `VerificationReport`, and `CompletionDecision` models and add named models for failed sources, source quality, result errors, conflicts, memory references, and audit references where their stable fields are known. Provider- or tool-specific metadata inside those records remains a bounded dictionary so the public shape is strict without pretending all nested metadata is uniform.

Alternative: keep `dict[str, Any]` and publish handwritten documentation. Rejected because runtime validation, generated OpenAPI, and client types would continue to drift.

### Normalize only at the read/API boundary

The database continues storing result JSON as produced by the runner. `RunView` validates it into `RunResult`; missing historical fields receive defaults, malformed collection members are discarded or normalized, and undocumented top-level keys are ignored. New writes should use the same model where practical, but no data rewrite is required.

Alternative: migrate every JSON row. Rejected because it creates deployment and rollback risk without improving runtime behavior.

### Preserve nullable result, but make a present result total

An in-progress run may still return `result: null`. Once a result object exists, all collection fields serialize consistently as arrays/dictionaries and `summary` falls back to the run summary or an empty string for legacy rows. This gives clients a predictable object while retaining lifecycle semantics.

### Keep verification in one canonical location

`verification_report` remains inside `result`, alongside the answer and its completion metadata. The duplicated `RunView.verification_report` field is removed, and frontend consumers read only `result.verification_report`. This makes ownership explicit and prevents the two copies from diverging.

### Align TypeScript manually with OpenAPI

The project currently maintains handwritten frontend types. They will mirror the new schema, including typed completion and error records, and tests will assert the relevant UI paths. Introducing code generation is outside this focused change.

## Risks / Trade-offs

- [Unknown legacy top-level fields disappear from API output] → Cover all currently produced fields with repository/runner searches and contract tests; persistence remains untouched for rollback or later recovery.
- [Malformed historical nested data could fail validation] → Add pre-validation normalization for list/dict fields and exercise representative legacy payloads.
- [A strict nested model could block future provider metadata] → Keep explicit `metadata`/detail dictionaries at intentionally extensible nested boundaries while forbidding undocumented top-level result expansion.
- [Existing consumers may read the former top-level verification field] → Update all in-repository consumers in the same change and expose one unambiguous OpenAPI location.

## Migration Plan

1. Add schemas and normalization tests without changing the database model.
2. Change `RunView.result` to `RunResult | None` and remove the top-level verification duplicate.
3. Update frontend types and run contract/UI tests.
4. Verify current and historical run endpoints plus OpenAPI output.

Rollback consists of reverting the API/type changes; stored JSON never changes.

## Open Questions

None.
