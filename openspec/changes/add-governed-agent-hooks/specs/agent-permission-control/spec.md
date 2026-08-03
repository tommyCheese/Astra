## ADDED Requirements

### Requirement: Hook side effects use independent attenuated principals
Every executable Hook handler SHALL run as an independent Hook principal whose effective permissions are the intersection of its source policy, manifest effect ceiling, event capabilities, Run or Task scope, data labels, runtime profile, network policy, and credential references; it MUST NOT borrow the triggering Agent's or tool's Grants.

#### Scenario: Notification Hook sends data externally
- **WHEN** a Hook attempts HTTP egress containing event data
- **THEN** the destination, payload labels, credential reference, network scope, and Hook principal are evaluated through the unified Permission Engine before transmission

#### Scenario: Hook requires broader authority
- **WHEN** a Hook-derived action exceeds its effective ceiling or requires a new approval
- **THEN** it is denied or escalated to an authorized user and the Hook cannot approve the request itself

### Requirement: Hook decisions can only preserve or restrict authorization
Hook admission results SHALL be normalized as additional constraints on the canonical PermissionRequest and MUST NOT turn a platform, managed, execution-mode, Grant, budget, Sandbox, protected-resource, credential, or data-flow `ask` or `deny` into `allow`.

#### Scenario: Hook auto-approves a dangerous tool
- **WHEN** a Hook returns allow for a tool invocation whose canonical authorization result is ask or deny
- **THEN** the canonical ask or deny remains effective

#### Scenario: Hook requests additional review
- **WHEN** a Hook returns ask for an otherwise allowed action
- **THEN** the action enters the existing approval or unattended fail-closed flow with Hook provenance

### Requirement: Hook control-plane resources are protected
Hook definitions, trust decisions, handler content, Catalog snapshots, failure policies, execution records, outbox, dead letters, replay controls, runtime profiles, and Hook Grants SHALL be protected control-plane resources that ordinary Run, Task, Agent, tool, or Hook permissions cannot mutate.

#### Scenario: Agent tries to disable a blocking Hook
- **WHEN** an Agent or ordinary tool attempts to disable, bypass, edit, or delete an effective Hook or its audit record
- **THEN** the action is denied regardless of execution mode or a lower-trust Grant

