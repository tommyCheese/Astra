## ADDED Requirements

### Requirement: History limit copy distinguishes display from retention
The system SHALL describe the sidebar history limit as the maximum number of conversations currently displayed and SHALL NOT imply that exceeding the limit deletes persisted conversations.

#### Scenario: Sidebar renders history limit
- **WHEN** the conversation sidebar is displayed
- **THEN** its copy identifies the configured client limit as a display limit
