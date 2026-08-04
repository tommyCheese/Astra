## MODIFIED Requirements

### Requirement: Model thinking settings do not control public process events

The runtime SHALL produce Astra phase and concise reasoning-summary events according to the Agent execution protocol regardless of whether Provider model thinking is disabled, low, or high. When the immutable effective model-thinking selection is enabled, the runtime MAY additionally forward Provider-visible thinking text through dedicated `model_thinking.*` events, but MUST NOT copy that text into Astra reasoning-summary events or expose encrypted, redacted, inferred, or otherwise undisclosed chain-of-thought.

#### Scenario: Model thinking is disabled
- **WHEN** a Run executes with model thinking disabled
- **THEN** the runtime continues to emit applicable phase, public reasoning summary, tool, reflection, and verification events
- **THEN** the runtime emits no model-thinking text events

#### Scenario: Provider reports visible thinking text
- **WHEN** a Run has model thinking enabled and the Provider explicitly returns visible thinking text
- **THEN** the runtime emits dedicated ordered model-thinking events with the Provider, model operation, visibility level, and completeness state
- **THEN** Astra reasoning summaries remain concise and independently generated

#### Scenario: Provider reports only reasoning tokens or protected content
- **WHEN** a Provider reports reasoning-token usage, encrypted content, a signature, or redacted thinking without visible text
- **THEN** the runtime may record numeric usage and a stable unavailability reason
- **THEN** the runtime MUST NOT copy or infer hidden reasoning content into public process events

