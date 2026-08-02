## MODIFIED Requirements

### Requirement: Memory writes are visible in the UI
The system SHALL expose proposed, candidate, human-activated, rejected, and committed Memory writes in the run view and Memory management UI so users can inspect what the Agent proposed and what a human allowed into production recall.

#### Scenario: Memory candidate shown in chat audit
- **WHEN** the Agent commits a candidate Memory item during a run
- **THEN** the chat UI shows a compact candidate Memory event
- **THEN** the detailed audit view shows scope, kind, content, confidence, provenance, and `candidate` status

#### Scenario: Human decision shown in Memory management
- **WHEN** a human activates or rejects a Memory candidate
- **THEN** the Memory detail shows the resulting lifecycle state, actor, reason, and state version

## ADDED Requirements

### Requirement: Only human-activated ordinary Memory participates in persistent recall
The system SHALL restrict ordinary persistent Memory recall to `active` records that have passed the human activation transition, while preserving separately governed AutoDream publication behavior.

#### Scenario: Unreviewed persistent candidate matches a request
- **WHEN** a task, session, or user candidate matches the current request but has not been human-activated
- **THEN** the system does not inject it into the request context

#### Scenario: Human-activated persistent record matches a request
- **WHEN** an active record matches namespace, lifecycle, confidence, relevance, validity, source-access, and budget constraints
- **THEN** the system may inject it as low-authority Memory context
