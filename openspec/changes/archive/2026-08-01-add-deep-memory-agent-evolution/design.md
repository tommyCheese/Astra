## Context

Astra already persists complete Task, Run, AgentTurn, ToolCall, Artifact, verification, and event history. It also has a `memories` table with scope, kind, content, structured data, provenance, confidence, and optional expiration, plus an LLM-based post-Run extractor. The current read path filters by the current `run_id` and returns the most recently updated records, so it does not provide useful cross-Session continuity, temporal conflict handling, or relevance-based retrieval.

Conversation context compaction is a separate projection over a Task's visible Runs. It protects the active model window and intentionally preserves the original history. Deep Memory must preserve this distinction: context summaries optimize one conversation, while long-term Memory represents reusable claims and experiences across conversations.

The packaged Agent Profile already contains `MEMORY.md` governance and a disabled `AUTODREAM.md` placeholder. Dynamic Memory is serialized as untrusted context, Profile snapshots are immutable per Run, tool authority comes from runtime policy, and the product currently operates as a local API without a complete account/authentication model. The implementation must therefore support explicit namespace keys without pretending that a missing authenticated user identity is globally safe.

The first release must continue to support SQLite and PostgreSQL and must not require a graph database, vector database, external scheduler, or online training system. It must remain useful with deterministic retrieval and the mock model used by tests.

## Goals / Non-Goals

**Goals:**

- Persist typed Memory that can be safely reused across Runs and Tasks within an explicit namespace.
- Represent changing knowledge through immutable versions, lifecycle state, valid time, supersession, revocation, and provenance.
- Retrieve a small, relevant, auditable Memory set using deterministic filters and hybrid scoring.
- Measure which memories were recalled and whether they helped or harmed subsequent execution.
- Run bounded, opt-in AutoDream consolidation that produces reviewable generations and can be rolled back.
- Represent learned procedures and policy recommendations as governed candidates, never as direct mutations of active policy or Skills.
- Propagate expiration, revocation, conversation deletion, and namespace authorization changes to every derived read model.

**Non-Goals:**

- Training or fine-tuning foundation models in the request-serving process.
- Making generated Memory, an evolution candidate, or an AutoDream result a source of authorization.
- Replacing Run history, context compaction, Evidence Packs, or Artifact provenance.
- Introducing a mandatory embedding provider, graph database, or distributed scheduler.
- Sharing Memory across users, workspaces, or organizations without an explicit identity and authorization model.
- Allowing AutoDream to edit canonical Agent Profile documents or installed Skills.
- Automatically promoting evolution candidates in the initial rollout.

## Decisions

### 1. Keep immutable execution evidence separate from versioned Memory projections

Runs, Turns, ToolCalls, Artifacts, evaluations, and events remain the source of truth. A Memory record is a derived, compact projection with one or more source references. Consolidation creates new Memory versions and links them to the evidence and input Memory versions; it does not rewrite source records or silently edit active Memory content.

Each Memory version has a stable `memory_key`, a unique record ID, lifecycle status, and optional `supersedes_id`. Publishing a replacement marks the previous active version as superseded in the same transaction. Revoked and expired versions remain available to privileged audit reads but are ineligible for Agent recall.

Alternative considered: update Memory rows in place. This is simpler but loses the exact content used by historical Runs and makes consolidation rollback or deletion audits unreliable.

### 2. Use explicit namespaces and deny unsafe cross-Session fallback

Memory uses `namespace_type` and `namespace_id` in addition to the semantic `scope`:

- Run-scoped Memory uses the Run ID.
- Task-scoped episodic Memory uses the Task ID.
- Workspace Memory requires a non-empty workspace ID.
- User Memory requires a non-empty created-by/user identity.
- Global or organization Memory is not created by the Agent until an authenticated governance path exists.

For a new Run, the retrieval namespace set is derived from its Run, Task, workspace, and creator records. The repository never treats `NULL` workspace or creator values as a shared namespace. Legacy rows that cannot be mapped safely remain run-scoped.

Alternative considered: use `scope` alone. Scope describes intended visibility but is not an identity boundary and would make all records with a missing owner accidentally shareable.

### 3. Extend the relational model before adding specialized indexes

The existing `memories` table receives additive lifecycle, namespace, temporal, stable-key, utility, and version columns. Supporting tables capture:

- `memory_sources`: source Run/Turn/ToolCall/Artifact or external reference.
- `memory_recall_events`: query fingerprint, candidate/selected score components, context target, and later outcome feedback.
- `memory_consolidation_jobs`: bounded job state, lease/idempotency data, input cursor, generation, validation, and publication result.
- `memory_links`: derivation, duplicate, contradiction, and supersession relationships when a single `supersedes_id` is insufficient.
- `agent_evolution_candidates`: procedure or policy candidate content, evidence, evaluation, lifecycle, and promotion metadata.
- `agent_evolution_evaluations` and `agent_evolution_audit_events`: immutable, digested evaluation manifests and append-only actor/reason/state-transition history.

