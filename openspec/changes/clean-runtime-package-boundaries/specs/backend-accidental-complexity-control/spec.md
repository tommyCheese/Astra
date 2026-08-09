## ADDED Requirements

### Requirement: Exact duplicate behavior has one semantic owner
Exact duplicate runtime identity, outcome, observation, lifecycle response, model finalization, and repository normalization behavior SHALL have one implementation at its closest semantic owner.

#### Scenario: Two implementations are behaviorally identical
- **WHEN** duplicate implementations have the same contract and no independent policy
- **THEN** consumers use one owner and the duplicate methods or helpers are removed

### Requirement: Transitional runtime pipelines expire
Builder, Composer, Assembly, and Stage abstractions introduced during runtime convergence SHALL be removed when their behavior can be expressed directly by the canonical typed capability slots.

#### Scenario: Capability slot owns an operation
- **WHEN** context, decision, action, observation, progress, or completion behavior is already owned by a canonical slot
- **THEN** a parallel Stage or assembly transfer object for the same operation is removed

### Requirement: Cleanup produces measurable reduction
The completed cleanup SHALL reduce production lines, public symbols, and redundant abstractions relative to the change baseline without reducing supported behavior.

#### Scenario: Cleanup is accepted
- **WHEN** canonical imports and typed boundaries are in place
- **THEN** recorded metrics show a net reduction and architecture plus complete backend regression suites pass
