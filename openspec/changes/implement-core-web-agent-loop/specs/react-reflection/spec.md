## ADDED Requirements

### Requirement: Model returns structured Agent decisions
The system SHALL require model-driven Agent loop steps to return structured AgentDecision output with decision type, reasoning summary, optional tool name, optional tool input, expected observation, and stop condition.

#### Scenario: Valid tool decision
- **WHEN** the model decides to call a tool
- **THEN** the decision includes `decision_type=call_tool`, a registered tool name, structured tool input, and a reasoning summary
- **THEN** the system validates the decision before executing the tool

#### Scenario: Invalid decision schema
- **WHEN** the model returns malformed or incomplete AgentDecision JSON
- **THEN** the system records the schema error
- **THEN** the loop enters reflection, retry, or blocked status according to retry limits

### Requirement: Reasoning summary is audit-safe
The system SHALL store only concise reasoning summaries intended for audit and UI display, and MUST NOT require storing full hidden chain-of-thought.

#### Scenario: Turn exposes concise reasoning
- **WHEN** a turn is shown in the UI
- **THEN** the UI displays the reasoning summary and action outcome
- **THEN** it does not display or depend on hidden chain-of-thought

### Requirement: Observations feed the next decision
The system SHALL convert each tool result, tool failure, validation error, or verification result into a structured Observation that is provided to the next Agent decision.

#### Scenario: Successful tool observation
- **WHEN** `web_search` returns candidate sources
- **THEN** the next decision receives an observation containing candidate count, warnings, and relevant metadata

#### Scenario: Failed tool observation
- **WHEN** a tool call fails
- **THEN** the next decision receives an observation containing error category, message, tool name, and retry history

### Requirement: Reflection handles recoverable failures
The system SHALL trigger structured reflection when a tool fails, evidence quality is low, a result conflicts with prior observations, or verification fails.

#### Scenario: Search returns no candidates
- **WHEN** `web_search` returns zero candidates
- **THEN** the Agent reflection proposes a revised query, a retry, or a blocked outcome
- **THEN** the decision is recorded before any retry

#### Scenario: Fetch content is low quality
- **WHEN** `web_fetch` returns a low quality score or warnings
- **THEN** the Agent reflection decides whether to fetch another candidate, retry with a different crawler plan, or continue with caveats

### Requirement: Reflection obeys retry limits
The system SHALL enforce maximum retry counts per tool, per query, and per run so that reflection cannot loop indefinitely.

#### Scenario: Retry limit exceeded
- **WHEN** the same tool or strategy fails beyond the configured retry limit
- **THEN** the Agent MUST stop retrying that strategy
- **THEN** the run records a blocked or warning result with the failed strategy details

### Requirement: Replanning is explicit
The system SHALL represent plan changes as explicit `replan` decisions and persist the new plan or revised step list.

#### Scenario: Agent revises plan after failed evidence
- **WHEN** verification determines that evidence is insufficient
- **THEN** the Agent may emit a `replan` decision
- **THEN** the revised plan is stored and visible in the run audit trail
