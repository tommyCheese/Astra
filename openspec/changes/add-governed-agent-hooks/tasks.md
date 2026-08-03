## 1. Baseline and contracts

- [ ] 1.1 Add characterization tests for Run, prompt, model, tool, approval, compaction, subagent, completion, recovery, cancellation, and event ordering with no Hooks configured
- [ ] 1.2 Define versioned `HookManifest`, `HookBinding`, handler descriptor, selector, decision-capability, effective-policy, and configuration schemas
- [ ] 1.3 Define the event catalog and versioned event-specific payload schemas for every v1 lifecycle event
- [ ] 1.4 Define `HookEventEnvelopeV1`, correlation/causation identities, data labels, reference manifest, delivery metadata, and canonical serialization/digest rules
- [ ] 1.5 Define versioned admission result, observation acknowledgement, safe diagnostic, execution status, and dead-letter reason schemas
- [ ] 1.6 Add schema fixtures and compatibility tests for additive fields, unsupported major versions, unknown event types, invalid capabilities, and oversized payloads

## 2. Verified Hook catalog and trust

- [ ] 2.1 Implement verified built-in and administrator-managed Hook discovery sources without scanning Task Workspaces
- [ ] 2.2 Reuse managed extension identity, digest, lifecycle, configuration revision, and health primitives while keeping a separate `HookCatalog`
- [ ] 2.3 Implement selector compilation for exact event, tool identity, capability, agent scope, status, and data-label matches
- [ ] 2.4 Implement deterministic ordering by trust tier, priority, Hook identity, version, and digest
- [ ] 2.5 Reject duplicate identities, unsupported protocols, invalid handler/capability combinations, conflicting schemas, and policy-ineligible bindings
- [ ] 2.6 Derive effective failure policy, timeout, output limit, data access, handler types, origins, effects, and rollout from manifest requests intersected with managed policy
- [ ] 2.7 Add catalog digest, conflict, managed-only, Workspace-ignore, source drift, configuration drift, and equivalent-input determinism tests

## 3. Persistence, migrations, and Run snapshots

- [ ] 3.1 Add database models and migration for Hook definitions, immutable versions, source identity, trust, configuration, lifecycle, and health
- [ ] 3.2 Add Run Hook snapshot and binding records containing resolved selectors, handler/schema/config digests, ordering, capabilities, limits, and effective policies
- [ ] 3.3 Add Hook execution records containing event/binding identity, input and output digests, status, decision, duration, attempt, causation, and redacted diagnostics
- [ ] 3.4 Add Hook outbox, claim/fencing lease, retry schedule, terminal dead-letter, and replay-lineage records and indexes
- [ ] 3.5 Implement repositories with idempotent creation, compare-and-swap state transitions, bounded payload persistence, retention classification, and secret exclusion
- [ ] 3.6 Freeze Hook snapshots at Run creation and validate them during admission, approval resume, recovery, cancellation, and observation delivery
- [ ] 3.7 Treat legacy Runs without Hook snapshots as an empty Hook set and add forward/backward migration and rollback tests

## 4. Admission dispatcher and composition

- [ ] 4.1 Implement a no-Hook fast path and a dispatcher that resolves bindings only from the Run's frozen Hook snapshot
- [ ] 4.2 Implement event envelope construction with least-privilege projections, stable references, data-label filtering, and payload-size enforcement
- [ ] 4.3 Implement sequential admission execution without holding a database transaction across handler calls
- [ ] 4.4 Implement restriction-only aggregation with platform and managed precedence, deny-over-ask semantics, and allow normalized to continue
- [ ] 4.5 Implement bounded context additions with provenance, purpose, data labels, token limits, and all-or-nothing validation
- [ ] 4.6 Implement the allowed RFC 6902 input-patch subset, protected-field rules, deterministic application, conflict detection, and one-round mutation cap
- [ ] 4.7 Implement completion-block fingerprinting, remediation observations, per-Run/Hook block caps, and interactive versus unattended terminal disposition
- [ ] 4.8 Implement causation depth, same-chain re-entry suppression, control-plane mutation denial, and recursion diagnostics
- [ ] 4.9 Add composition tests for mixed trust tiers, priorities, deny/ask/continue, context limits, patch conflicts, timeouts, cancellation, and recursion

