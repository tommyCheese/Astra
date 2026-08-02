## 1. Change Coordination and Data Contracts

- [x] 1.1 Reconcile and archive or sync the completed `add-governed-subagent-runtime` ContextManifest/checkpoint specs before changing their persisted schema
- [x] 1.2 Inventory context construction, observation persistence, generic model generation, usage accounting and recovery call sites for conversation, standard root, trusted root, quick child and trusted child paths
- [x] 1.3 Add versioned `ContextEnvelope`, `ContinuationManifest`, compaction metadata and Astra-owned root/child checkpoint schemas
- [x] 1.4 Add `RootContextCheckpointV2` with global continuity fields, validated Evidence/Artifact/result references and explicit untrusted-summary metadata
- [x] 1.5 Replace the child checkpoint payload with backward-compatible `ChildContextCheckpointV2` fields while retaining V1 read support
- [x] 1.6 Add configuration and feature flags for shadow mode, role enablement, threshold scope, recovery waterline, output reserve, recent-tail budgets, deterministic emergency and generic compaction model policy
- [x] 1.7 Add database migration fields/indexes for owner, window number, input digest, policy/schema version, implementation, generation model identity and compaction lifecycle status

## 2. Shared Token Accounting and Policy Engine

- [x] 2.1 Extract a shared Token accounting service that prefers Provider-reported usage/tokenizers and returns conservative source-labelled estimates otherwise
- [x] 2.2 Implement usable-input calculation with model window, normal output reserve and compaction-output reserve
- [x] 2.3 Implement `total` and `body_after_prefix` scope accounting with per-window prefill baselines and the full context hard cap
- [x] 2.4 Implement conversation, root and child `CompactionPolicy` objects with protected sections, checkpoint schema, recent-tail priority and role-specific capacity exits
- [x] 2.5 Implement deterministic recent-tail selection by Token budget, newest-first selection and chronological reinsertion
- [x] 2.6 Implement trigger evaluation for soft threshold, recovery waterline, hard cap, model/Provider switch and model downshift
- [x] 2.7 Add shadow-mode metrics that compute trigger decisions and projected post-compaction size without changing model-visible history

## 3. Shared Compaction Lifecycle

- [x] 3.1 Implement `AgentContextCompactionService` snapshot, started event, external model call, validation, post-budget check and conditional installation flow
- [x] 3.2 Implement idempotency keys from owner, window number, input digest and policy version, with reusable completed results
- [x] 3.3 Implement state-version and cancellation-epoch compare-and-swap installation that marks stale results superseded
- [x] 3.4 Implement cumulative checkpoint input so repeated compactions merge the prior checkpoint without recursive summary nesting
- [x] 3.5 Implement schema, protected-field, forbidden-content, reference-access, hash and recovery-waterline validators
- [x] 3.6 Implement bounded retry and role-specific capacity failure handling without mutating active history before a valid install
- [x] 3.7 Persist source item boundaries, retained-tail boundary, Token before/after, model/Provider, duration, cost and failure stage for every attempt

## 4. Astra-Owned Semantic Compaction

- [x] 4.1 Implement versioned conversation, root and child compaction prompt builders over the same Provider-neutral `ContextEnvelope`
- [x] 4.2 Route every compaction through the existing ordinary text-generation client without compact endpoints, compaction parameters, triggers or opaque response items
- [x] 4.3 Implement local extraction of pure/fenced JSON, bounded syntax repair and strict schema validation without requiring Provider JSON mode
- [x] 4.4 Implement Astra-owned root/conversation checkpoint generation with the active model and separately metered ordinary model usage
- [x] 4.5 Implement Astra-owned child checkpoint generation with contract/manifest binding, local facts and reference validation
- [x] 4.6 Implement role-specific deterministic emergency checkpoints from canonical state, verified references and bounded normalized observations
- [x] 4.7 Add portability tests proving the same Astra checkpoint can continue across supported ordinary generation Providers after budget recalculation

## 5. Tool Output Governance

- [x] 5.1 Add role-aware inline byte and Token limits to normalized tool outcomes before they enter root or child model context
- [ ] 5.2 Persist oversized complete outputs in the existing ToolCall/Artifact/Evidence storage with checksum, provenance, identity and data-purpose controls
- [x] 5.3 Replace oversized observations with bounded previews, stable references, status, key fields and classified error metadata
- [x] 5.4 Fail through classified storage/recovery paths when a required large output cannot be persisted instead of silently truncating it
- [x] 5.5 Verify root and child compaction requests consume normalized bounded observations rather than raw external payloads

## 6. Root Agent Loop Integration

