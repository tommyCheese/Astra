## Context

Astra already separates canonical Plan nodes from ToolCall records, but the separation is incomplete:

- `required_capabilities` in generated Plans commonly contains concrete tool identities.
- Tool manifest `capabilities` defaults to a security authority such as `network_read`, so functional task capability and authorization authority are conflated.
- Engine and Plan revision validation add registered tool names to the "available capability" set.
- ContextAssembler, AgentLoop decision validation, and parallel Node Workers each implement their own `name | capability | permission` intersection.
- The model chooses a concrete tool during an execution turn, but the candidate set is therefore already statically narrowed by the Plan's tool identity.

The active Run still must use a frozen eligible catalog, effect analysis, permission checks, approval integrity, backend availability, budgets, and CompletionGate. Tool-agnostic planning cannot weaken any of those controls.

## Goals / Non-Goals

**Goals:**

- Make newly generated and revised Plans independent from concrete tool identities and providers.
- Give tools a provider-neutral functional vocabulary without changing the meaning of security capabilities or permissions.
- Resolve a deterministic, auditable set of candidate tools at execution time from node needs and current eligibility.
- Use one selector contract across serial and parallel execution.
- Allow the runtime to reject an invalid concrete selection and let the next bounded turn choose an alternative.
- Preserve historical Plan execution without a database rewrite.

**Non-Goals:**

- Automatically translating one tool's input into another tool's incompatible schema.
- Letting the planner authorize actions, select providers, bypass approval, or freeze a ToolCall.
- Replacing ToolRouter, PluginCatalog, InvocationPipeline, PermissionEngine, PlanScheduler, or CompletionGate.
- Installing tools during a Run or mutating its frozen catalog.
- Adding Deep Research, research-specific planning, or a domain-specific Web plan template.

## Decisions

### 1. Plans keep the existing field but change its contract to semantic needs

`PlanNode.required_capabilities` remains an additive-compatible `list[str]`, but new values describe task capabilities such as `information.search`, `information.read`, `data.visualize`, or `workspace.execute`. A new Plan validator receives the registered tool-name set and rejects any new or revised Plan that persists one of those names.

Keeping the field avoids a database migration and preserves API/UI compatibility. A separate structured requirement object was considered, but the current matching needs do not justify a second persisted Plan format.

### 2. Functional task capabilities are separate from security authorities

`ToolSpec` gains `task_capabilities`. Existing `capabilities`, `permissions`, `risk`, and backend fields keep their authorization and runtime-eligibility meaning. Built-in tools declare semantic task capabilities explicitly; plugin tools may add their own namespaced values.

This avoids adding functional strings to the security allowlist and makes it possible for two providers with different concrete tool names to satisfy the same Plan need.

### 3. One CapabilityToolResolver produces candidates; it does not execute

The shared resolver takes:

- the policy-filtered tools returned by ToolRouter;
- the active node's semantic requirements;
- optional safety constraints such as read-only/idempotent execution;
- optional excluded concrete tools;
- legacy exact-name compatibility.

It returns a typed resolution containing requirements, ordered candidates, matched capabilities, unresolved capabilities, unavailable reasons, and whether legacy matching was used. Candidates are ordered deterministically by requirement coverage, side-effect/risk preference, provider identity, and tool identity.

When a node has no task-capability requirement, all currently eligible manifests remain candidates. This is necessary for reasoning-only nodes and for goals whose tool need becomes clear only after dependency evidence arrives.

For a multi-capability node, successful ToolCalls associated with that node accumulate semantic capability coverage. Subsequent turns resolve candidates against the remaining requirements. Node completion is rejected while a declared required capability remains unsatisfied; changing the concrete implementation does not require a Plan rewrite.

### 4. The model chooses the concrete tool only during an execution turn

ContextAssembler and the parallel Node Worker expose only resolver candidates. The model still emits a concrete `call_tool` decision because tool input must conform to that concrete manifest. AgentLoop validates the proposed tool against the resolution before effect analysis or persistence.

Automatic silent substitution was rejected: two tools advertising the same task capability may have incompatible inputs, costs, side effects, or semantics. An out-of-candidate choice becomes a typed observation containing safe candidate identities and unresolved needs; the next bounded turn may choose again, replan, ask the user, or block.

### 5. Selection remains upstream of all existing security controls

Candidate resolution is not authorization. After the model chooses a candidate, the existing ToolRouter input validation, effect analyzer, PermissionEngine, approval flow, sandbox/backend checks, ToolCall persistence, and result validation run unchanged. Approved resumes continue to freeze a concrete tool, input, effect plan, and integrity digest.

### 6. Serial and parallel paths share matching semantics

ContextAssembler and `ReadOnlyAgentNodeExecutor` use the same resolver. The parallel path supplies a constraint requiring read-only, idempotent tools. If a node can only be fulfilled by a side-effecting candidate, it retains the existing deterministic serial fallback rather than executing unsafely in a Worker.

PlanScheduler continues using semantic capability keys for concurrency accounting. Provider-specific limits remain runtime metadata and are not written into the logical Plan.

### 7. Historical Plans use a narrow compatibility adapter

For an already persisted Plan, a requirement that exactly equals a frozen tool identity may match only that tool and marks the resolution `legacy_tool_binding=true`. New Plan creation, model normalization, reflection PlanPatch validation, and user-requested Plan revision reject concrete tool names.

Rollback can disable new-plan enforcement while retaining additive `task_capabilities`; historical storage needs no downgrade.

### 8. The mock planner becomes goal-oriented rather than Web-shaped

The mock implementation generates logical analyze, fulfill, and verify nodes. It infers only semantic capability needs for deterministic tests. Real planner instructions explicitly prohibit tool/provider names and describe capabilities as stable semantic needs.

## Risks / Trade-offs

- [A tool declares an inaccurate semantic capability] → Validate naming/shape, expose the declaration in the frozen catalog, and keep effect/permission validation authoritative after selection.
- [Multiple candidates match but differ materially] → Do not auto-substitute; expose full eligible manifests and let the execution decision select with current evidence.
- [A semantic requirement is too broad] → Return deterministic candidates plus matched capability details; outcome evaluation still decides whether the node actually succeeded.
- [A multi-capability node completes after only one operation] → Track per-node coverage from successful ToolCalls and reject completion while required capabilities remain unresolved.
- [A new planner emits a concrete tool name] → Reject the Plan and use the existing bounded planning fallback rather than persisting the binding.
- [Historical exact-name requirements remain coupled] → Limit exact-name matching to loaded legacy Plans and mark it in audit output.
- [Serial and parallel candidate sets drift] → Put matching in one resolver and test both call sites against the same fixtures.
- [Empty requirements expose many tools] → ToolRouter still applies frozen catalog, policy, permission authority, risk, and backend eligibility before manifests enter model context.

## Migration Plan

1. Add semantic task-capability metadata and the shared resolver without changing persisted Plan fields.
2. Declare built-in tool task capabilities and test multiple equivalent providers.
3. Update planning prompts, mock planning, normalization, creation, patch, and revision validation to prohibit concrete names.
4. Replace serial and parallel ad hoc matching with the shared resolver and add selection audit context/events.
5. Add legacy exact-name execution tests and new-plan rejection tests.
6. Run full backend/frontend suites and strict OpenSpec validation.

## Open Questions

- A future catalog may add cost, latency, quality, locality, or user-preference scores to candidate ranking; this change keeps ranking deterministic and policy-neutral.
- A future decision contract could request a capability plus abstract arguments and let an adapter choose the concrete schema, but that requires a typed cross-tool input ontology and is intentionally deferred.