## 5. Hook principals, permission integration, and handlers

- [ ] 5.1 Add immutable Hook principals and compile effective permissions from source policy, manifest ceiling, event capability, scope, labels, runtime, network, and credential references
- [ ] 5.2 Route Hook filesystem, Artifact/Evidence reads, HTTP egress, notifications, credentials, and other side effects through the unified Permission Engine
- [ ] 5.3 Prevent Hook principals from borrowing Agent/tool Grants, self-approving, widening delegated authority, or modifying protected Hook and audit resources
- [ ] 5.4 Implement the platform/managed in-process handler adapter with narrow service interfaces and no request-scoped repository access
- [ ] 5.5 Implement the isolated-command handler using exec argv, structured stdin/stdout, runtime profiles, read-only/default Workspace projection, environment allowlist, deadlines, cancellation, and resource limits
- [ ] 5.6 Implement the restricted-HTTP handler with HTTPS/service identity policy, DNS/IP rebinding protection, redirect rules, credential references, idempotency headers, deadlines, and body limits
- [ ] 5.7 Implement common response parsing, schema validation, stdout/stderr/body redaction, output truncation, and safe error classification for all handlers
- [ ] 5.8 Enforce fail-closed for security, authorization, compliance, and mutation admission, fail-open-with-audit only when managed policy permits, and retry-only semantics for observation
- [ ] 5.9 Add permission attenuation, self-approval, protected-control-plane, RCE, shell injection, environment leakage, credential leakage, SSRF, DNS rebinding, redirect, output bomb, timeout, and cancellation tests

## 6. Lifecycle integration

- [ ] 6.1 Dispatch `run.before_start` before execution begins and persist `run.started`, `run.failed`, and `run.cancelled` observation outbox entries at canonical transitions
- [ ] 6.2 Dispatch `prompt.before_accept` before Run input acceptance, retain the original prompt canonically, and install only validated context additions
- [ ] 6.3 Dispatch bounded `model.before_request` context admission and `model.responded` or `model.failed` observations without exposing protected prefix or provider secrets
- [ ] 6.4 Insert `tool.before_authorize` after resolution and initial schema validation and before trusted Effect analysis in `InvocationPipeline`
- [ ] 6.5 On accepted tool patches, invalidate prior candidate state, revalidate the full schema, create a new digest, re-run trusted Effect analysis, freeze the Effect Plan, and obtain canonical authorization
- [ ] 6.6 Persist `tool.execution_started`, `tool.succeeded`, `tool.failed`, and `tool.blocked` projections without allowing post Hooks to mutate canonical outcomes
- [ ] 6.7 Emit `approval.requested` and `approval.decided` observations with safe previews, reviewer identity, frozen Hook provenance, and resume-integrity checks
- [ ] 6.8 Integrate `context.before_compact`, `context.compacted`, and `context.compaction_failed` with the shared compaction boundary without allowing Hook mutation of protected prefixes or checkpoints
- [ ] 6.9 Dispatch `subagent.before_start` with attenuation-only contract changes and emit `subagent.started` and `subagent.stopped` from canonical supervision transitions
- [ ] 6.10 Dispatch `run.before_complete` before Completion Gate commit and emit `run.completed` only after the gate and canonical Run transition succeed
- [ ] 6.11 Add end-to-end lifecycle ordering, approval pause/resume, patched-tool exactly-once, compaction, concurrent subagent, completion block, cancellation, and process-restart tests

## 7. Reliable observation delivery and telemetry

