## ADDED Requirements

### Requirement: Navigation-layer deletion priority
The backend SHALL treat single-consumer forwarding modules, one-owner companion state containers, and field-for-field query wrappers as accidental complexity when they have no independent policy, lifecycle, transaction, framework, or substitution responsibility. Such code SHALL be merged into its canonical owner before a new facade or package export is considered.

#### Scenario: Companion module always changes with its owner
- **WHEN** usage analysis shows that a companion module is constructed or called only by one owner and represents the same protocol or aggregate
- **THEN** the companion implementation is colocated with that owner and its old module path is deleted

#### Scenario: Strong typing is still required
- **WHEN** a redundant transfer object or module boundary is removed
- **THEN** the remaining owner retains named types for stable structures and does not replace them with an unvalidated dictionary

### Requirement: Navigation simplification preserves mandatory boundaries
Navigation reduction MUST preserve the canonical Loop ports, transaction ownership, effect analysis, authorization, approval integrity, persisted audit, cancellation, result-unknown recovery, and plugin isolation. Modules implementing those responsibilities SHALL NOT be merged merely to reduce counts.

#### Scenario: A boundary adds a reading hop
- **WHEN** a module owns a mandatory safety invariant or replaceable external adapter
- **THEN** it remains an explicit boundary and the architecture report identifies why the hop is necessary
