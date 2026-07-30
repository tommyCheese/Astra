## 1. Semantic Capability Contracts

- [ ] 1.1 Add provider-neutral `task_capabilities` to ToolSpec without changing security capability or permission semantics
- [ ] 1.2 Declare semantic task capabilities for built-in Web, chart, and shell tools
- [ ] 1.3 Implement typed deterministic capability resolution, progress, unresolved-needs, safety constraints, and legacy exact-name matching
- [ ] 1.4 Add focused resolver tests with multiple equivalent providers, exclusions, partial multi-capability progress, and empty requirements

## 2. Tool-Agnostic Planning

- [ ] 2.1 Update real and mock planning instructions/output so logical Plans use semantic requirements and never concrete tool/provider names
- [ ] 2.2 Validate semantic capability availability separately from registered tool identities and reject new concrete-name bindings
- [ ] 2.3 Apply the same validation to initial Plan creation, reflection PlanPatch, and user-requested Plan revision without silently dropping needs
- [ ] 2.4 Add planning normalization, fallback, revision, and historical compatibility tests

## 3. Dynamic Serial Execution

- [ ] 3.1 Replace ContextAssembler's ad hoc name/permission intersection with shared candidate resolution and expose safe selection/progress context
- [ ] 3.2 Validate each concrete Agent decision against the active resolution before effect analysis and emit auditable alternative-selection observations
- [ ] 3.3 Prevent node completion while declared semantic requirements remain unresolved and accumulate coverage from successful node ToolCalls
- [ ] 3.4 Preserve ToolRouter, permission, approval, backend, budget, ToolCall, result-processing, verification, and CompletionGate ordering
- [ ] 3.5 Add serial runtime tests for equivalent candidates, unrelated-tool rejection, alternative selection after failure, capability gaps, and multi-step coverage

## 4. Dynamic Parallel Execution

- [ ] 4.1 Make read-only Node Workers use the shared resolver with read-only/idempotent safety constraints
- [ ] 4.2 Track semantic capability progress within a parallel node and reject premature completion
- [ ] 4.3 Preserve deterministic serial fallback when only side-effecting matching tools exist
- [ ] 4.4 Add parallel tests proving shared matching semantics and safe fallback

## 5. Compatibility, Audit, and Verification

- [ ] 5.1 Preserve historical exact-tool Plan execution through an explicitly audited compatibility mode
- [ ] 5.2 Record safe candidate requirements, matches, gaps, compatibility mode, and selected concrete tool without exposing inputs or credentials
- [ ] 5.3 Update README architecture documentation to distinguish Plan needs, semantic task capabilities, security authorities, and concrete ToolCalls
- [ ] 5.4 Run focused planning, router, AgentLoop, parallel, plugin, permission, API, and frontend tests and fix regressions
- [ ] 5.5 Run full backend/frontend validation and strict OpenSpec validation, then record verification notes
