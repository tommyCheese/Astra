## 1. Memory Schema and Migration

- [x] 1.1 Add typed Memory constants and lifecycle transition rules
- [x] 1.2 Extend `MemoryRecord` with stable key, namespace, lifecycle, temporal, version, importance, utility, and supersession fields plus supporting indexes
- [x] 1.3 Add Memory source, link, recall-event, consolidation-job, evolution-candidate, immutable evolution-evaluation, and evolution-audit database models with relationships and indexes
- [x] 1.4 Create an additive Alembic migration that backfills legacy Memory into safe Run namespaces and creates source links
- [x] 1.5 Add migration/model tests for SQLite defaults, indexes, legacy backfill, and non-null safety

## 2. Memory Lifecycle and Repository

- [x] 2.1 Add a dedicated Memory repository/service with namespace derivation that never treats missing identities as shared
- [x] 2.2 Implement candidate validation and constrained lifecycle transitions with optimistic expected-version checks
- [x] 2.3 Implement immutable version creation, supersession, revocation, expiration, and historical state reads
- [x] 2.4 Implement source and Memory-link persistence with provenance validation
- [x] 2.5 Preserve compatibility wrappers for existing Run repository Memory methods and serialized Run views
- [x] 2.6 Add repository tests for namespace isolation, transitions, supersession, expiration, and provenance rejection

## 3. Cross-Session Retrieval and Feedback

- [x] 3.1 Implement deterministic tokenization, lexical overlap, kind/tag, recency, confidence, importance, and bounded utility score components
- [x] 3.2 Implement eligibility filtering for namespace, lifecycle, expiration, kind, confidence, provenance, and source accessibility
- [x] 3.3 Implement stable item/token-budget selection and an optional semantic scorer interface
- [x] 3.4 Persist recall events with candidate, selected, excluded, query fingerprint, policy version, and shadow-mode data
- [x] 3.5 Integrate cross-Session retrieval into context assembly while preserving legacy and shadow feature flags
- [x] 3.6 Record selected Memory in AgentTurn audit data and accept bounded outcome feedback
- [x] 3.7 Add retrieval tests for determinism, isolation, temporal replacement, expiry, budgets, shadow mode, and instruction isolation

## 4. Extraction and Memory Management API

- [x] 4.1 Normalize extractor output into supported Memory kinds, namespace identities, stable keys, temporal fields, and candidate lifecycle
- [x] 4.2 Validate and activate safe Run/Task/workspace/user candidates without granting authority
- [x] 4.3 Add authorized APIs and schemas to list, inspect, revoke, and view the history and sources of Memory
- [x] 4.4 Extend Run and audit responses with safe lifecycle, version, namespace, score, and provenance metadata
- [x] 4.5 Add API tests for cross-Session reads, revocation, invalid transitions, missing identity, and audit-safe output

## 5. AutoDream Profile and Consolidation Domain

- [x] 5.1 Update packaged `AUTODREAM.md` and Profile validation so the protocol is active but background-only
- [x] 5.2 Add a dedicated AutoDream model operation and role-to-document composition that remains unavailable to synchronous Runs
- [x] 5.3 Implement frozen consolidation input manifests and deterministic normalized hashes
- [x] 5.4 Implement deterministic duplicate consolidation and a bounded model-output normalization contract
- [x] 5.5 Implement proposal validation for source coverage, namespace isolation, version conflicts, instruction isolation, and protected authority
- [x] 5.6 Implement atomic generation publication, supersession, and audited rollback
- [x] 5.7 Add Profile and consolidation-domain tests for operation isolation, reproducibility, conflicts, atomicity, and rollback

## 6. AutoDream Worker and Operations

- [x] 6.1 Add validated, disabled-by-default AutoDream scheduling, budget, batch, cooldown, and lease settings
- [x] 6.2 Implement a bounded background service with startup recovery, database-backed idempotency, and failure isolation
- [x] 6.3 Wire AutoDream startup and shutdown into the FastAPI lifespan without affecting normal Memory paths when disabled
- [x] 6.4 Add authorized APIs to trigger, list, inspect, publish, and roll back consolidation jobs
- [x] 6.5 Add worker and API tests for disabled mode, bounded selection, interrupted recovery, duplicate prevention, and audit state

## 7. Governed Agent Evolution Candidates

- [x] 7.1 Implement immutable procedure and policy-recommendation candidates with constrained lifecycle transitions
- [x] 7.2 Implement evaluation-manifest validation for baselines, held-out cases, safety metrics, thresholds, cost, and latency
- [x] 7.3 Enforce protected policy floors and reject candidates that expand Tools, permissions, credentials, approvals, or sandbox authority
- [x] 7.4 Add APIs to create, inspect, evaluate, approve, reject, and record rollback metadata while automatic production promotion remains disabled
- [x] 7.5 Add tests for stale versions, missing baselines, safety regressions, disabled Tool references, and non-executable approved candidates

## 8. Deletion, Retention, and UI

- [x] 8.1 Propagate conversation deletion through Memory and evolution sources, revoking or revalidating unsupported derived records before source deletion commits
- [x] 8.2 Materialize expired lifecycle state and clean optional derived indexes without making worker execution a correctness dependency
- [x] 8.3 Add frontend types and API clients for Memory, consolidation jobs, and evolution candidates
- [x] 8.4 Add Memory audit and management UI for lifecycle, sources, recall scores, revocation, consolidation review, and rollback
- [x] 8.5 Add frontend tests for inspection, safe rendering, revocation, and disabled promotion controls

## 9. Evaluation, Documentation, and Verification

- [x] 9.1 Add fixed retrieval and temporal-update fixtures comparing no-memory, legacy recency, cross-Session retrieval, and consolidation
- [x] 9.2 Record metrics for relevance, task outcomes, token/latency cost, stale use, harmful feedback, and namespace leakage
- [x] 9.3 Document Memory namespaces, lifecycle, scoring, AutoDream operations, deletion, rollout flags, evaluation, and rollback
- [x] 9.4 Run backend and frontend test suites, OpenSpec validation, migration smoke tests, and targeted concurrency checks
- [x] 9.5 Update the system design and graph evolution roadmap with implemented boundaries and deferred semantic/graph indexing work
