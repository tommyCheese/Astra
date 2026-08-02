## MODIFIED Requirements

### Requirement: Memory records are structured and scoped
The system SHALL store persistent Memory as immutable, versioned records with a stable Memory key, explicit namespace type and namespace ID, scope, supported kind, lifecycle status, content, structured data, provenance, confidence, importance, utility, creation and update time, valid time, optional expiration time, and optional supersession reference.

#### Scenario: Store workspace memory
- **WHEN** the Agent identifies a reusable workspace fact with sufficient provenance and the current Task has a non-empty workspace identity
- **THEN** the system stores it in that exact workspace namespace with `scope=workspace`, a supported kind, confidence, and provenance pointing to the source Run, Turn, ToolCall, Artifact, or evaluation

#### Scenario: Store user preference memory
- **WHEN** the user explicitly states a durable preference and the current Task has a non-empty user identity
- **THEN** the system stores it in that exact user namespace with `scope=user`
- **THEN** the memory includes provenance indicating the originating Task, Run, or message

#### Scenario: Missing persistent namespace identity
- **WHEN** a workspace or user Memory candidate has no corresponding non-empty workspace or user identity
- **THEN** the system does not place it in a shared namespace
- **THEN** it either keeps the candidate Run-scoped or rejects it with an audited reason

### Requirement: Memory recall is explicit and auditable
The system SHALL record candidate and selected Memory items, score components, exclusion reasons, query fingerprint, target Agent context, and later utility feedback, and SHALL expose selected Memory reads in the Run audit trail.

#### Scenario: Agent receives recalled memory
- **WHEN** the Agent loop assembles context for a decision
- **THEN** it retrieves Memory eligible for the current Run, Task, workspace, and user namespaces and matching lifecycle, expiration, kind, confidence, provenance, relevance, and token-budget constraints
- **THEN** the Turn records the selected Memory IDs, version IDs, and safe score summaries used in the decision context

#### Scenario: Shadow recall is enabled
- **WHEN** cross-Session retrieval operates in shadow mode
- **THEN** the system records the candidate and selected result set for evaluation
- **THEN** it does not inject those results into model context

### Requirement: Persistent memory requires provenance
The system SHALL NOT activate Task, workspace, or user Memory unless it has at least one valid source reference, an explicit namespace identity, and a confidence value.

#### Scenario: Missing provenance
- **WHEN** the Agent proposes a Task, workspace, or user Memory write without a valid source reference
- **THEN** the system rejects or quarantines the write
- **THEN** the rejection is recorded in the Turn, Run events, or Memory lifecycle audit

#### Scenario: Source is deleted
- **WHEN** all valid source references for an active Memory are deleted or become inaccessible
- **THEN** the system revokes that Memory before it can be recalled again
- **THEN** the lifecycle audit identifies source deletion as the reason

### Requirement: Memory writes are visible in the UI
The system SHALL expose proposed, active, superseded, revoked, expired, and quarantined Memory versions in authorized audit views so users can inspect and correct what the Agent learned.

#### Scenario: Memory write shown in chat audit
- **WHEN** the Agent proposes or activates a Memory item during a Run
- **THEN** the chat UI shows a compact Memory event
- **THEN** the detailed audit view shows namespace, scope, kind, lifecycle, version, content, confidence, validity, provenance, and supersession state

#### Scenario: User revokes memory
- **WHEN** an authorized user revokes an active Memory from the audit view or API
- **THEN** subsequent retrieval excludes it immediately
- **THEN** historical Runs continue to show that the older version had previously been recalled

## ADDED Requirements

### Requirement: Memory kinds are typed and normalized
The system SHALL normalize new cross-Session Memory into `semantic_fact`, `user_preference`, `episodic_experience`, `procedure`, `failure_pattern`, or `evaluation_feedback`, and SHALL prevent unsupported legacy kinds from being promoted across Sessions without normalization.

#### Scenario: Extract reusable failure knowledge
- **WHEN** a completed Run has a verified failure, diagnosed cause, and successful mitigation
- **THEN** the extractor may create a `failure_pattern` candidate linking the conditions, symptoms, mitigation, and source evidence

