## 1. Skill Package Contracts and Validation

- [x] 1.1 Add versioned `SkillPackage`, frontmatter, resource manifest, built-in/custom origin, compatibility, and validation diagnostic models.
- [x] 1.2 Implement strict YAML frontmatter parsing and Agent Skills name, description, optional field, directory-name, UTF-8, size, and file-count validation.
- [x] 1.3 Implement root-confined path normalization that rejects traversal, unsafe links, special files, unsupported kinds, and paths outside the staged package.
- [x] 1.4 Implement deterministic package and per-resource SHA-256 digests over normalized sorted paths and bytes.
- [x] 1.5 Add fixture packages and unit tests for minimal, complete, malformed, oversized, escaping, binary, reserved-identity, and digest-drift cases.

## 2. Shared Skill and Revision Persistence

- [x] 2.1 Add content-addressed Skill blob storage with immutable file revisions and deduplication by digest.
- [x] 2.2 Add database models and migration for globally shared Skill records, `builtin|custom` origin, enabled state, mutable Draft revision, active Published Revision, history, diagnostics, and tombstones.
- [x] 2.3 Add repositories for atomic Draft file sets, optimistic revision tokens, publication, historical restore-to-Draft, enable/disable, recoverable removal, and export.
- [x] 2.4 Implement an Astra release loader that registers immutable built-in revisions and rejects modification or reserved-namespace replacement.
- [x] 2.5 Add migration, repository, deduplication, built-in immutability, publication concurrency, retention, and export round-trip tests.

## 3. Custom Skill Import, Creation, and Safety

- [x] 3.1 Implement bounded folder/archive import that creates a custom Draft without publishing or executing content.
- [x] 3.2 Implement custom Skill creation and built-in-to-custom clone using the same Draft storage and validation pipeline.
- [x] 3.3 Add non-executing safety inspection for path risks, executables, obfuscation indicators, unexpected binaries, policy-bypass/exfiltration patterns, and undeclared compatibility needs.
- [x] 3.4 Block publish and executable Draft testing on required critical diagnostics or unavailable required scanners while keeping editing and safe preview available.
- [x] 3.5 Add tests for invalid archives, zip bombs, duplicate identities, reserved identities, scanner failure, critical findings, clone behavior, and safe Draft recovery.

## 4. Virtual Skill Filesystem and Editing APIs

- [x] 4.1 Add root-confined virtual Draft file APIs for list, read, create, batch write, move, rename, and delete without exposing server filesystem paths.
- [x] 4.2 Use stable `skill-draft://` and revision URIs and return media type, digest, revision token, readonly state, and diagnostics for each file.
- [x] 4.3 Implement atomic batch autosave with Draft-level optimistic concurrency and stale-write conflict payloads.
- [x] 4.4 Add package validation, safe Markdown preview, Draft/Published Diff, revision history, restore, and portable export APIs.
- [x] 4.5 Add API tests for traversal, stale tokens, partial batch failure, binary handling, read-only built-ins, preview sanitization, Diff, history, restore, and export.

## 5. Monaco Skill Authoring Workbench

- [x] 5.1 Add a separate Skill Library route listing built-in and custom Skills by origin, state, active revision, compatibility, and diagnostics without user/tenant/scope controls.
- [x] 5.2 Integrate Monaco Editor with virtual Skill URIs, multi-tab models, dirty/saved state, undo/redo, keyboard navigation, and cleanup of disposed models.
- [x] 5.3 Add a multi-file tree with create, move, rename, delete, search, and file-type-aware open behavior.
- [x] 5.4 Add Markdown source/preview split view, safe rendering, frontmatter diagnostics, and source-range navigation.
- [x] 5.5 Add language configuration and bounded diagnostics for Markdown, YAML, JSON, Python, JavaScript, TypeScript, and Shell text files without executing code.
- [x] 5.6 Add autosave, transient failure recovery, stale-revision three-way comparison, Draft/Published Diff, revision history, and restore-to-Draft.
- [x] 5.7 Add create, import, clone built-in, validate, publish, enable/disable, export, and recoverable remove flows.
- [x] 5.8 Add component and end-to-end tests for multi-file editing, read-only built-ins, autosave conflicts, preview safety, publication, history, accessibility, and responsive layout.

## 6. Eligible Catalog and Run Skill Snapshot

