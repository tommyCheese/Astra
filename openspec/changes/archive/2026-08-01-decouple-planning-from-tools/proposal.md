## Why

Astra's canonical Plan nodes do not store a `tool_name`, but their `required_capabilities` currently contain concrete names such as `web_search` and `web_fetch`. This makes planning depend on whichever tools happen to be installed when the plan is generated, duplicates matching logic across serial and parallel runtimes, and prevents equivalent providers or future plugins from being selected dynamically.

## What Changes

- Make new task plans describe logical work, expected outcomes, semantic task capabilities, dependencies, risk, and success criteria without naming or preferring a concrete tool.
- Add provider-neutral semantic task-capability declarations to tool manifests, separate from security authorities and permissions.
- Add one shared capability-driven tool selector that derives the eligible candidate set from the active Plan node, frozen/available catalog, policy, backend, and prior execution state.
- Let the model select a concrete tool only at execution time from the eligible candidates; reject an out-of-scope selection with an auditable observation so the next turn can choose another candidate.
- Use the same selector in serial AgentLoop context assembly and parallel Node Workers.
- Preserve historical Plans whose capability list contains a tool identity through a read/execute compatibility path, while preventing newly generated or revised Plans from persisting tool identities as capability requirements.
- Record candidate resolution and the ultimately selected concrete ToolCall without moving permission, effect, approval, or CompletionGate decisions into the planner.

## Capabilities

### New Capabilities

- `dynamic-tool-selection`: Semantic tool capability declaration, execution-time candidate resolution, auditable selection, rejection, and alternative selection behavior.

### Modified Capabilities

- `general-agent-reasoning`: Plan DAG nodes become tool-agnostic logical work declarations; concrete tool selection belongs to an execution decision, not planning.
- `policy-driven-tool-runtime`: Tool manifests expose semantic task capabilities and both serial and parallel runtimes resolve candidates through one policy-aware selector.
- `web-agent-loop`: Remove the obsolete Web-only concrete allowlist from the general Run loop while preserving Web behavior through semantic candidates.
- `source-summary-task`: Allow any eligible discovery/read implementation to fulfill a Web summary rather than requiring Google in the logical workflow.

## Impact

- Backend Plan normalization/validation, model planning prompts, ToolSpec manifests, ToolRouter integration, ContextAssembler, AgentLoop, Plan revision, parallel Node Worker, and related audit events are affected.
- Existing `required_capabilities` persistence and API fields remain readable; no database migration is required.
- Built-in Web, chart, and shell tools gain additive semantic task-capability metadata.
- Historical Runs and approved ToolCalls keep their concrete tool identities, while new Plans no longer encode those identities.
- Evidence Grounding, Deep Research, Skills, permissions, approvals, plugin result processing, and CompletionGate remain downstream consumers or independent policies and do not become planning dependencies.
