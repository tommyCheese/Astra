## 1. Configuration and Candidate Selection

- [x] 1.1 Add validated conversation retention settings and deployment environment wiring
- [x] 1.2 Implement oldest-first bounded candidate selection and immediate eligibility revalidation
- [x] 1.3 Add repository tests for age, terminal, pin, share, empty-conversation, ordering, and batch rules
- [x] 1.4 Add and validate database indexes for bounded retention candidate scans

## 2. Canonical Deletion Lifecycle

- [x] 2.1 Extract reusable conversation lifecycle deletion including artifact and workspace cleanup
- [x] 2.2 Route the conversation DELETE API through the canonical lifecycle service
- [x] 2.3 Test database cascade cleanup, safe workspace targeting, and best-effort external cleanup failures

## 3. Background Aging Worker

- [x] 3.1 Implement startup sweep, cancellable periodic execution, per-item isolation, revalidation, and aggregate results
- [x] 3.2 Wire retention startup and shutdown into the FastAPI lifespan
- [x] 3.3 Test disabled behavior, bounded deletion, race skips, failure isolation, and clean shutdown

## 4. Product Copy and Operations

- [x] 4.1 Change sidebar copy from retention wording to display-limit wording and update frontend tests
- [x] 4.2 Document configuration, protection rules, observability, enablement, and rollback
- [x] 4.3 Run focused backend/frontend suites and the full feasible validation set
