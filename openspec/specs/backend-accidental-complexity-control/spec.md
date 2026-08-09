# backend-accidental-complexity-control Specification

## Purpose
TBD - created by archiving change remove-backend-accidental-complexity. Update Purpose after archive.
## Requirements
### Requirement: Deletion-first internal simplification
The backend SHALL remove redundant internal code before introducing replacement abstractions, and every retained class or module SHALL have a concrete framework, domain, state, policy, substitution, or aggregate ownership responsibility.

#### Scenario: Stateless one-use abstraction is reviewed
- **WHEN** an internal class has one operation, no meaningful state, and no alternate implementation or framework contract
- **THEN** the implementation SHALL replace it with a cohesive function or merge it into its actual owner unless documented evidence justifies the class

### Requirement: Canonical internal representation
The backend SHALL avoid parallel internal input, result, projection, and state containers that only copy an existing canonical representation.

#### Scenario: Data crosses an internal stage
- **WHEN** validated data moves between two internal stages within the same trust boundary
- **THEN** the stages SHALL reuse a canonical model or explicit typed parameters instead of adding a field-for-field transfer class

### Requirement: Aggregate-owned persistence
Persistence code SHALL be organized around coherent aggregates and transactions, without generic repository infrastructure or forwarding stores that add no independent policy.

#### Scenario: Repository fragment only forwards operations
- **WHEN** a repository fragment shares the same session, aggregate, transaction boundary, and callers as its parent repository
- **THEN** the fragment SHALL be merged into the aggregate owner or its operations SHALL be expressed as local functions

### Requirement: Governed compatibility lifetime
Compatibility branches, aliases, adapters, and fallback representations SHALL exist only when a supported caller or persisted state requires them, and otherwise SHALL be deleted.

#### Scenario: Compatibility path has no supported consumer
- **WHEN** usage analysis and regression tests show that no supported runtime, API, plugin, or persisted state consumes a compatibility path
- **THEN** the path and its dedicated tests and exports SHALL be removed without adding a compatibility re-export

### Requirement: Measurable net simplification
The refactor SHALL record production code metrics before and after implementation and SHALL reduce modules, classes, functions or methods, public symbols, and physical lines without reducing supported behavior.

#### Scenario: Refactor is accepted
- **WHEN** all deletion cohorts are complete
- **THEN** the architecture report SHALL show a net reduction from the 61,883-line, 307-module, 792-class, 2,492-function-or-method baseline and all required regression checks SHALL pass

### Requirement: External behavior preservation
The refactor SHALL preserve supported HTTP contracts, persistence semantics, permission decisions, audit records, recovery behavior, and agent results.

#### Scenario: Internal owner or representation changes
- **WHEN** an internal class, module, adapter, projection, or repository fragment is removed
- **THEN** focused and full regression tests SHALL demonstrate equivalent supported behavior at the external boundary

