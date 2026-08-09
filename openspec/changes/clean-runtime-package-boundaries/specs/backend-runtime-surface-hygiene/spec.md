## ADDED Requirements

### Requirement: Test-only production surfaces are removed
Production modules SHALL NOT retain functions, classes, rollout constants, compatibility constructors, or normalization pipelines whose only consumers are tests and which are not required by a documented plugin, persistence, or public boundary.

#### Scenario: Usage analysis finds only dedicated tests
- **WHEN** a production symbol has no supported runtime, registration, dynamic-resource, persisted-state, or external contract consumer
- **THEN** the symbol, its exports, and tests dedicated solely to that private surface are removed without a replacement stub

### Requirement: Future capabilities remain outside the core Runtime
Speculative benchmark, rollout, exchange, memory-generation, and governance behavior SHALL remain outside the core Runtime until a separately specified capability integrates it through a typed slot or plugin boundary.

#### Scenario: Unintegrated capability is encountered during cleanup
- **WHEN** an implementation represents planned behavior but is not invoked by the canonical execution path
- **THEN** it is removed or isolated outside Runtime rather than retained as an apparent core capability