#### Scenario: Encounter unknown legacy kind
- **WHEN** retrieval encounters a legacy Memory kind outside the supported set
- **THEN** it may remain available within its original Run
- **THEN** it is not selected for cross-Session recall until normalized

### Requirement: Memory lifecycle transitions are constrained
The system SHALL validate lifecycle transitions and SHALL exclude candidate, superseded, revoked, expired, and quarantined Memory from normal Agent recall.

#### Scenario: Activate validated candidate
- **WHEN** a candidate has a valid namespace, supported kind, provenance, confidence, and safe bounded content
- **THEN** the system may transition it to active with an audited transition

#### Scenario: Reject terminal-state reactivation
- **WHEN** a request attempts to change a revoked, expired, or superseded record directly back to active
- **THEN** the system rejects the transition
- **THEN** restoration requires a new version or an audited generation rollback

### Requirement: Temporal updates preserve history
The system SHALL represent when a Memory was observed and valid, and SHALL replace changing knowledge by creating a new version that supersedes the prior active version instead of editing prior content in place.

#### Scenario: User preference changes
- **WHEN** a user gives a new preference that conflicts with an active preference for the same stable key
- **THEN** the system creates a new version with the new valid time
- **THEN** it marks the prior version superseded while preserving its content and provenance for audit

#### Scenario: Query historical validity
- **WHEN** an authorized audit requests Memory state at an earlier time
- **THEN** the system can identify which version was active and valid at that time

### Requirement: Cross-Session recall is namespace isolated
The system SHALL retrieve cross-Session Memory only from explicit namespaces derived from the current Run and SHALL NOT interpret missing owner identities as a shared namespace.

#### Scenario: Recall workspace experience in a later Task
- **WHEN** a new Task in workspace `W` asks a question relevant to an active Memory in workspace `W`
- **THEN** the Memory is eligible for scoring even though it originated in another Task or Run

#### Scenario: Different workspace has similar query
- **WHEN** a Task in workspace `B` is semantically or lexically similar to Memory owned by workspace `A`
- **THEN** workspace `A` Memory is filtered before relevance scoring

### Requirement: Recall is bounded and reproducible
The system SHALL score eligible Memory with deterministic lexical, structural, temporal, confidence, importance, and bounded utility signals, SHALL support an optional semantic signal, and SHALL select a stable result set within item and token budgets.

#### Scenario: Repeated identical retrieval
- **WHEN** the same query, namespace set, Memory snapshot, and retrieval policy are evaluated twice
- **THEN** the selected IDs, order, and score components are identical

#### Scenario: Candidate set exceeds budget
- **WHEN** relevant eligible Memory exceeds the configured item or token budget
- **THEN** the system selects the highest-ranked complete items that fit
- **THEN** it records budget exclusion for remaining candidates

### Requirement: Memory outcome feedback is bounded
The system SHALL accept audited helpful, harmful, contradicted, used, or ignored feedback for a recall event and SHALL aggregate utility within configured bounds without overriding namespace, lifecycle, expiration, or provenance eligibility.

#### Scenario: Recalled procedure causes verified regression
- **WHEN** verification links a failed outcome to a recalled procedure
- **THEN** the system records harmful feedback and lowers its bounded utility
- **THEN** the Memory remains subject to normal review, quarantine, or revocation rather than being silently deleted

### Requirement: Expiration and deletion immediately affect eligibility
The system SHALL evaluate expiration and source accessibility at query time and SHALL propagate explicit revocation or conversation deletion to every derived search projection.

#### Scenario: Worker has not materialized expiration
- **WHEN** an active record's expiration time is in the past but the expiration worker has not run
- **THEN** retrieval still excludes the record

#### Scenario: Consolidated memory has multiple sources
- **WHEN** one source conversation is deleted and another valid source still supports the Memory
- **THEN** the system removes the deleted source, revalidates the Memory, and preserves it only if remaining evidence satisfies activation rules

