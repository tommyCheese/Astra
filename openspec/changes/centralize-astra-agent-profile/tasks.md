## 1. Canonical Profile Documents

- [x] 1.1 Create `backend/app/agent_profile/` with `README.md`, `IDENTITY.md`, `SOUL.md`, `MEMORY.md`, and `AUTODREAM.md` using the approved responsibility boundaries and concise runtime-ready content
- [x] 1.2 Add document schema metadata, required-section rules, size limits, and explicit statements that Profile text cannot grant runtime capabilities
- [x] 1.3 Configure Python package data so every required Markdown file is included in source distributions and wheels
- [x] 1.4 Add tests that load the packaged documents outside the repository and backend working directories

## 2. Profile Domain and Loader

- [x] 2.1 Implement immutable Agent Profile document, manifest, snapshot, and model-operation value objects
- [x] 2.2 Implement package-resource loading, UTF-8 validation, normalization, required-section validation, and typed configuration failures
- [x] 2.3 Implement deterministic per-document SHA-256 hashes and an aggregate version including the composition schema version
- [x] 2.4 Add unit tests for stable normalization and hashes, changed-content versions, malformed documents, missing resources, and size limits

## 3. Run Snapshot Persistence

- [ ] 3.1 Add `agent_profile_snapshot` to the Run ORM model and API schema with a migration that marks existing Runs as `legacy-unversioned`
- [ ] 3.2 Freeze the packaged Profile before the first model invocation for new Runs and prevent later mutation of the stored snapshot
- [ ] 3.3 Reconstruct the Profile from an existing Run snapshot when resuming, including after the packaged default changes
- [ ] 3.4 Expose only safe Profile version, composition version, document identifiers, and hashes through the standard Run view
- [ ] 3.5 Add repository and API tests for new snapshots, legacy Runs, restart/resume consistency, immutability, and raw-content non-disclosure

## 4. Central Prompt Composition

- [ ] 4.1 Implement a Prompt Composer with an explicit model-operation enum and documented role-to-document selection matrix
- [ ] 4.2 Preserve each operation's existing structured output protocol while composing trusted Profile sections exactly once
- [ ] 4.3 Delimit runtime manifests, conversation history, recalled Memory, observations, and external content as lower-trust contextual data
- [ ] 4.4 Ensure `AUTODREAM.md` is excluded from every current synchronous question-answering operation
- [ ] 4.5 Add prompt composition tests for contract, plan, decide, combined answer, synthesize/finalize, reflect, and memory extraction

## 5. Model Client and Runtime Integration

- [ ] 5.1 Pass the frozen Run Profile into model operations without changing deterministic `MockModelClient` behavior
- [ ] 5.2 Migrate all real-model call sites from duplicated Astra identity strings to the centralized Prompt Composer
- [ ] 5.3 Replace `_chat_json` substring-based usage operation inference with the explicit model-operation identifier
- [ ] 5.4 Preserve answer streaming, JSON normalization, retry behavior, usage metering, and typed model errors after prompt migration
- [ ] 5.5 Verify that eligible Tool Manifests, persisted switches, sandbox availability, Tool Router permissions, risks, and budgets remain the only executable capability authority

## 6. Trust Boundaries and Memory Separation

- [ ] 6.1 Keep actual run, workspace, and user Memory writes in the existing database path with provenance and confidence rather than modifying Profile files
- [ ] 6.2 Add regression tests proving instruction-like Memory and external text cannot alter Profile selection, authorize tools, or bypass Tool Router checks
- [ ] 6.3 Add tests proving packaged `AUTODREAM.md` neither schedules work nor writes, deletes, or consolidates Memory
- [ ] 6.4 Verify Profile snapshots and logs do not capture credentials, API keys, or unredacted secret configuration

## 7. Verification and Documentation

- [ ] 7.1 Run backend unit, repository, API, migration, model-client, Agent-loop, and usage-metering tests and fix regressions
- [ ] 7.2 Build and inspect the backend wheel to verify that all Profile resources are included and loadable
- [ ] 7.3 Update the Astra architecture and Agent execution walkthrough with the Profile, database Memory, runtime capability, and Run snapshot boundaries
- [ ] 7.4 Document the future threshold for normalizing repeated Run snapshots into immutable Profile revisions without adding online editing in this change
- [ ] 7.5 Run OpenSpec validation and confirm all capability scenarios have automated or explicitly documented verification coverage
