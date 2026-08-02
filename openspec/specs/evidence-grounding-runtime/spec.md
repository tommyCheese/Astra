# evidence-grounding-runtime Specification

## Purpose
TBD - created by archiving change build-grounded-web-tool-foundation. Update Purpose after archive.
## Requirements
### Requirement: Canonical evidence graph is run scoped
The system SHALL represent search traces, candidates, source snapshots, passages, claims, support edges, and citations with stable evidence identities scoped to one Run.

#### Scenario: Evidence is ingested
- **WHEN** a supported tool result produces canonical evidence
- **THEN** every persisted record contains its Run identity and available Plan node, NodeExecution, ToolCall, Artifact, digest, and timestamp lineage

### Requirement: Evidence ingestion is append-only and idempotent
The host SHALL persist schema-validated evidence fragments using deterministic keys, SHALL treat identical retries as idempotent, and SHALL reject a conflicting payload for an existing key.

#### Scenario: Tool result is replayed after recovery
- **WHEN** an identical evidence fragment is ingested again for the same Run
- **THEN** no duplicate record is created and the original record remains unchanged

### Requirement: Grounding context is bounded and reference based
The system SHALL construct synthesis context from bounded passages and evidence references instead of requiring provider-specific raw outputs or complete source bodies.

#### Scenario: Large source was fetched
- **WHEN** normalized source content exceeds the model context projection limit
- **THEN** the context builder includes selected bounded passages and stable references while the complete body remains an Artifact

### Requirement: Material claims bind to eligible evidence
Trusted grounded results SHALL represent material claims and their evidence bindings explicitly, and citations SHALL reference declared claims and eligible source passages from the current Run.

#### Scenario: Model invents an evidence identity
- **WHEN** a result references an evidence identity not present in the current Run ledger
- **THEN** citation-integrity or provenance validation fails and the invented reference is not rendered

### Requirement: Grounding policies are workflow independent
The Evidence Grounding Runtime SHALL expose generic validators without importing or activating a Deep Research module, and validator requirements SHALL be selected by the frozen Run contract and profile.

#### Scenario: Ordinary trusted task does not use Web evidence
- **WHEN** a trusted Run has no grounding verification requirement and produces no canonical Web evidence
- **THEN** research-specific validators and source-count policies are not activated

### Requirement: Existing trusted completion semantics are reused
Grounding validators SHALL return standard ValidationOutcome records, VerificationEngine SHALL aggregate them, and CompletionGate SHALL enforce mandatory failures through existing verification requirements.

#### Scenario: Mandatory claim support fails
- **WHEN** `grounding.claim_support` is mandatory and reports a blocking failure
- **THEN** the trusted Run cannot complete successfully

