## Context

Astra currently embeds short identity statements independently in `OpenAICompatibleModelClient.plan`, `synthesize`, `decide`, `decide_with_answer`, `reflect`, and `extract_memory_candidates`. `ContextAssembler` separately supplies Tool Manifests, unavailable capabilities, observations, database Memory, reasoning policy, task contract, plan graph, and Agent state. This already provides most dynamic runtime facts, but there is no canonical Agent Profile, role-to-document composition policy, or durable record of which identity definition shaped a historical Run.

The existing `memories` table stores scoped runtime facts with provenance and confidence. It must remain distinct from a proposed `MEMORY.md`: the document governs memory behavior, while database rows contain actual user, workspace, and Run data. The current backend runs directly from the Python application package, uses SQLite or PostgreSQL through SQLAlchemy, and does not yet provide authenticated administrator workflows for editing privileged system prompts.

## Goals / Non-Goals

**Goals:**

- Establish Git-managed, packaged canonical documents for Astra's identity, personality, memory governance, and future AutoDream protocol.
- Compose every real-model system prompt through one typed, role-aware boundary.
- Preserve current structured JSON protocols and deterministic mock behavior while removing duplicated identity wording.
- Freeze enough Profile data per Run to reproduce the selected identity after restart or later Profile changes.
- Keep dynamic tool availability, permissions, memory data, and task context outside the static Profile trust domain.
- Make Profile versioning and usage testable and auditable.

**Non-Goals:**

- Add a UI or API for editing identity or personality.
- Store canonical Profile source exclusively in the database.
- Commit the live SQLite database to Git.
- Implement AutoDream scheduling, autonomous background action, memory consolidation, or vector retrieval.
- Let workspace or user content override platform identity, safety rules, or permissions.
- Expose complete system prompts through the normal Run API.

## Decisions

### Decision 1: Canonical Profile documents live inside the backend Python package

Create `backend/app/agent_profile/` containing `README.md`, `IDENTITY.md`, `SOUL.md`, `MEMORY.md`, and `AUTODREAM.md`. Load them with `importlib.resources` and declare Markdown files as package data.

This placement keeps the trusted Profile under Git review, couples changes to tests and releases, works for wheels and containers, and avoids fragile current-working-directory paths. `docs/` is rejected because operational runtime inputs should not depend on a documentation tree. A repository-root configurable directory is not the default because installed packages may not retain the repository layout.

Alternative considered: store active Markdown only in the database. This would support hot editing, but Astra currently lacks the administrator authorization, draft review, activation, cache invalidation, and rollback controls required for safe system-prompt mutation.

### Decision 2: Use a typed Profile loader and immutable value objects

Introduce an `AgentProfileLoader` that validates package resources once, normalizes line endings and terminal whitespace, computes SHA-256 hashes, and returns an immutable `AgentProfile` containing typed documents and an `AgentProfileManifest`. A `composition_schema_version` participates in the aggregate Profile hash so changes to selection semantics also create a new version.

Required-document failures become typed configuration errors before a model HTTP request. Silent fallback to scattered legacy prompts is prohibited because it would defeat auditability and create inconsistent identity.

Alternative considered: read Markdown on every model call. This simplifies hot reload but adds repeated I/O, permits mid-Run drift, and makes prompt behavior depend on mutable files.

### Decision 3: Freeze complete Profile source per Run, but keep files authoritative for new Runs

Add an `agent_profile_snapshot` JSON field to `runs`. Before the first model invocation, a new Run stores the manifest, normalized canonical document content, composition schema version, and role-selection metadata. A resumed Run reconstructs its `AgentProfile` from this snapshot. A new Run loads the current packaged files.

The copied content is an audit snapshot, not an editable authority. It is acceptable for the current document size and deployment scale and guarantees exact resume behavior across service restarts and Profile upgrades. Standard API serialization exposes safe metadata only; raw contents remain backend audit data.

Alternative considered: save hashes only and recover content from Git. Deployed wheels and containers may not contain repository history, so hashes alone cannot resume an old Run after an upgrade. A normalized `agent_profile_revisions` table would reduce duplication at scale but adds activation semantics that are unnecessary until online editing or multiple profiles are introduced.

### Decision 4: Compose prompts by model operation

