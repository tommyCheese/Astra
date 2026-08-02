# memory-management Specification

## Purpose
TBD - created by archiving change implement-core-web-agent-loop. Update Purpose after archive.
## Requirements
### Requirement: Memory records are structured and scoped
The system SHALL store Memory records with `run`, `task`, `session`, or `user` scope, kind, content, structured data, provenance, confidence, creation time, update time, and optional expiration time; no workspace Memory fields or aliases SHALL exist.

#### Scenario: Store session memory
- **WHEN** the Agent identifies a reusable fact for the current browser session with sufficient provenance
- **THEN** the system stores it with `scope=session` and the Run's session identity

#### Scenario: Store user preference memory
- **WHEN** the user explicitly states a durable preference and a stable user identity exists
- **THEN** the system stores it with `scope=user`
- **THEN** the memory includes provenance indicating the originating Run

### Requirement: Run memory is available during the loop
The system SHALL maintain run memory for current-goal facts, observations, failures, source summaries, and intermediate conclusions.

#### Scenario: Observation becomes run memory
- **WHEN** a tool returns a useful observation
- **THEN** the Agent may store a summarized run memory item linked to the turn and ToolCall
- **THEN** later turns can retrieve that item without re-reading the full tool output

### Requirement: Memory recall is explicit and auditable
The system SHALL record which Memory items are recalled into an Agent context and expose selected and excluded reads in the Run audit trail without a shadow-mode field.

#### Scenario: Agent receives recalled memory
- **WHEN** the Agent loop assembles context for a decision with persistent recall enabled
- **THEN** it retrieves Memory items matching current run, task, session, or user namespaces and current eligibility rules
- **THEN** the recall event records selected and excluded Memory IDs and scores

### Requirement: Persistent memory requires provenance
The system SHALL NOT write task, session, or user Memory unless the Memory has valid provenance and a confidence value.

#### Scenario: Missing provenance
- **WHEN** the Agent proposes a task, session, or user Memory write without provenance
- **THEN** the system rejects the write
- **THEN** the rejection is recorded in the Run events

### Requirement: Memory writes are visible in the UI
The system SHALL expose proposed, candidate, human-activated, rejected, and committed Memory writes in the run view and Memory management UI so users can inspect what the Agent proposed and what a human allowed into production recall.

#### Scenario: Memory candidate shown in chat audit
- **WHEN** the Agent commits a candidate Memory item during a run
- **THEN** the chat UI shows a compact candidate Memory event
- **THEN** the detailed audit view shows scope, kind, content, confidence, provenance, and `candidate` status

#### Scenario: Human decision shown in Memory management
- **WHEN** a human activates or rejects a Memory candidate
- **THEN** the Memory detail shows the resulting lifecycle state, actor, reason, and state version

### Requirement: Memory does not replace evidence
The system SHALL treat memory as context, not as an unchecked factual source for final answers unless it includes auditable provenance.

#### Scenario: Final answer uses memory
- **WHEN** a final answer relies on recalled memory
- **THEN** the answer cites the memory provenance or includes a caveat that the memory is contextual

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

### Requirement: Local users can manage enforced Memory runtime settings
The system SHALL expose a validated local Runtime API for Memory write enablement, cross-Session recall mode, recall item and token budgets, minimum confidence and relevance score, AutoDream enablement, scan interval, and minimum candidate count. The system SHALL persist valid updates atomically and apply them to subsequent runtime work.

#### Scenario: Enable shadow cross-Session recall
- **WHEN** a local user saves cross-Session mode `shadow`
- **THEN** subsequent recall records candidate and selection decisions without injecting selected Memory into model context
- **THEN** the persisted setting survives application restart

#### Scenario: Reject invalid Memory settings
- **WHEN** a user submits a value outside the configured safe bounds or an unsupported recall mode
- **THEN** the system returns a typed validation error
- **THEN** neither persisted nor in-memory Memory settings change

### Requirement: AutoDream lifecycle follows runtime configuration
The system SHALL start the AutoDream scanner when a valid runtime update enables it and SHALL stop the scanner when a runtime update disables it without deleting persisted consolidation jobs or source evidence.

#### Scenario: Enable AutoDream after application startup
- **WHEN** AutoDream is disabled at startup and a local user enables it
- **THEN** the background scanner starts without restarting the application

#### Scenario: Disable a running AutoDream scanner
- **WHEN** a local user disables AutoDream
- **THEN** the scanner stops scheduling new work
- **THEN** persisted jobs and audit records remain available

### Requirement: Only human-activated ordinary Memory participates in persistent recall
The system SHALL restrict ordinary persistent Memory recall to `active` records that have passed the human activation transition, while preserving separately governed AutoDream publication behavior.

#### Scenario: Unreviewed persistent candidate matches a request
- **WHEN** a task, session, or user candidate matches the current request but has not been human-activated
- **THEN** the system does not inject it into the request context

#### Scenario: Human-activated persistent record matches a request
- **WHEN** an active record matches namespace, lifecycle, confidence, relevance, validity, source-access, and budget constraints
- **THEN** the system may inject it as low-authority Memory context

