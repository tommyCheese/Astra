## ADDED Requirements

### Requirement: Trusted Web summaries expose claim-level grounding
A trusted summary based on Web evidence SHALL expose material claims with canonical evidence references and citations in addition to the existing summary, findings, and source list.

#### Scenario: Grounded summary succeeds
- **WHEN** a trusted summary contains material factual claims from fetched sources
- **THEN** each supported claim references at least one eligible passage from the current Run

### Requirement: Legacy summary consumers remain compatible
The system SHALL continue populating the existing summary, findings, sources, caveats, and audit fields while claims and citations are introduced additively.

#### Scenario: Historical result lacks grounding fields
- **WHEN** a historical RunResult without claims or citations is loaded
- **THEN** it remains readable and the missing grounding collections normalize to empty values