- [ ] 6.1 Build the standard-root protected prefix from current request, authorization boundary, active Skills, budget and current verified runtime state without creating trusted-only structures
- [ ] 6.2 Build the trusted-root protected prefix from TaskContract, Profile/Skill snapshot, permissions, Plan/AgentState versions, budget and Completion Gate inputs
- [ ] 6.3 Insert pre-model context-pressure checks into all standard and trusted decision/model-call paths
- [ ] 6.4 Insert post-tool checks after result persistence/normalization and before any follow-up model call
- [ ] 6.5 Inject `RootContextCheckpointV2` plus retained recent observations into model context while continuing to evaluate completion from canonical state
- [ ] 6.6 Preserve action idempotency, waiting continuations, cancellation, Plan node state and Evidence lineage across root compaction and recovery
- [ ] 6.7 Integrate verified child fan-in results as references in root checkpoints without promoting unaccepted child local facts

## 7. Child Agent Loop Integration

- [ ] 7.1 Rebuild the child protected prefix each window from DelegationContract, role protocol, attenuated permissions/catalogs, Workspace scope, local Plan, budget and termination rules
- [ ] 7.2 Replace the unused `SubagentContextCheckpointService.compress()` wrapper with the shared child compaction policy and strict checkpoint generator
- [ ] 7.3 Include the active child checkpoint and retained observations in every child model context instead of persisting an unread `local_summary`
- [ ] 7.4 Add child pre-model, post-tool and post-recovery pressure checks using its independent window and body-after-prefix baseline
- [ ] 7.5 Validate all child checkpoint Evidence/Artifact refs against child identity, data labels, purposes, contract hash and manifest hash
- [ ] 7.6 Preserve bounded structured continuation answers and remaining budget across child compaction and waiting-parent recovery
- [ ] 7.7 Return classified budget-limited, waiting or blocked `SubagentResult` when child protected context cannot fit, without exposing or promoting private state

## 8. Conversation Compaction and Migration

- [ ] 8.1 Replace `_build_summary()` character-tail truncation with conversation policy input built from complete eligible Run/audit records and any prior checkpoint
- [ ] 8.2 Update automatic pre-Run compaction to use Astra semantic checkpoints, Token-bounded recent Runs and the recovery-waterline postcondition
- [ ] 8.3 Update `/compact` to invoke the shared semantic engine idempotently on idle conversations and leave the old projection unchanged on failure
- [ ] 8.4 Add lazy V1 `summary/folded_run_ids` migration as unverified legacy input while retaining all original Runs and rollback-readable fields
- [ ] 8.5 Update conversation rendering to inject canonical prefix, one active checkpoint and recent Runs without nested `Earlier conversation summary` accumulation
- [ ] 8.6 Extend context status/API schemas with reported-versus-estimated usage, implementation, window number, checkpoint status, retained/folded counts and Token before/after
- [ ] 8.7 Update the context capacity UI and command results to disclose Astra semantic/deterministic compaction status and classified failures without rendering hidden reasoning

## 9. Recovery, Observability and Safety Verification

- [ ] 9.1 Extend root and child recovery compatibility checks for schema/policy version, manifest/contract/catalog digests, generation metadata and reference accessibility
- [ ] 9.2 Regenerate Astra checkpoints from complete audit history and ContinuationManifest when a checkpoint is damaged or schema-incompatible
- [ ] 9.3 Add lifecycle events and analytics for trigger, role, implementation, reason, status, supersession, Token change, tail size, duration, model usage and cost
- [ ] 9.4 Add audit redaction tests proving compaction telemetry excludes hidden reasoning, credentials, secret payloads and inaccessible child data
- [ ] 9.5 Add concurrency tests for simultaneous observation commits, duplicate compaction requests, cancellation during compaction and Worker lease recovery
- [ ] 9.6 Add crash-point tests before request, after Provider response and before/after checkpoint installation, proving no repeated external tool effect

## 10. Quality Evaluation and Rollout

- [ ] 10.1 Add deterministic checkpoint fixtures covering user corrections, rejected approaches, exact paths/parameters, Plan progress, failures, open issues and next actions
- [ ] 10.2 Add child isolation fixtures proving parent history, sibling scratchpad, hidden reasoning, credentials and unrelated Memory never enter child checkpoints
- [ ] 10.3 Add repeated-compaction tests for at least three windows with checkpoint accumulation, recent-tail preservation and recovery-waterline enforcement
- [ ] 10.4 Add generic model malformed-output, retryable failure, deterministic emergency, model downshift and cross-Provider portability integration tests
- [ ] 10.5 Add end-to-end long tool-loop tests for standard root, trusted root, quick child and trusted child, including Artifact/Evidence reference recovery
- [ ] 10.6 Build an evaluation report comparing V2 against current character folding on task completion, key-field retention, reference validity, Token use, cost and latency
- [ ] 10.7 Run shadow mode and staged feature-flag rollout with explicit acceptance thresholds and rollback checks before disabling V1 writes
- [ ] 10.8 Update operator, architecture and user documentation for Astra-owned compaction behavior, generic model route configuration, cost, limitations, migration and diagnostics