- [x] 6.1 Implement deterministic `SkillCatalogBuilder` using enabled built-in revisions and enabled custom active Published Revisions filtered by compatibility and runtime capabilities.
- [x] 6.2 Implement origin-qualified identities, collision rejection, stable ordering, metadata token accounting, and Catalog digest generation.
- [x] 6.3 Implement a deterministic name/description shortlist for Catalogs that exceed the configured model-context metadata budget.
- [x] 6.4 Add database models and migration for immutable Run Skill Catalog snapshots, durable revision references, resource manifests, answer mode, activation history, and Draft-test markers.
- [x] 6.5 Freeze the Skill Catalog before the first model operation and reconstruct it from durable content rather than live Draft or active-revision pointers.
- [x] 6.6 Add tests for reproducible Catalogs, identity conflicts, shortlist stability, snapshot reconstruction, republish during Run, missing blobs, and digest mismatch.

## 7. Skill Activation and Progressive Disclosure

- [x] 7.1 Extend the model decision protocol with structured `activate_skill` accepting only frozen qualified identities.
- [x] 7.2 Implement explicit Composer/user-request pre-activation without bypassing eligibility, revision, compatibility, budget, or revocation checks.
- [x] 7.3 Implement `SkillActivationService` lifecycle, initiator/reason, deterministic multi-Skill ordering, deactivation, and conflict events.
- [x] 7.4 Implement `read_skill_resource` for active snapshot URIs with root, digest, media type, size, byte-budget, and resource-kind checks.
- [x] 7.5 Return a bounded resource inventory at activation without eagerly loading bodies, and reject instruction truncation when the instruction budget is insufficient.
- [x] 7.6 Add tests for automatic and explicit activation, unavailable Skills, multiple Skills, conflicts, invalid resource paths, digest drift, revocation, and quotas.

## 8. Prompt Composition and Runtime Propagation

- [x] 8.1 Extend `PromptComposer` with individually delimited Skill blocks and explicit platform/Profile/role/administrator/Skill/runtime-context precedence framing.
- [x] 8.2 Select only Skills applicable to each planner, controller, reflector, answer, memory, and verification operation and record the operation-to-activation association.
- [x] 8.3 Preserve active Skill identities through context compaction while reloading resource bodies only when still required.
- [x] 8.4 Propagate an attenuated Skill Catalog and active subset into `NodeContextSnapshot` and delegated execution.
- [x] 8.5 Keep ordinary Run summaries compact while allowing dedicated Skill and audit detail APIs to retrieve authorized full content.
- [x] 8.6 Add prompt golden tests for hierarchy, origin/revision boundaries, explicit-instruction conflicts, claimed authority, inactive omission, multi-Skill ordering, compaction, and summary redaction.

## 9. Quick-Response Skill Integration

- [x] 9.1 Expose frozen Skill discovery metadata to the standard quick controller without creating TaskContract, Plan, PlanNode, PlanEdge, or trusted AgentState.
- [x] 9.2 Support explicit pre-activation and controller-selected activation inside the quick decide/tool/finalize loop.
- [x] 9.3 Preserve all shared Tool schema, Effect, approval behavior, Sandbox, Artifact, cancellation, error, and budget boundaries for Skill-guided quick actions.
- [x] 9.4 Add a non-switching recommendation when a Skill workflow appears to require trusted planning or strong verification.
- [x] 9.5 Add behavior tests proving quick Skill Runs and quick Draft tests never create trusted planning or Completion Gate records and never silently switch answer mode.

## 10. Trusted-Execution Skill Integration

- [x] 10.1 Add a Skill Resolution phase before trusted TaskContract and initial complete Plan DAG generation.
- [x] 10.2 Persist selected Skill identities and revision digests in TaskContract, trusted AgentState, Plan metadata, and success-criterion provenance.
- [x] 10.3 Add `required_skill_ids` bindings to applicable Plan nodes and reconstruct only the attenuated subset for each NodeExecution.
- [x] 10.4 Require a valid PlanPatch/replan when a previously inactive frozen-Catalog Skill is needed after Plan persistence.
- [x] 10.5 Map accepted mandatory Skill checks to stable success criteria and evidence requirements evaluated by the normal trusted Completion Gate.
- [x] 10.6 Preserve Plan confirmation and effect approval behavior independently from Skill activation.
- [x] 10.7 Add tests for pre-plan resolution, node attenuation, parallel nodes with different Skills, late activation/replan, completed evidence preservation, and completion verification.

