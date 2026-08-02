## MODIFIED Requirements

### Requirement: Planning strategies select distinct runtime paths
The system SHALL distinguish adaptive and plan-first strategies for new Runs while enforcing the same canonical Plan validation, ready-node scheduling, node lifecycle, replan budget, and completion boundaries. Direct SHALL remain a legacy persisted value only and MUST NOT be accepted for a new requested policy.

#### Scenario: Legacy direct snapshots remain readable
- **WHEN** an existing Run contains a persisted effective direct strategy
- **THEN** the compatibility runtime can parse and display the immutable snapshot
- **THEN** preferences and new Run requests normalize or reject direct instead of creating another direct Run

#### Scenario: Adaptive planning defers expansion to the Agent
- **WHEN** the effective planning strategy is adaptive
- **THEN** the Run starts from a lightweight canonical Plan and permits bounded PlanPatch expansion based on observations
- **THEN** the Agent cannot execute a non-ready node or bypass Plan validation
- **THEN** an accepted PlanPatch creates a new active Plan version while preserving completed-node lineage and evidence

#### Scenario: Plan-first generates a full plan
- **WHEN** the effective planning strategy is plan-first
- **THEN** the runtime requests a model-generated contract and complete PlanDraft before executing the Agent Loop
- **THEN** the PlanDraft is validated and persisted before any external action

#### Scenario: Replan budget controls persisted revisions
- **WHEN** a strategy has exhausted its effective replan budget
- **THEN** the runtime rejects additional PlanPatch creation
- **THEN** it requests user input, uses an allowed existing ready node, or enters blocked according to policy
