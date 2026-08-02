## ADDED Requirements

### Requirement: Grounding outcomes participate through verification requirements
The Completion Gate SHALL enforce mandatory grounding ValidationOutcome failures when selected by the TaskContract and MUST NOT globally require research-specific validators merely because the capability is installed.

#### Scenario: Ordinary non-Web trusted task completes
- **WHEN** a trusted task has no grounding requirement and uses no canonical Web evidence
- **THEN** the absence of Web sources does not block completion

#### Scenario: Required grounding validator fails
- **WHEN** the TaskContract requires a grounding validator and its blocking outcome fails
- **THEN** the Completion Gate reports the corresponding unmet verification requirement
