## Context

Astra's trusted runtime already owns TaskContract creation, canonical PlanGraph execution, ToolCall and Artifact provenance, ValidationOutcome aggregation, reflection, and CompletionGate decisions. Web-specific evidence is still assembled through mutable `WebTaskAdapter` state and tool-name branches, however, and the persisted Evidence Pack contains provider-shaped dictionaries rather than stable source, passage, claim, and citation identities.

The in-progress plugin runtime already defines a ToolResultEnvelope, result processors, validators, and InvocationPipeline. This change must converge on those contracts while preserving the legacy AgentLoop path until plugin migration is complete. Existing trusted Runs must remain general-purpose and must not acquire research planning, source-count, or long-report behavior.

## Goals / Non-Goals

**Goals:**

- Make network retrieval composable through normalized search and read contracts.
- Preserve every successful query and source result across multiple calls.
- Create immutable, run-scoped evidence identities with complete invocation lineage.
- Let trusted synthesis cite stable evidence rather than model-authored URLs.
- Feed grounding validators into the existing VerificationEngine and CompletionGate.
- Allow ordinary trusted tasks and a future Deep Research workflow to share the same evidence layer with different validation policies.
- Keep legacy Web tool names and historical RunResult fields readable during migration.

**Non-Goals:**

- Implementing `/deep-research`, a research planner, coverage-driven补搜 loop, background report execution, or module-specific UI.
- Requiring every trusted Run to browse the Web or satisfy research-specific source-count rules.
- Adding authenticated browser sessions, paywall bypass, CAPTCHA handling, or anti-bot evasion.
- Making public search snippets sufficient evidence for material claims.
- Replacing the Plugin Catalog, Permission Engine, PlanScheduler, VerificationEngine, or CompletionGate.

## Decisions

### 1. Dependency direction is Deep Research → trusted runtime + grounding

The shared layer is named Evidence Grounding Runtime and has no dependency on a Deep Research package. General trusted execution uses it when canonical evidence is present; a future workflow module can require stricter coverage and diversity validators through its frozen profile.

No trusted core module imports or activates Deep Research. The future module selection remains nullable and existing Runs require no data backfill.

### 2. Network tools remain search and read; find/open are host evidence operations

`web_search` accepts one legacy `query` or a bounded `queries` batch. Each logical query receives its own stable search trace and preserves applied or unsupported constraints. `web_fetch` remains a compatible public name while its output gains the semantics of `web_read`: canonical source identity, immutable snapshot identity, content digest, bounded passages, links, and extraction signals.

`find_passages(source_id, query)` and `open_passage(source_id, passage_id)` are implemented on the host Evidence Ledger rather than as network tools. The Web OCI runtime is intentionally stateless and cannot be granted database access merely to reopen evidence. A future isolated evidence-input transport may expose these operations as model-callable tools without changing their contracts.

Alternative considered: pass complete source bodies back into a stateless `web_find` tool. This was rejected because it duplicates sensitive content in prompts and ToolCall inputs, increases cost, and weakens provenance.

### 3. Evidence is canonical, append-only, and run-scoped

The canonical graph is:

```text
SearchTrace → SearchCandidate → SourceSnapshot → Passage
                                             ↘ SupportEdge → Claim → Citation
```

Every record includes a deterministic evidence key plus Run, Plan node, NodeExecution, ToolCall, Artifact, digest, and timestamp lineage where applicable. Re-ingesting an identical fragment is idempotent; a conflicting payload under the same key fails closed.

Raw or normalized large bodies remain Artifacts. Evidence records store bounded passage text and typed metadata so validators and UI do not need to parse provider outputs.

### 4. Provider plugins normalize; the host persists

The built-in Web result processor converts ToolResultEnvelope data into schema-validated canonical evidence fragments. Plugins do not write repositories directly. InvocationPipeline or its recorder adds trusted runtime lineage and passes fragments to a host EvidenceWriter.

During the compatibility period the legacy WebTaskAdapter uses the same canonical fragment builder and final Evidence Ledger projector. This keeps one evidence meaning while the remaining AgentLoop migration proceeds.

Alternative considered: keep provider-specific Evidence Packs and translate only during final synthesis. This was rejected because concurrent nodes, validation, recovery, and future Deep Research need stable evidence before finalization.

### 5. Search constraints report truthfully

