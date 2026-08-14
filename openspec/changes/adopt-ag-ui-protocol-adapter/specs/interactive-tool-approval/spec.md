## ADDED Requirements

### Requirement: Persistent approvals are exposed as AG-UI tool interrupts
The system SHALL project a pending governed tool approval as an AG-UI tool-call lifecycle followed by a `RUN_FINISHED` interrupt, and the interrupt SHALL expose only the frozen safe action preview and backend-supported decisions.

#### Scenario: Exact approval is required
- **WHEN** a frozen ToolCall reaches the persistent request-approval gate without a matching grant
- **THEN** the AG-UI stream identifies the tool call and ends with a correlated `tool_call` interrupt
- **THEN** the tool is not invoked before a valid interrupt response reaches the existing Astra approval service

#### Scenario: Similar approval is unavailable
- **WHEN** the backend did not generate a safe similar-action matcher
- **THEN** the interrupt response schema does not advertise an allow-similar decision

### Requirement: AG-UI approval resume is restart-safe and idempotent
The system MUST durably correlate an AG-UI interrupt with the pending Astra approval and internal Run, and MUST reject stale, mismatched, unauthorized, expired, or repeated resolutions without repeating tool execution.

#### Scenario: Service restarts while waiting
- **WHEN** the service restarts after emitting an approval interrupt but before receiving the user's decision
- **THEN** an authorized client can recover the pending safe approval and resolve it through a new protocol Run

#### Scenario: Resolution is replayed
- **WHEN** a client replays the same resolved interrupt after the frozen action was already accepted or rejected
- **THEN** the backend returns the established outcome or a safe stale response and does not execute the ToolCall again

