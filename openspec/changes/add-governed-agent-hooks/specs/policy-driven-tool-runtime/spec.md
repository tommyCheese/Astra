## ADDED Requirements

### Requirement: Tool admission Hooks execute at a stable pipeline boundary
InvocationPipeline SHALL dispatch matching tool admission Hooks only after tool resolution and initial input-schema validation and before trusted Effect analysis and the canonical Permission Engine decision.

#### Scenario: Hook denies resolved invocation
- **WHEN** a matching `tool.before_authorize` Hook denies a schema-valid resolved invocation
- **THEN** the invocation is recorded as blocked and no Effect authorization, approval grant consumption, executor call, or tool side effect occurs

#### Scenario: No Hook matches
- **WHEN** no frozen Hook binding matches an invocation
- **THEN** the invocation continues through the existing Effect, authorization, execution, result, processing, and validation stages without a tool-name-specific branch

### Requirement: Hook input patches invalidate prior security analysis
If a tool admission Hook proposes an allowed input patch, InvocationPipeline MUST validate the patch capability and conflicts, apply at most one mutation round, revalidate the complete input schema, generate a new candidate digest, run trusted Effect analysis, freeze the resulting Effect Plan, and obtain a new canonical authorization before execution.

#### Scenario: Patch changes target path
- **WHEN** a Hook changes a file or resource target in tool input
- **THEN** any prior Effect Plan, approval preview, matcher result, grant match, or authorization for the unpatched target is discarded

#### Scenario: Patched input is invalid
- **WHEN** the final patched input fails the ToolSpec schema or protected-field policy
- **THEN** the invocation fails safely before Effect authorization or execution

#### Scenario: Patch would trigger the same Hook again
- **WHEN** accepted mutation produces the final tool input
- **THEN** the same pre-tool Hook set is not recursively reinvoked for another mutation round

### Requirement: Post-tool Hooks observe canonical outcomes
Post-tool observation Hooks SHALL receive only persisted canonical success, failure, or blocked outcome projections and MUST NOT rewrite ToolResultEnvelope, ToolCall status, Artifact, Evidence, ValidationOutcome, completion signal, or observation history.

#### Scenario: Result Hook returns modified output
- **WHEN** a post-tool Hook attempts to replace tool data, evidence, artifacts, validation, or completion signals
- **THEN** the mutation is rejected while the canonical invocation outcome remains unchanged

#### Scenario: Result Hook triggers remediation action
- **WHEN** a post-tool Hook requests a separately permitted notification or remediation side effect
- **THEN** that effect is authorized and audited under the Hook principal rather than being treated as part of the completed tool invocation

