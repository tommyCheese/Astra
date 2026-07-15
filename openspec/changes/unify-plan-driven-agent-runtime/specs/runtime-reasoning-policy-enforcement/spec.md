## MODIFIED Requirements

### Requirement: Planning strategies select distinct runtime paths
The system SHALL distinguish direct, adaptive, and plan-first strategies during planning while enforcing the same canonical Plan validation, ready-node scheduling, node lifecycle, replan budget, and completion boundaries for every strategy.

#### Scenario: Direct planning starts locally
- **WHEN** the effective planning strategy is direct
- **THEN** the Run uses a local canonical single-node Plan without a model planning call
- **THEN** the node is executed only after the PlanScheduler selects it

#### Scenario: Adaptive planning defers expansion to the Agent
- **WHEN** the effective planning strategy is adaptive
- **THEN** the Run starts from a lightweight canonical Plan and permits bounded PlanPatch expansion based on observations
- **THEN** the Agent cannot execute a non-ready node or bypass Plan validation

#### Scenario: Plan-first generates a full plan
- **WHEN** the effective planning strategy is plan-first
- **THEN** the runtime requests a model-generated contract and complete PlanDraft before executing the Agent Loop
- **THEN** the PlanDraft is validated and persisted before any external action

#### Scenario: Replan budget controls persisted revisions
- **WHEN** a strategy has exhausted its effective replan budget
- **THEN** the runtime rejects additional PlanPatch creation
- **THEN** it requests user input, uses an allowed existing ready node, or enters blocked according to policy