The initial search path uses indexed relational filters, tokenized lexical overlap, exact kind/task/environment matches, recency, confidence, importance, and historical utility. The retrieval interface accepts an optional semantic scorer so PostgreSQL vector indexing or another provider can be added later without changing Agent context assembly.

Alternative considered: introduce Neo4j or a vector service immediately. It would add operational dependencies before Astra has measured a need for multi-hop graph traversal or embedding recall.

### 4. Use typed Memory with constrained lifecycle transitions

Supported initial kinds are:

- `semantic_fact`
- `user_preference`
- `episodic_experience`
- `procedure`
- `failure_pattern`
- `evaluation_feedback`

Unknown legacy kinds remain readable only in their original Run and are not promoted cross-Session without normalization. Lifecycle transitions are validated:

`candidate -> active -> superseded|revoked|expired`

`candidate -> quarantined|revoked`

`quarantined -> candidate|revoked`

Terminal states cannot return to active; rollback publishes or reactivates a prior generation through a new audited transition rather than deleting history. Expiration eligibility is evaluated at query time even if the background sweeper has not yet materialized the `expired` status.

Alternative considered: infer status solely from timestamps. Explicit status is needed for review, quarantine, rollback, and deterministic audit behavior.

### 5. Retrieve through eligibility, multi-signal scoring, and a token budget

Retrieval has three stages:

1. Eligibility filters namespace, lifecycle, expiration, minimum confidence, allowed kinds, provenance presence, and optional task/environment constraints.
2. Candidate scoring combines normalized lexical overlap, exact structured tags, kind affinity, recency decay, confidence, importance, and bounded historical utility. Semantic similarity is an optional additive signal.
3. Stable sorting and token budgeting select the final set. Ties use `updated_at` and record ID so results are reproducible.

The scorer returns component scores and exclusion reasons. Selected items are serialized under the existing untrusted-context delimiter with ID, kind, validity, confidence, and provenance summary. Instruction-like Memory cannot register tools, change Profile composition, or alter permissions.

Alternative considered: let the model issue unrestricted Memory searches during every decision. The initial deterministic prefetch is cheaper and easier to audit. A bounded Memory tool may be added later on top of the same eligibility contract.

### 6. Separate Memory extraction, activation, and utility feedback

The current post-Run extractor continues to propose candidates. Run-scoped observations can be active immediately, while Task, workspace, or user candidates pass deterministic validation for supported kind, namespace identity, provenance, confidence, content bounds, and sensitive/instruction-like data before activation.

Every context assembly records a recall event with candidates and selected items. AgentTurn `memory_reads` references selected event entries. Completion and verification can attach coarse outcome feedback such as used, ignored, contradicted, helpful, or harmful. Utility is a bounded aggregate and never overrides eligibility or authorization.

Alternative considered: treat every extracted item as durable active knowledge. That maximizes recall but amplifies hallucinations, duplicate growth, and prompt-injection persistence.

### 7. Implement AutoDream as an opt-in bounded background service

AutoDream follows the existing FastAPI lifespan service pattern and is disabled by default. Configuration controls enablement, scan interval, minimum candidate count, maximum records per job, model-call budget, and lease timeout. Startup recovery marks abandoned running jobs as interrupted before scheduling new work.

A job:

1. Acquires a database-backed lease/idempotency key for one namespace and working region.
2. Freezes an input manifest containing Memory IDs, versions, hashes, and provenance references.
3. Invokes a dedicated `AUTODREAM` model operation with the packaged `AUTODREAM.md` protocol and read-only untrusted evidence.
4. Validates bounded output, source coverage, namespace consistency, lifecycle operations, instruction isolation, and protected kinds.
5. Persists a proposed generation.
6. Publishes atomically only when configured for automatic low-risk publication; otherwise it remains reviewable.
7. Records events and supports rollback to the previous published generation.

The initial implementation may use a deterministic consolidator when no configured model is available, but it must follow the same job and validation contract. A job cannot invoke arbitrary tools or modify Profile documents, permissions, credentials, installed Skills, or source evidence.

Alternative considered: run consolidation synchronously at the end of every Run. This adds user-visible latency and lacks the cross-Session view that makes consolidation useful.

### 8. Activate `AUTODREAM.md` only for the dedicated operation

The Profile document status becomes `active`, meaning it is a valid governance document, not that scheduling is automatically enabled. Normal planner, controller, reflection, answer, and Memory extraction operations continue to exclude it. A new background-only model operation is the sole role allowed to select it.

AutoDream jobs freeze a Profile manifest just like Runs so historical consolidation can be reconstructed. Database Memory remains delimited untrusted input and cannot be promoted into trusted Profile content by prompt composition.

Alternative considered: embed the protocol entirely in service code and leave the document disabled. That would duplicate governance and weaken versioned auditability.

### 9. Treat Agent evolution as candidate generation plus external evaluation

AutoDream or a completed Run may propose two candidate types:

- `procedure`: a reusable, human-readable runbook constrained to current tools and policies.
- `policy_recommendation`: suggested planner, model-routing, retrieval, or scheduling parameters within declared tunable bounds.

