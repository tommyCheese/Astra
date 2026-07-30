# Implementation Verification

Verified on 2026-07-30.

## Scope delivered

- Provider-neutral, bounded Web search batches with truthful constraint audit and per-invocation trace lineage.
- Stable source, snapshot, passage, candidate, claim, support-edge, citation, and evidence-fragment contracts.
- Run-scoped append-only Evidence Ledger persistence with identical replay idempotency, concurrent insert convergence, and conflicting replay rejection.
- Shared fragment normalization in the built-in Web plugin and the legacy Web adapter.
- Claim/citation projection plus provenance, citation-integrity, and material-claim support validators routed through existing verification and completion semantics.
- Backward-compatible RunResult/API/frontend grounding fields and validated citation-to-source-card presentation.
- Explicit architectural boundary: Deep Research is not implemented or activated by this change and can only depend on the shared grounding layer later.

## Automated verification

- `pytest -q` in `backend`: **604 passed, 8 skipped**.
- Focused grounding/Web/plugin/invocation/agent-loop/result/API/security suite: **191 passed, 1 skipped**.
- `ruff check` on changed grounding, Web, runtime, schema, migration, and focused test files: passed.
- `npm run lint` in `frontend`: passed.
- `npm test` in `frontend`: **120 passed** across 10 files, including grounding presentation and historical compatibility.
- `npm run build` in `frontend`: production build passed.
- `alembic heads`: one head, `0026_scheduled_jobs_heartbeat`, with grounding migration `0025_grounding_evidence` in the linear chain.
- `alembic upgrade head` against a fresh isolated SQLite database: passed through all revisions including `0025_grounding_evidence`.
- `git diff --check`: passed.
- `openspec validate build-grounded-web-tool-foundation --strict`: passed.

## Compatibility observations

- Existing singular `web_search.query`, `web_fetch`, legacy result fields, provider selection, and historical persisted results remain readable.
- Grounding validators return no outcomes when a Run has no canonical evidence, so ordinary trusted execution does not acquire research-specific requirements.
- Search-result snippets remain candidate-only and cannot satisfy mandatory material claim support.
- Frontend citation markers are rendered only when claim, evidence reference, and an existing canonical-equivalent source card all agree.

## Known non-blocking test output

- The existing React Flow graph test logs JSDOM `NaN` SVG attribute warnings; the suite passes and the warnings are outside this change.