- [ ] 7.1 Write canonical lifecycle occurrence and matching observation outbox rows atomically with the originating state transition
- [ ] 7.2 Implement a supervised outbox worker with bounded concurrency, claim leases, fencing tokens, delivery deadlines, exponential backoff, and shutdown drain
- [ ] 7.3 Use `(event_id, hook_binding_digest)` as the delivery idempotency identity and preserve it across transport retries
- [ ] 7.4 Implement dead-letter transition, safe payload references, terminal diagnostics, authorized replay, and replay lineage without repeating canonical actions
- [ ] 7.5 Recover expired claims and pending deliveries after restart and prevent stale workers from committing results
- [ ] 7.6 Add metrics and traces for catalog resolution, admission latency, decisions, failure policy, handler runtime, payload sizes, outbox lag, attempts, dead letters, replay, and suppressed recursion
- [ ] 7.7 Add load and fault-injection tests for event bursts, handler slowness, transport outages, worker crashes, duplicate delivery, fencing, shutdown, retention, and database backpressure

## 8. APIs, UI, and compatibility import

- [ ] 8.1 Add authorized Hook Catalog and detail APIs exposing source, digest, scope, selector, capabilities, effective policies, health, configuration schema, and safe diagnostics
- [ ] 8.2 Add protected create/install, configure, enable, disable, rotate, and delete APIs with optimistic concurrency and managed-policy enforcement
- [ ] 8.3 Add side-effect-free dry-run APIs for synthetic events, selector resolution, effective policy, input/output schema validation, patch conflicts, and simulated result parsing
- [ ] 8.4 Add execution, latency, failure, outbox, dead-letter, and replay APIs with pagination, redaction, retention, authorization, and audit
- [ ] 8.5 Add a Hook management UI for sources, review status, effective authority, runtime configuration, health, enablement, dry-run, executions, and failures
- [ ] 8.6 Add Run timeline presentation for Hook allow-as-continue, ask, deny, patch, context, timeout, conflict, retry, dead-letter, replay, and completion-block events
- [ ] 8.7 Implement an inert Claude Code/Copilot command-Hook parser and map the supported event subset into native import previews
- [ ] 8.8 Require explicit immutable installation, runtime selection, capability/effect/data review, and digest acceptance before an imported candidate can be enabled
- [ ] 8.9 Surface unmapped events, matchers, handler types, environment assumptions, exit codes, and semantic differences instead of silently approximating them
- [ ] 8.10 Add API authorization, CSRF/concurrency, UI accessibility, i18n, secret redaction, import injection, and Workspace non-execution tests

## 9. Rollout, validation, and documentation

- [ ] 9.1 Add deployment flags for an empty Catalog, platform-observation-only, managed admission, external command, restricted HTTP, user scope, compatibility import, and emergency external-Hook disable
- [ ] 9.2 Implement startup and runtime diagnostics for mandatory Hook absence, unhealthy backends, digest drift, schema incompatibility, policy conflicts, and unavailable frozen handlers
- [ ] 9.3 Run shadow admission against representative Runs and compare decisions, latency, Effect Plans, approvals, results, completion, and failure rates without enforcing Hook output
- [ ] 9.4 Define canary gates for admission p95/p99 latency, timeout and fail-open rates, denial/ask anomalies, outbox lag, dead letters, Run success, and recovery correctness
- [ ] 9.5 Add a break-glass procedure that is itself protected and audited, preserves mandatory security semantics, and distinguishes disabling external automation from bypassing policy
- [ ] 9.6 Document event schemas, handler contracts, selector and composition rules, permission attenuation, failure policies, idempotency, testing, import differences, operations, and threat model
- [ ] 9.7 Update Astra system design and operator documentation with Hook trust boundaries, data flow, database lifecycle, observability, retention, recovery, rollback, and troubleshooting
- [ ] 9.8 Verify the full backend and frontend suites with no Hooks configured and prove existing tool, approval, compaction, subagent, completion, streaming, and cancellation behavior remains compatible

