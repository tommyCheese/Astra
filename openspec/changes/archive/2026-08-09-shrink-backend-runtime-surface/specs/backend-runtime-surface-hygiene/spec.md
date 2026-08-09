## ADDED Requirements

### Requirement: Retired provider code has no production runtime surface
The backend SHALL remove provider-specific production adapters after their provider is retired when no active runtime, registration boundary, or declared dynamic resource loads them.

#### Scenario: Scan production imports after Web retirement
- **WHEN** the backend production module graph is inspected after built-in Web tool retirement
- **THEN** no Python module remains under the retired Web tool implementation package

### Requirement: Generic grounding remains operational after adapter removal
The backend MUST retain generic evidence fragment validation, lineage, persistence, projection, and verification contracts independently of any retired provider-specific raw-output converter.

#### Scenario: Plugin emits canonical evidence
- **WHEN** an active plugin result processor emits a valid canonical evidence fragment
- **THEN** the host validates, persists, projects, and verifies it without importing a retired Web adapter

### Requirement: Backend cleanup preserves active runtime contracts
Production-surface cleanup SHALL NOT alter Fast or Trusted runtime dispatch, permission enforcement, tool result envelopes, Artifact access, or historical runtime compatibility unless those changes are explicitly specified separately.

#### Scenario: Run suites after cleanup
- **WHEN** the retired conversion modules and their private helpers are removed
- **THEN** architecture checks and the complete backend test suite pass without compatibility fallbacks or replacement stubs
