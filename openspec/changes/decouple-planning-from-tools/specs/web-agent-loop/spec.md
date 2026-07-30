## MODIFIED Requirements

### Requirement: Run uses bounded general Agent loop
The system SHALL execute Runs through a bounded Agent loop that can follow a logical Plan, dynamically select eligible tools for active semantic needs, observe results, reflect on failures, verify outputs, and finalize a response without making the loop Web-only.

#### Scenario: Successful bounded Web-backed run
- **WHEN** a user submits a goal that requires current public information
- **THEN** the system dynamically selects eligible discovery and reading tools until it reaches `finalize`, `blocked`, or the configured maximum turn count
- **THEN** the run records Plan, candidate resolution, ToolCalls, observations, verification, and final response

#### Scenario: Successful run without Web
- **WHEN** a user goal can be fulfilled from reasoning, workspace tools, artifacts, or another registered capability
- **THEN** the Agent loop does not require `web_search`, `web_fetch`, or Web evidence

#### Scenario: Maximum turns reached
- **WHEN** the Agent loop reaches the configured maximum turn count without a final answer
- **THEN** the run status becomes `completed_with_warnings` or `blocked`
- **THEN** the result includes a verification note explaining that the loop stopped at the turn limit

### Requirement: Agent loop gates dynamically selected tools through registry
The system SHALL expose concrete tools only after semantic candidate resolution and SHALL execute a selected candidate only through ToolRegistry/ToolRouter and the existing policy, permission, effect, approval, backend, and budget gates.

#### Scenario: Eligible tool call
- **WHEN** an execution decision requests a concrete tool from the active candidate resolution
- **THEN** the system validates the tool against the frozen registry and runtime gates before execution
- **THEN** the ToolCall is persisted with input, output, status, permission, and side effect level

#### Scenario: Out-of-candidate tool request
- **WHEN** an execution decision requests an unregistered tool or a tool outside the active semantic candidates
- **THEN** the system MUST NOT execute the tool
- **THEN** the turn records a rejected observation and permits bounded alternative selection, reflection, replan, or blocked status
