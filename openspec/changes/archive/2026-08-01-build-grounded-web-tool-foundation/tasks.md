## 1. Canonical Grounding Contracts

- [x] 1.1 Add typed search trace, candidate, source snapshot, passage, claim, support edge, citation, and evidence fragment schemas
- [x] 1.2 Add deterministic canonical URL, content digest, evidence identity, segmentation, find, and open helpers
- [x] 1.3 Add unit tests for stable identities, bounded passages, candidate-only evidence, and local find/open behavior

## 2. Web Atomic Retrieval

- [x] 2.1 Extend `web_search` to accept bounded logical query batches while preserving legacy singular query behavior
- [x] 2.2 Normalize domain, freshness, language, region, content-type, and result-count constraints with applied and unsupported audit fields
- [x] 2.3 Preserve logical query lineage on normalized Google, Brave, Bing, and DuckDuckGo candidates without exposing credentials
- [x] 2.4 Extend successful Web reads with source, snapshot, digest, segmentation, passage, link, and extraction signal fields
- [x] 2.5 Update sandbox Web runtime compatibility and tests for the extended search/read contracts

## 3. Evidence Ledger and Plugin Ingestion

- [x] 3.1 Add append-only run-scoped evidence persistence with deterministic idempotency and conflicting-replay rejection
- [x] 3.2 Add a host EvidenceWriter and bounded EvidenceLedger context/find/open projection
- [x] 3.3 Make the built-in Web result processor emit schema-validated canonical evidence fragments
- [x] 3.4 Make the legacy Web adapter use the same fragment builder and accumulate rather than overwrite multi-query evidence
- [x] 3.5 Persist a typed evidence-ledger Artifact with ToolCall and Plan lineage during trusted finalization

## 4. Trusted Grounding and Completion

- [x] 4.1 Add claim and citation fields to FinalAnswer and RunResult with backward-compatible normalization
- [x] 4.2 Update synthesis instructions to bind claims to supplied evidence identities rather than inventing source URLs
- [x] 4.3 Implement provenance, citation-integrity, and structural claim-support validators with candidate-only exclusion
- [x] 4.4 Feed grounding ValidationOutcome records through VerificationEngine and the existing CompletionGate only when applicable or required
- [x] 4.5 Add trusted runtime tests proving grounded failures block when mandatory and ordinary non-Web trusted Runs remain unchanged

## 5. API and Presentation

- [x] 5.1 Extend frontend/API RunResult types for claims, citations, and grounding audit references
- [x] 5.2 Render validated inline citation markers linked to existing source cards without rendering invented evidence references
- [x] 5.3 Add result-schema, API, and frontend compatibility tests for new and historical grounded results

## 6. Verification and Documentation

- [x] 6.1 Run focused Web tool, grounding, plugin, security, trusted runtime, API, and frontend tests and fix regressions
- [x] 6.2 Update README documentation for atomic Web retrieval, evidence grounding, compatibility behavior, and explicit Deep Research non-scope
- [x] 6.3 Run strict OpenSpec validation and record implementation verification notes
