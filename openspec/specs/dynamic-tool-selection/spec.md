# dynamic-tool-selection Specification

## Purpose
TBD - created by archiving change decouple-planning-from-tools. Update Purpose after archive.
## Requirements
### Requirement: Tool manifests declare semantic task capabilities
The system SHALL allow a tool manifest to declare provider-neutral task capabilities separately from security capabilities, permissions, risk, and execution backend.

#### Scenario: Equivalent providers are registered
- **WHEN** two concrete tools from different providers both declare `information.search`
- **THEN** both tools can satisfy the same Plan capability requirement without the Plan naming either provider or tool

### Requirement: Runtime resolves candidates from active needs
The system SHALL derive an ordered candidate set at execution time from the active Plan node's semantic needs, frozen eligible catalog, Run policy, backend availability, and execution-safety constraints.

#### Scenario: Active node requires external search
- **WHEN** a node requires `information.search` and two currently eligible tools declare that capability
- **THEN** both manifests enter the execution decision context in deterministic order
- **THEN** ineligible tools do not enter that context

#### Scenario: Node declares no tool need
- **WHEN** a node has no semantic capability requirement
- **THEN** the runtime may expose every tool that remains eligible under catalog and policy controls
- **THEN** the Plan remains valid even if the node completes by reasoning over existing evidence without a ToolCall

### Requirement: Concrete selection occurs during execution
The system SHALL select and persist a concrete tool identity only as part of an execution decision and SHALL validate that identity against the current candidate resolution before effect analysis or execution.

#### Scenario: Model selects an eligible candidate
- **WHEN** the model selects a concrete candidate and supplies input matching its manifest
- **THEN** the runtime proceeds through the normal ToolRouter, effect, permission, approval, execution, and result pipeline
- **THEN** the resulting ToolCall records the selected concrete identity

#### Scenario: Model selects a tool outside the candidates
- **WHEN** the model selects a registered or invented tool that is not in the active candidate resolution
- **THEN** the runtime rejects the decision without executing the tool
- **THEN** an auditable observation identifies the semantic requirements and safe candidate identities so a later bounded turn may choose an alternative

### Requirement: Multi-capability progress is cumulative
The runtime SHALL derive satisfied and unresolved semantic requirements from successful ToolCalls associated with the active node, and a required capability SHALL remain unresolved until at least one successful selected tool declares it.

#### Scenario: Node requires discovery and reading
- **WHEN** a successful search candidate satisfies `information.search` but the node also requires `information.read`
- **THEN** the next execution decision resolves candidates for `information.read`
- **THEN** the runtime rejects node completion until both declared requirements are satisfied

#### Scenario: Equivalent tool replaces a failed candidate
- **WHEN** one selected tool fails before satisfying its capability and another eligible tool declares the same capability
- **THEN** that capability remains unresolved
- **THEN** a later bounded turn may select the alternative without revising the Plan

### Requirement: Candidate resolution is shared across execution modes
Serial AgentLoop execution and parallel Node Worker execution SHALL use the same semantic matching contract.

#### Scenario: Read-only node executes in parallel
- **WHEN** a node's requirement has an eligible read-only idempotent candidate
- **THEN** the parallel Worker sees the same semantic match as serial execution, constrained to its safe execution class

#### Scenario: Only side-effecting candidates exist
- **WHEN** parallel execution evaluates a node whose matching candidates require side effects
- **THEN** the Worker does not execute those candidates and preserves the deterministic serial fallback

### Requirement: Selection is auditable and does not authorize
The system SHALL expose requirements, candidates, matched capabilities, unresolved capabilities, compatibility mode, and the selected concrete tool in safe audit data, and candidate membership MUST NOT be treated as permission to execute.

#### Scenario: Candidate requires approval
- **WHEN** an eligible candidate is selected but its effect plan requires user approval
- **THEN** the existing approval flow pauses execution
- **THEN** the selection audit does not bypass or weaken that approval

### Requirement: Historical exact-name Plans remain executable
The runtime SHALL recognize an exact frozen tool identity in a historical Plan through an explicit compatibility path, while new and revised Plans MUST NOT persist concrete tool identities as semantic requirements.

#### Scenario: Historical Run resumes
- **WHEN** a persisted historical node requires `web_search` and that frozen tool remains eligible
- **THEN** runtime resolution can match that tool and marks the resolution as legacy tool binding

#### Scenario: New Plan names a registered tool
- **WHEN** a newly generated, patched, or revised Plan uses a registered tool identity as a required capability
- **THEN** Plan validation rejects it before activation