The normalized request supports language, region, result count, freshness bounds, included/excluded domains, and content types. Providers apply only supported constraints. Query syntax rewriting and post-filtering are recorded separately, and unsupported constraints remain visible in successful output.

Provider credentials, raw response headers, redirect tokens, and secret configuration never enter search traces or evidence records.

### 6. Snapshot and passage identities derive from canonical content

Source identity derives from the canonical URL. Snapshot identity derives from source identity plus normalized content digest. Passages are deterministic bounded segments with stable ordinal, offsets, and section labels where extractable.

The fetch output retains bounded `content` for current model compatibility but synthesis context prefers passage references and the Evidence Ledger projection. Search snippets are marked `candidate_only` and cannot independently satisfy material-claim validation.

### 7. Grounding policies are validator configuration, not global mode branches

The shared validators are:

- `grounding.provenance`: cited records exist in the current Run and preserve lineage.
- `grounding.citation_integrity`: citations reference declared claims and passages.
- `grounding.claim_support`: material claims have at least one eligible passage and no candidate-only evidence.

General trusted execution activates these validators only when Web evidence was attempted or when its TaskContract explicitly requires them. Future Deep Research adds coverage, diversity, freshness, and conflict policies through module-specific verification requirements. Installation of that module cannot change the validator set of a Run with no module selected.

### 8. VerificationEngine aggregates and CompletionGate decides

Web-specific fetched-source inspection moves out of VerificationEngine. Grounding validators return existing ValidationOutcome records. `apply_validation_outcomes` updates TaskContract criteria, and the existing CompletionGate blocks mandatory failed requirements.

Grounding gaps may emit `low_confidence`, `evidence_conflict`, or `completion_gate_failed` reflection signals, but the grounding layer does not choose queries or patch plans itself.

### 9. Results preserve compatibility and add typed grounding

RunResult retains summary, findings, sources, source_quality, conflicts, caveats, and audit references. It adds typed `claims` and `citations`. Models return claim text with evidence IDs; a host projector validates those IDs and derives presentation annotations. Models do not invent source URLs or calculate final character offsets.

Older results normalize missing claims and citations to empty lists. The first frontend slice renders compact inline citation markers and continues to show the existing source cards.

## Risks / Trade-offs

- [The plugin migration is incomplete] → Use one canonical fragment builder from both the plugin processor and legacy adapter; delete the adapter only when `pluginize-tool-runtime` finishes.
- [Persisting every passage increases database size] → Bound passage size/count, keep full bodies in Artifact storage, and apply existing Run retention.
- [Deterministic segmentation changes after extractor upgrades] → Include a segmentation version in snapshot metadata and derive IDs from the frozen normalized content plus version.
- [Provider filters differ] → Return applied, emulated, post-filtered, and unsupported constraints explicitly.
- [Claim-support validation adds model cost] → First enforce structural eligibility and provenance deterministically; semantic entailment can be an opt-in validator in a later change.
- [Existing trusted behavior changes accidentally] → Activate grounding requirements by evidence applicability or explicit TaskContract requirement, and add golden tests proving trusted non-Web runs keep their current profile and validator set.
- [Concurrent ingestion races] → Use deterministic keys and a unique `(run_id, evidence_key)` constraint; identical retries are idempotent and conflicting retries fail.

## Migration Plan

1. Add grounding schemas, deterministic builders, Evidence Ledger persistence, and a backward-compatible database migration.
2. Extend Web search/read outputs and sandbox configuration without removing legacy fields or tool names.
3. Make the built-in plugin processor emit canonical fragments and make the legacy adapter project the same ledger.
4. Add grounding validators and result projection behind applicability checks.
5. Extend API/frontend types and render citations while preserving existing source presentation.
6. Run Web tool, security, plugin, trusted runtime, result schema, API, and frontend tests plus strict OpenSpec validation.
7. After plugin runtime migration completes, remove mutable WebTaskAdapter accumulation and remaining tool-name evidence branches in a separate cleanup.

Rollback disables grounding projection and validators while retaining additive tables and result fields. Legacy fields remain sufficient for the previous application version.

## Open Questions

- Semantic claim entailment should later use the active model, a smaller verifier model, or deterministic lexical checks plus selective model escalation.
- A future provider-neutral cache may deduplicate snapshots across Runs, but the first implementation remains Run-scoped to preserve retention and authorization boundaries.
- Exposing find/open as model-callable host tools depends on a safe evidence-input binding for isolated runtimes and is intentionally deferred.
