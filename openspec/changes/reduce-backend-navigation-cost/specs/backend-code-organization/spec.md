## ADDED Requirements

### Requirement: Direct capability navigation
Each high-frequency backend use case SHALL expose one canonically named implementation entry, and its normal reading path SHALL NOT cross a module that only re-exports, forwards, or field-copies the next stage. Package initializers MUST NOT hide the canonical owner behind compatibility exports.

#### Scenario: Maintainer follows a Runtime profile
- **WHEN** a maintainer starts from Standard or Trusted Run execution
- **THEN** every subsequent module on the documented path owns a checkpoint protocol, composition decision, policy, side effect, or replaceable boundary

#### Scenario: Thin navigation module has no independent responsibility
- **WHEN** a module only calls another module with the same inputs and has no policy, lifecycle, state, transaction, or substitution role
- **THEN** callers import the actual owner and the thin module is removed without a compatibility re-export

### Requirement: Domain-action naming for navigation surfaces
Public application and runtime navigation modules SHALL be named for the domain action, protocol, or owned aggregate they implement; broad names such as `state`, `service`, `contracts`, or `dependencies` SHALL be qualified or colocated when they do not uniquely identify that responsibility in their package context.

#### Scenario: Runtime persistence module is renamed
- **WHEN** a module owns checkpoint encoding, pending-action persistence, and interrupted-action recovery
- **THEN** its canonical name identifies the checkpoint protocol rather than only calling it generic state

### Requirement: Measured use-case navigation cost
The backend architecture report SHALL record the module sequence for representative Standard execution, Trusted execution, and Run read-model use cases, and a navigation refactor SHALL reduce at least one non-policy hop in each affected sequence without increasing function or module hard-limit violations.

#### Scenario: Navigation change is accepted
- **WHEN** the refactor completes
- **THEN** before/after module sequences and structural metrics demonstrate fewer forwarding hops and the architecture verification suite passes
