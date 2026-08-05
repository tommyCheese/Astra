## 1. Baseline and contracts

- [x] 1.1 Add characterization tests that capture current Web, Chart, and Bash ToolCall, event, approval, observation, result, and failure behavior
- [x] 1.2 Define versioned PluginDescriptor, PluginContribution, component identity, applicability binding, and lifecycle state schemas
- [x] 1.3 Define host interfaces for ToolProviderPlugin, ToolExecutor, EffectAnalyzer, ResultProcessor, Validator, ApprovalPresenter, RuntimeBackend, and health probes
- [x] 1.4 Extend ToolResultEnvelope with an explicit protocol version and implement strict envelope plus ToolSpec output-schema validation
- [x] 1.5 Add contract tests for unsupported protocol versions, malformed contributions, invalid envelopes, and safe diagnostics

## 2. Deterministic plugin catalog

- [x] 2.1 Implement built-in, managed-package, and isolated-descriptor discovery source interfaces without scanning Task Workspaces
- [x] 2.2 Implement provider identity, digest, trust-level, and allowlist verification before loading executable contributions
- [x] 2.3 Implement PluginCatalogBuilder with deterministic ordering and immutable tool and component binding indexes
- [x] 2.4 Reject duplicate model-visible tool names, component IDs, runtime backend IDs, and ambiguous applicability bindings
- [x] 2.5 Add provider lifecycle and bounded health state tracking for discovered, verified, loaded, healthy, enabled, disabled, unhealthy, and draining states
- [x] 2.6 Add tests proving Workspace plugin metadata is ignored, digest drift is rejected, conflicts fail closed, and equivalent inputs produce identical catalog digests

## 3. Generic invocation pipeline

- [x] 3.1 Introduce InvocationRequest, InvocationRuntimeContext, and InvocationOutcome models that preserve waiting-approval and recovery semantics
- [x] 3.2 Move ToolRouter resolution and input-schema validation behind the InvocationPipeline resolve stage
- [x] 3.3 Implement analyzer selection from frozen bindings with a conservative host declaration analyzer fallback
- [x] 3.4 Move Effect Plan hashing, permission authorization, approval creation, grant consumption, and approval revalidation into pipeline stages
- [x] 3.5 Implement executor backend selection and capability-limited context serialization for isolated providers
- [x] 3.6 Move tool execution, envelope validation, ToolCall completion, Artifact association, Workspace diff recording, and DataFlowState updates into pipeline stages
- [x] 3.7 Implement zero-to-many processor dispatch and return normalized observations, evidence fragments, validation inputs, and completion signals
- [x] 3.8 Add pipeline tests for allow, ask, deny, cancellation, timeout, invalid output, analyzer failure, processor failure, and checkpoint recovery

## 4. Agent Loop decoupling

- [x] 4.1 Replace the inline tool execution block in AgentLoop with InvocationPipeline invocation and generic outcome handling
- [x] 4.2 Remove WebTaskAdapter and ChartTaskAdapter construction from AgentLoop and inject frozen processor and validator bindings instead
- [x] 4.3 Replace the single Web evidence pack with an aggregate EvidenceBundle that supports multiple provider evidence fragments
- [x] 4.4 Run all applicable validators and aggregate blocking and non-blocking outcomes in CompletionGate
- [x] 4.5 Replace the `bash_execute` quick-completion name check with a generic trusted completion signal
- [x] 4.6 Add a synthetic third-party provider test proving a tool can execute, process, validate, and complete without modifying AgentLoop
- [x] 4.7 Add a mixed Web-plus-Chart Run test proving both validator outcomes are preserved and aggregated

## 5. Built-in provider migration

- [x] 5.1 Implement `astra.web` provider contributions for web tools, sandbox backend configuration, evidence processing, and Web validation
- [x] 5.2 Implement `astra.chart` provider contributions for chart rendering, Artifact processing, and Chart validation
- [x] 5.3 Implement `astra.shell` provider contributions for Bash execution, Bash effect analysis, safe approval preview, grant matching, and Workspace completion signals
- [x] 5.4 Add legacy result adapters inside each built-in provider and remove provider-specific output interpretation from core runtime modules
- [x] 5.5 Replace build_tool_registry with PluginCatalogBuilder-based built-in assembly while preserving current tool names and enablement defaults
- [x] 5.6 Remove concrete Web, Chart, and Bash tool-name branches from AgentLoop, DefaultEffectAnalyzer, approvals, and generic sandbox dispatch
- [x] 5.7 Run golden compatibility tests and document any intentional event or result contract changes

## 6. Run snapshot and persistence

- [x] 6.1 Extend the Tool Catalog Snapshot schema and migration with plugin, executor, analyzer, processor, validator, configuration revision, and resolved-binding identities
- [x] 6.2 Freeze the complete resolved catalog before a Run exposes manifests to the model or executes a tool
- [x] 6.3 Implement resume-time comparison that distinguishes behavioral identity drift from display-only metadata changes
- [x] 6.4 Fail closed when schema, executor, analyzer, processor, validator, provider digest, or permission-relevant configuration changes during a paused Run
- [x] 6.5 Preserve deserialization and presentation of historical ToolCalls and legacy catalog snapshots
- [x] 6.6 Add migration and recovery tests for approval resume, application restart, component drift, old snapshots, and display-only changes

## 7. Dynamic provider and tool settings

- [x] 7.1 Add generic provider, tool state, and provider configuration revision persistence models and migration
- [x] 7.2 Implement dynamic provider and tool catalog read APIs with labels, availability, health, enablement, schemas, and safe diagnostics
- [x] 7.3 Implement provider and tool enable/disable APIs with lifecycle transition validation and audit events
- [x] 7.4 Implement schema-validated provider configuration writes that persist credential references without returning secret values
- [x] 7.5 Add a one-version compatibility adapter for the legacy fixed-field Tool Settings API and emit deprecation metadata
- [x] 7.6 Update the frontend settings UI to render dynamic providers, tools, health state, and basic JSON Schema configuration fields
- [x] 7.7 Add API and UI tests for unknown providers, conflicts, unavailable backends, secret fields, enablement persistence, and legacy compatibility

## 8. External provider isolation

- [x] 8.1 Define a language-neutral request, result, health, cancellation, and error protocol for isolated provider transports
- [x] 8.2 Implement an isolated provider adapter with wall-time, response-size, concurrency, cancellation, network, credential, and host-service boundaries
- [x] 8.3 Prevent external providers from receiving unrestricted Artifact, Workspace, database, credential, or Sandbox service objects
- [x] 8.4 Add adversarial tests for oversized output, forged annotations, unauthorized service access, schema drift, cancellation, crash, and timeout
- [x] 8.5 Keep managed package discovery disabled by default until its configured identity and digest policy is covered by deployment tests

## 9. Rollout and cleanup

- [x] 9.1 Add startup diagnostics and metrics for provider discovery, verification, load, health, catalog conflicts, invocation stages, and contribution failures
- [x] 9.2 Add a rollout flag that restricts the catalog to built-in plugins and a rollback procedure that preserves new snapshots
- [x] 9.3 Run backend unit, integration, Docker, permission, recovery, API, and frontend suites with external discovery disabled and enabled
- [x] 9.4 Update the system design and operator documentation with plugin trust boundaries, managed source configuration, lifecycle, and troubleshooting
- [ ] 9.5 Remove legacy registry builders, static tool toggle fields, compatibility API, and unused adapter code after the announced compatibility window
- [x] 9.6 Verify no core AgentLoop, generic Effect Analyzer, approval, settings, or completion module branches on `web_search`, `web_fetch`, `chart.render`, or `bash_execute`
