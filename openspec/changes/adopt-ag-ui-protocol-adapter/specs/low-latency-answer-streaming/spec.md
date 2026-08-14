## ADDED Requirements

### Requirement: AG-UI preserves progressive first-content rendering
The AG-UI path SHALL begin streaming after request validation and Run correlation, SHALL NOT wait for a terminal RunView or noncritical structured Activity projection before emitting displayable answer content, and SHALL preserve the existing Astra first-content processing budget.

#### Scenario: First answer delta is available
- **WHEN** Astra commits the first safe answer delta for an AG-UI Run
- **THEN** the adapter emits the corresponding text content without waiting for verification, plan completion, or a complete Run snapshot
- **THEN** the React client makes that content visible immediately

### Requirement: AG-UI deltas use bounded rendering work
The system SHALL bound server-side text and reasoning aggregation, and the React projection store SHALL commit high-frequency text, reasoning, and compatible Activity updates at most once per browser animation frame after the first displayable update.

#### Scenario: Multiple content events arrive in one frame
- **WHEN** several compatible text or process deltas arrive before the next animation frame
- **THEN** the client preserves their protocol order and makes at most one batched visible state commit for that frame

#### Scenario: Terminal or interrupt event arrives
- **WHEN** a completion, cancellation, error, interrupt, or artifact-available event arrives among buffered progress deltas
- **THEN** the client flushes relevant buffered content and exposes the critical state without waiting for an ordinary progress batch

### Requirement: AG-UI reconnect converges through snapshots
The AG-UI client SHALL recover from a broken stream through authoritative message, State, and Activity snapshots when it cannot prove a compatible delta baseline, and MUST NOT remain permanently streaming after the underlying Run becomes terminal.

#### Scenario: Stream disconnects after partial text
- **WHEN** the client loses the AG-UI connection after displaying part of an answer
- **THEN** it preserves the visible partial text while reconnecting or loading recovery state
- **THEN** authoritative snapshots eventually reconcile the message and terminal Run state without duplicating content