Add a `PromptComposer` with an explicit operation enum and role-to-document matrix. The initial matrix is:

| Operation | Profile content |
|---|---|
| contract | identity goals and boundaries |
| plan | identity goals and boundaries |
| decide | identity + relevant soul principles |
| decide_with_answer | identity + soul |
| synthesize/finalize | identity + soul |
| reflect | identity + relevant memory governance |
| memory extraction | memory governance only |

`AUTODREAM.md` is never selected by current synchronous operations. The composer appends role-specific JSON/output contracts after trusted Profile sections and labels dynamic context separately.

An explicit operation value also replaces `_chat_json`'s current substring-based operation inference for usage metering. This prevents Profile wording changes from misclassifying model invocations.

Alternative considered: concatenate all four documents into every prompt. This is simpler but wastes tokens, weakens role focus, and risks activating future AutoDream concepts during normal chat.

### Decision 5: Treat runtime context as data with a lower trust level

Prompt composition uses ordered, labeled sections:

```text
trusted platform profile
  -> trusted role and output protocol
  -> runtime capability manifest and policy
  -> delimited untrusted memory/history/tool/external data
  -> current user request
```

Tool availability remains the intersection of registered implementations, environment settings, persisted tool switches, infrastructure availability, Tool Router permissions and risks, and remaining Run budgets. Profile text cannot modify this intersection. Recalled Memory and external observations are explicitly labeled as data, not instructions.

Alternative considered: let `IDENTITY.md` enumerate supported tools. That list would become stale whenever a user disables a tool or Docker/network infrastructure becomes unavailable.

### Decision 6: Keep canonical governance separate from actual Memory and AutoDream state

`MEMORY.md` defines allowed memory categories, provenance, confidence, recall, conflict, expiration, deletion, and instruction-isolation principles. Actual records continue using the existing `memories` table. `AUTODREAM.md` is a disabled protocol placeholder; its mere presence does not schedule work or authorize actions. A future change may add Dream jobs, proposals, approval state, and audit tables without changing this boundary.

## Risks / Trade-offs

- [Risk] Full Profile content copied into every Run increases database size. → Keep documents concise, measure snapshot size, and migrate to immutable shared revisions if scale warrants it.
- [Risk] Markdown language changes structured JSON behavior. → Keep role schemas in typed code, validate documents, and add real-client prompt composition contract tests.
- [Risk] Memory or tool output performs prompt injection. → Delimit dynamic data, state its trust level, preserve hard Tool Router enforcement, and test instruction-like Memory content.
- [Risk] Profile content accidentally claims unavailable capabilities. → Add content review rules and tests asserting that executable tools come only from dynamic manifests.
- [Risk] Removing embedded wording changes mock or usage behavior. → Leave `MockModelClient` deterministic and replace substring operation inference with an explicit enum before switching prompts.
- [Risk] Raw snapshots expose privileged system text through APIs. → Store snapshots on the backend and serialize only safe version/hash metadata in normal Run views.
- [Risk] A malformed release cannot answer requests. → Validate packaged resources in tests and build checks; fail with a typed configuration error rather than silently changing identity.

## Migration Plan

1. Add canonical documents, package-data configuration, typed loader, normalization, validation, and manifest tests.
2. Add the Run snapshot field and migration; backfill existing Runs with an explicit `legacy-unversioned` marker without pretending their exact prompts are reconstructable.
3. Add the Prompt Composer and explicit model-operation identifier while retaining the existing role-specific output protocols.
4. Freeze the active Profile when creating or first executing a new Run; resume from its snapshot.
5. Migrate model call sites one operation at a time and verify prompt selection, usage metering, streaming, structured output normalization, and error handling.
6. Expose safe Profile metadata in audit serialization and update architecture documentation.
7. Remove duplicated identity phrases only after all model call paths are covered by tests.

Rollback keeps the additive database snapshot column. The model client can temporarily return to legacy prompt construction behind a short-lived internal compatibility switch, while new Profile files remain inert. Run snapshots must not be deleted during rollback.

## Open Questions

- At what database-size threshold should per-Run snapshots be normalized into immutable `agent_profile_revisions` referenced by Runs?
- Should a future administrator-facing Profile editor permit only deployment-level variants, or also restricted workspace-level `SOUL` overlays? That requires a separate authorization and activation proposal.
