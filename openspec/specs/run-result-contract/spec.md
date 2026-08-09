# run-result-contract Specification

## Purpose
TBD - created by archiving change tighten-run-result-contract. Update Purpose after archive.
## Requirements
### Requirement: Run results have a documented typed contract
The system SHALL expose every present run result through a formal schema containing the answer summary, findings, sources, evidence diagnostics, caveats, verification data, audit references, completion decision, and structured error when applicable. The system MUST NOT expose undocumented top-level result fields.

#### Scenario: Completed run is retrieved
- **WHEN** a client retrieves a completed run
- **THEN** the response result conforms to the documented run result schema and OpenAPI describes its nested fields

#### Scenario: Failed run is retrieved
- **WHEN** a client retrieves a blocked or failed run with a structured error
- **THEN** the response exposes the error through the documented error field while retaining the standard answer collections

#### Scenario: Internal extension key exists
- **WHEN** persisted result JSON contains an undocumented top-level key
- **THEN** the API omits that key from the serialized run result

### Requirement: Historical results remain readable
The system SHALL normalize historical persisted result JSON at the API boundary without requiring a database migration. Missing optional collections MUST serialize with stable empty defaults, and malformed optional collection members MUST NOT make the entire run endpoint fail.

#### Scenario: Legacy result omits newer fields
- **WHEN** a historical result contains a summary but omits evidence, verification, memory, or completion fields
- **THEN** the API returns that run successfully with safe defaults for the missing fields

#### Scenario: Legacy result contains malformed optional entries
- **WHEN** a historical optional collection contains nulls, scalars, or otherwise invalid members
- **THEN** the API safely discards or normalizes those members and still returns the run

#### Scenario: Run has no result yet
- **WHEN** an in-progress run has no persisted result
- **THEN** the API returns `result` as null

### Requirement: Verification has one canonical location
The system SHALL expose verification data only as `result.verification_report` and MUST NOT duplicate the report at the top level of the run response.

#### Scenario: Result includes verification report
- **WHEN** a result contains verification data
- **THEN** the normalized report is available at `result.verification_report` and no top-level `verification_report` field is present

### Requirement: Frontend consumers use the typed result
The frontend SHALL model and render the formal run result contract without depending on arbitrary top-level result keys.

#### Scenario: Answer contains evidence and caveats
- **WHEN** a typed result includes findings, sources, or caveats
- **THEN** the existing answer, evidence, source, and caveat presentation continues to render correctly

#### Scenario: Result contains a structured error
- **WHEN** a typed result includes an error
- **THEN** the frontend can read the documented error shape and present the established failure experience

