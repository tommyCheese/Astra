## ADDED Requirements

### Requirement: Model thinking configuration is explicit
Every new Run SHALL persist the current model-thinking selection and capability version; the system SHALL NOT derive it from a legacy reasoning-effort-only payload.

#### Scenario: Missing current thinking selection
- **WHEN** persisted Run data lacks the current model-thinking selection
- **THEN** the data is rejected as obsolete instead of receiving a provider-specific legacy mapping

