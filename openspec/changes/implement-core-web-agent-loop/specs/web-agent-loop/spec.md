## ADDED Requirements

### Requirement: Run uses Web-only Agent loop
The system SHALL execute new Web Agent runs through a bounded Agent loop that can plan, call allowed Web tools, observe results, reflect on failures, verify evidence, and finalize a response.

#### Scenario: Successful bounded Web Agent run
- **WHEN** a user submits a Web Agent goal
- **THEN** the system creates a run and executes loop turns until it reaches `finalize`, `blocked`, or the configured maximum turn count
- **THEN** the run records plan, tool calls, observations, verification, and final response

#### Scenario: Maximum turns reached
- **WHEN** the Agent loop reaches the configured maximum turn count without a final answer
- **THEN** the run status becomes `completed_with_warnings` or `blocked`
- **THEN** the result includes a verification note explaining that the loop stopped at the turn limit

### Requirement: Agent loop gates tools through registry
The system SHALL only execute tools through the ToolRegistry and a Web Agent allowlist containing `web_search` and `web_fetch`.

#### Scenario: Allowed Web tool call
- **WHEN** an Agent decision requests `web_search` or `web_fetch`
- **THEN** the system validates the tool against the registry and allowlist before execution
- **THEN** the ToolCall is persisted with input, output, status, permission, and side effect level

#### Scenario: Disallowed tool request
- **WHEN** an Agent decision requests an unregistered tool or a tool outside the Web Agent allowlist
- **THEN** the system MUST NOT execute the tool
- **THEN** the turn records a rejected observation and triggers reflection or blocked status

### Requirement: Agent turns are auditable
The system SHALL persist each Agent loop turn with a turn index, decision type, reasoning summary, selected tool, observation, reflection, status, and related ToolCall or Artifact identifiers.

#### Scenario: Tool turn is persisted
- **WHEN** the Agent calls a tool during a turn
- **THEN** the system stores the turn decision and links it to the resulting ToolCall
- **THEN** the run view exposes the turn for UI and audit display

#### Scenario: Finalization turn is persisted
- **WHEN** the Agent decides to finalize
- **THEN** the system stores a finalization turn containing the response summary, cited sources, caveats, and verification status

### Requirement: Evidence-based finalization
The system SHALL only finalize Web Agent answers from audited observations, ToolCalls, Artifacts, Memory records, and verification results.

#### Scenario: Final answer cites evidence
- **WHEN** the Agent finalizes a Web research answer
- **THEN** every key finding includes source URLs or references to audited memory provenance
- **THEN** the final result includes caveats when evidence is incomplete

#### Scenario: No usable evidence
- **WHEN** all tool calls fail or return insufficient evidence
- **THEN** the Agent MUST NOT fabricate an answer
- **THEN** the run returns a blocked or warning result that explains the evidence gap

### Requirement: Existing Web query behavior remains available through loop
The system SHALL preserve the current Web search, fetch, evidence pack, and verification behavior while executing it through Agent loop turns.

#### Scenario: Mock Web summary still completes
- **WHEN** the mock provider is configured
- **THEN** a deterministic Web Agent run completes without external network access
- **THEN** the run includes search, fetch, evidence pack, verification, and final response records
