## ADDED Requirements

### Requirement: A minimal single Agent Loop owns all answer-mode iteration
The system SHALL execute standard and trusted Agent iterations through one fixed Agent Loop that loads canonical state, collects context, obtains one decision, routes control or action, records an observation, and returns a typed terminal or continuation outcome. The system MUST NOT maintain a separate Fast or Trusted controller loop.

#### Scenario: Standard Run executes an iteration
- **WHEN** a standard Run is ready for a model decision
- **THEN** the single Agent Loop executes the iteration with the standard capability composition
- **THEN** no trusted-only Plan, Reflection, Verification, or CompletionGate capability is invoked

#### Scenario: Trusted Run executes an iteration
- **WHEN** a trusted Run or ready trusted Plan node is ready for a model decision
- **THEN** the same Agent Loop executes the iteration with the trusted capability composition
- **THEN** trusted planning and completion behavior is supplied by capabilities rather than a second controller

### Requirement: Runtime behavior is composed through fixed typed capability slots
The system SHALL compose non-essential Agent behavior through a bounded set of typed capability slots with deterministic ordering and typed inputs and outputs. Capability implementations MUST NOT mutate canonical Loop state directly, subscribe to arbitrary event names, or reorder mandatory action-safety stages.

#### Scenario: Trusted composition adds planning
- **WHEN** the composition root builds a trusted Runtime composition
- **THEN** it installs the registered Planning, Reflection, Verification, and Completion capabilities in declared deterministic slots
- **THEN** `loop.py` does not import their concrete implementations

#### Scenario: A capability attempts an undeclared mutation
- **WHEN** a capability returns a state change outside its typed contribution contract
- **THEN** the Runtime rejects the contribution without modifying canonical state
- **THEN** the failure is classified and auditable

### Requirement: Runtime composition is trusted, complete, and frozen
The system SHALL build Runtime compositions only from platform-registered trusted implementations, SHALL validate mandatory ports and safety-slot coverage before execution, and SHALL freeze capability identity, version, configuration digest, and ordering for the Run. Runtime composition MUST NOT scan Task Workspaces or import untrusted code.

#### Scenario: Mandatory safety capability is absent
- **WHEN** a composition omits schema validation, effect analysis, authorization, approval integrity, persistence, cancellation, or result-unknown recovery
- **THEN** Run execution fails closed before the first model or tool action

#### Scenario: A waiting Run resumes after deployment configuration changes
- **WHEN** a waiting Run resumes and current optional capability configuration differs from its frozen composition
- **THEN** the system restores the frozen compatible composition or fails with a classified compatibility error
- **THEN** it does not silently use the new composition

### Requirement: Runtime packages have exclusive responsibility owners
The system SHALL assign Run lifecycle to `run_management`, Agent iteration and capability contracts to `agent_runtime`, trusted Plan/DAG behavior to `planning`, and provider/tool/storage construction to infrastructure. The production application MUST NOT retain a generic `runner` ownership layer or an independent `fast_agent_runtime` controller package after migration.

#### Scenario: Developer follows the primary execution path
- **WHEN** a developer traces a dispatched Run to one model/action iteration
- **THEN** the path proceeds from Run management to the Agent Loop and explicit adapters without crossing an alternative controller

#### Scenario: Trusted scheduling selects work
- **WHEN** trusted DAG scheduling selects a ready node
- **THEN** the Planning capability supplies the work item to the shared Agent Loop
- **THEN** Planning does not implement a separate action loop

### Requirement: Each runtime concept has one canonical representation
The system SHALL use one canonical Runtime representation for each decision, action, observation, outcome, checkpoint, and capability identity. An additional API schema, ORM record, domain object, projection, dataclass, or mapping may exist only when it enforces a distinct validation, persistence, behavior, authorization, redaction, aggregation, or versioning invariant.

#### Scenario: Intermediate model only copies fields
- **WHEN** an intermediate model and mapper copy a canonical value without adding a distinct invariant
- **THEN** the intermediate model and mapper are removed
- **THEN** the consumer uses the canonical value or the single real boundary conversion

#### Scenario: Public projection redacts internal data
- **WHEN** a public Run view requires authorization-aware field selection or secret redaction
- **THEN** a dedicated public projection may remain
- **THEN** its unique invariant and field allowlist are covered by tests

### Requirement: Refactoring slices preserve behavior while reducing structure
The system SHALL accept a refactoring slice only when architecture checks, code-size/duplication checks, and applicable functional tests all pass. A slice MUST NOT be considered complete when it only moves files, introduces a compatibility facade, or leaves the replaced controller, mirror model, or mapper active.

#### Scenario: A standard action path is migrated
- **WHEN** the standard model-to-tool-to-observation path is routed through the single Loop
- **THEN** paired approval, recovery, cancellation, event, and result tests preserve its observable behavior
- **THEN** the replaced Fast controller path and duplicate representations are deleted in the same completed slice

#### Scenario: Architecture improves but behavior regresses
- **WHEN** architecture metrics improve but an applicable public or persisted behavior test fails
- **THEN** the slice is rejected as incomplete

### Requirement: Peripheral control planes remain outside the core Loop
The system SHALL keep AutoDream, Evolution, Credential administration, retention, and other background or management use cases outside the Agent Loop. A later validated serving integration MAY contribute through a typed bounded capability, but MUST NOT add direct branches, repositories, or mutable control-plane access to the Loop.

#### Scenario: AutoDream is configured
- **WHEN** AutoDream administration or scheduling is enabled
- **THEN** it runs through its own application use case
- **THEN** the core Agent Loop does not import or route AutoDream lifecycle state

#### Scenario: Credential data is required by an action adapter
- **WHEN** an action needs an authorized credential reference
- **THEN** the mandatory action boundary resolves it through a narrow permission/credential port
- **THEN** Credential administration models are not added to canonical Loop state