Candidates have draft, evaluating, rejected, approved, shadow, canary, promoted, and rolled-back states. Initial production code supports creation, inspection, offline evaluation attachment, approval/rejection, and rollback metadata, but does not mutate active Skills or runtime policy.

Promotion requires a versioned evaluation manifest with baseline and candidate results, representative cases, safety checks, minimum sample size, and regression thresholds. Security floors, approval requirements, permission ceilings, credential boundaries, and sandbox enforcement are never tunable.

Alternative considered: let the Agent rewrite its Skill or system prompt after a successful task. A single successful trajectory is not causal evidence and could permanently amplify an injected or overfit behavior.

### 10. Make deletion and retention propagate through source links

Before a conversation lifecycle service deletes a Task and its Runs, it identifies linked Memory and evolution-candidate sources. Derived records with another valid source retain that source and are revalidated; derived records whose support is entirely deleted are revoked and excluded from every search or evaluation projection before source deletion commits. Recall and evolution audit events retain only audit-safe identifiers required by the configured retention policy.

Expiration and explicit revocation synchronously affect query eligibility. Background workers materialize expired status and clean optional derived indexes, but correctness never depends on the worker running.

Alternative considered: cascade-delete all linked Memory. This would incorrectly remove consolidated knowledge supported by other evidence and would obscure why an active projection changed.

### 11. Roll out with observable flags and paired evaluation

Cross-Session recall, AutoDream scheduling, automatic consolidation publication, and evolution candidate promotion use separate flags. The default rollout enables schema and audit writes first, then shadow retrieval, then selected-context injection, then manual AutoDream, and only later scheduled AutoDream.

Evaluation compares no-memory, legacy recency, cross-Session retrieval, and retrieval-plus-consolidation on fixed cases. Required measures include retrieval precision/recall, temporal updates, abstention, task success, tool calls, tokens, latency, stale-memory use, negative transfer, deletion propagation, and namespace leakage.

## Risks / Trade-offs

- [Generated Memory persists a hallucination or injected instruction] → Require explicit namespace and provenance, validate supported types and content, quarantine suspicious candidates, keep all recalled text untrusted, and measure contradiction or harmful-use feedback.
- [Missing authenticated identity causes data leakage] → Never map missing user/workspace IDs to a shared namespace; keep unmappable legacy data run-scoped.
- [Lexical retrieval misses paraphrases] → Preserve an optional semantic scoring interface and add embeddings only after deterministic baselines and migration behavior are stable.
- [Scoring favors recent but irrelevant records] → Expose score components, use task/kind structure and utility, and evaluate against recency and no-memory baselines.
- [Consolidation loses rare but important detail] → Freeze input manifests, require source coverage, create immutable replacement versions, keep source evidence, and support generation rollback.
- [Concurrent extraction and Dream publication conflict] → Use stable versions, namespace leases, optimistic expected-version checks, and atomic publication.
- [Background jobs overload model or database capacity] → Disable by default, bound scans and records, enforce cooldown and budgets, and isolate failure per job.
- [Conversation deletion leaves derived data behind] → Resolve `memory_sources` in the deletion transaction and make query-time source/lifecycle filters authoritative.
- [Evolution optimizes to the evaluation set] → Require held-out cases, minimum sample size, safety regressions, shadow data, Canary limits, and rollback.
- [Additive schema increases repository complexity] → Centralize lifecycle and retrieval logic in a dedicated Memory repository/service rather than spreading filters across Agent code.

## Migration Plan

1. Add nullable/default-safe columns and new tables with indexes. Backfill every legacy Memory with a stable key, `active` status, version 1, and a safe namespace derived from its Run/Task; unmappable records become Run-scoped.
2. Deploy repository code that dual-reads legacy and new rows but keeps cross-Session injection disabled. Validate namespace counts, source coverage, and expiration behavior.
3. Start writing normalized candidates and recall audit events while retaining legacy current-Run retrieval as the serving path.
4. Enable shadow cross-Session retrieval and compare selected sets without placing them in model context.
5. Enable cross-Session context injection for local/workspace-scoped deployments after leakage and negative-transfer tests pass.
6. Deploy AutoDream tables, dedicated Profile operation, and manual job execution with scheduling and automatic publication disabled.
7. Enable scheduled proposal generation, then low-risk publication only after replay and rollback drills pass.
8. Deploy evolution candidate APIs and evaluation attachment; keep direct production promotion disabled until a separate operational policy explicitly enables it.

Rollback disables context injection and all workers, restoring legacy current-Run recall without dropping schema or audit history. A published consolidation generation is rolled back through lifecycle transitions; migrations are not destructively reversed while new records exist.

## Open Questions

- Which authenticated user and organization identities will become authoritative when Astra moves beyond its current local API boundary?
- Which semantic embedding provider, if any, meets the deployment's privacy and offline requirements after deterministic retrieval has a measured baseline?
- Should low-risk duplicate-only consolidation ever auto-publish, or should every generation require explicit approval in the first product release?
- What minimum replay suite and regression thresholds are required before procedure candidates may enter Shadow or Canary?
