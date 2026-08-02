## ADDED Requirements

### Requirement: Web search accepts bounded logical queries
The system SHALL accept either one legacy Web search query or a bounded batch of logical queries, SHALL assign each logical query an independent trace identity, and SHALL preserve candidates from every successful logical query.

#### Scenario: Multiple logical queries succeed
- **WHEN** a caller submits multiple valid logical queries
- **THEN** the result contains a trace for each query and candidates remain associated with their originating trace

#### Scenario: Legacy query remains compatible
- **WHEN** a caller submits the existing singular `query` input
- **THEN** the system executes one logical query and returns the existing top-level compatibility fields

### Requirement: Search constraints are explicit and auditable
The system SHALL normalize freshness, included domains, excluded domains, language, region, content types, and result limits, and SHALL distinguish provider-applied, emulated, post-filtered, and unsupported constraints.

#### Scenario: Provider cannot apply a constraint
- **WHEN** a requested constraint is unsupported by the selected provider
- **THEN** the successful result identifies that constraint as unsupported and MUST NOT claim it was applied

### Requirement: Web read produces stable source evidence
Every successful public Web read SHALL return a canonical source identity, immutable snapshot identity, normalized content digest, bounded passages, extraction signals, and retrieval lineage while preserving the existing bounded content field.

#### Scenario: Same canonical content is read twice
- **WHEN** the same canonical URL and normalized content are read repeatedly under the same segmentation version
- **THEN** the source, snapshot, and passage identities are stable

### Requirement: Source evidence can be found and reopened locally
The host grounding runtime SHALL find relevant passages and reopen a bounded passage window from a persisted source snapshot without issuing another network request.

#### Scenario: Find within a fetched source
- **WHEN** a caller searches a persisted source identity for a phrase
- **THEN** the runtime returns matching passage references from that snapshot and performs no network access

### Requirement: Candidate snippets are not strong evidence
Search-result snippets SHALL be classified as candidate-only evidence and MUST NOT independently satisfy a mandatory material-claim support requirement.

#### Scenario: Answer cites only a search snippet
- **WHEN** a material claim references only candidate-only search evidence
- **THEN** claim-support validation fails or reports the claim as unsupported