## 11. Draft Test Runs

- [x] 11.1 Add an API that freezes the current Draft revision into a non-catalog test snapshot with a required test prompt and explicit `standard|trusted` answer mode.
- [x] 11.2 Add Workbench controls to validate and start quick or trusted Draft tests and link to their chat/audit views.
- [x] 11.3 Route Draft-test resources and scripts through the same activation, Tool Catalog, Invocation Pipeline, approval, Sandbox, Artifact, and budget controls as ordinary Runs.
- [x] 11.4 Clearly label Draft test Runs, exclude their snapshot from ordinary Catalogs, and retain the exact test digest after further editing.
- [x] 11.5 Add integration tests for quick and trusted Draft tests, concurrent Draft edits, critical diagnostic blocking, snapshot isolation, and ordinary Catalog exclusion.

## 12. Skill-Driven Tool and Sandbox Execution

- [x] 12.1 Normalize `allowed-tools` and compatibility declarations into non-authoritative requested-capability metadata and missing-capability diagnostics.
- [x] 12.2 Add immutable Published Revision and Draft-test resource bindings to `ToolExecutionContext` without exposing storage paths.
- [x] 12.3 Materialize bundled scripts and assets as digest-checked read-only inputs for eligible sandbox providers.
- [x] 12.4 Route every Skill-driven script, command, dependency action, file change, network call, credential use, artifact, and result through the standard Invocation Pipeline.
- [x] 12.5 Link Skill-attributed tool calls to activation, revision, effect plan, approval, workspace changes, artifacts, validators, Plan nodes, and completion outcomes.
- [x] 12.6 Block writes to frozen Skill storage and track template copies or transformations as ordinary Task Workspace changes.
- [x] 12.7 Add integration tests proving edit/open/preview/publish/activation have no side effect, `allowed-tools` grants nothing, scripts cannot execute in-process, and sandbox limits apply.

## 13. Audit, Revocation, Budgets, and Chat UX

- [x] 13.1 Add settings for Catalog, activation, instruction, resource, Draft-test, script, execution, and artifact budgets.
- [x] 13.2 Enforce emergency revision revocation so frozen content remains inspectable while new Skill-attributed executable or external actions are blocked.
- [x] 13.3 Emit structured events for import, edit, validation, publish, Catalog freeze, activation, disclosure, resource read, conflict, attributed action, Plan binding, test, and revocation.
- [x] 13.4 Add safe metrics for Catalog size, tokens, activation outcomes, resource bytes, Draft tests, attributed execution, and diagnostic categories.
- [x] 13.5 Add Composer automatic/explicit Skill selection for both answer modes and compact timeline events for activation, conflicts, resource use, trusted Plan revision, and attributed approvals.
- [x] 13.6 Extend the audit drawer to show origin, exact revision/test digest, activation, resource reads, tool calls, Plan bindings, policy outcomes, and historical recovery data.
- [x] 13.7 Add budget, revocation-race, secret-redaction, audit-linkage, Composer, approval attribution, and historical-inspection tests.

## 14. Rollout, Compatibility, and Verification

- [x] 14.1 Add feature flags that enable storage and built-in loading before custom editing/publishing and mode integration.
- [x] 14.2 Ship at least one read-only built-in documentation Skill and one sandbox-script Skill as Astra release fixtures.
- [x] 14.3 Add compatibility tests using packages validated by Agent Skills reference rules and verify Astra exports re-import unchanged.
- [x] 14.4 Run backend unit/integration suites, frontend tests, migrations, lint, type checks, production build, and focused security regressions.
- [x] 14.5 Measure context cost, shortlist behavior, activation accuracy, quick latency, trusted pre-plan latency, resume determinism, and Draft-test isolation.
- [x] 14.6 Document authoring, Draft/publication, built-in/custom behavior, Monaco limitations, modes, permissions, sandbox, troubleshooting, APIs, rollout, and rollback.
- [x] 14.7 Verify non-Skill Runs, existing Agent Profile snapshots, Tool Catalog snapshots, approvals, parallel DAG execution, answer-mode invariants, and legacy clients remain compatible.
