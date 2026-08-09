## ADDED Requirements

### Requirement: Versioned tool provider plugin contract
The system SHALL define a versioned Tool Provider Plugin contract that can contribute tool manifests, executor bindings, trusted effect analyzers, result processors, validators, approval presenters, runtime backends, and configuration schema without modifying the Agent Loop.

#### Scenario: Provider contributes a new tool
- **WHEN** an enabled and trusted provider contributes a valid tool and its supporting bindings
- **THEN** the tool enters the application catalog and can participate in the standard invocation and completion pipeline without a core runtime code change

#### Scenario: Provider uses an unsupported protocol
- **WHEN** a provider declares a plugin protocol version the host does not support
- **THEN** the provider is not loaded and a safe diagnostic identifies the protocol incompatibility

### Requirement: Trusted and bounded plugin discovery
The system MUST discover executable plugin contributions only from built-in sources or administrator-managed sources with verified provider identity and content digest, and MUST NOT discover or import executable plugins from a Task Workspace.

#### Scenario: Workspace contains plugin metadata
- **WHEN** a Task Workspace contains an entry point, plugin manifest, Hook, or executable registration file
- **THEN** the host ignores it as a plugin discovery source and does not import or execute it

#### Scenario: Managed provider digest changes
- **WHEN** an allowlisted provider's content digest no longer matches the configured identity
- **THEN** the provider is disabled before its contributions enter the application catalog

### Requirement: Deterministic catalog assembly and conflict rejection
The system SHALL assemble plugin contributions deterministically and SHALL reject duplicate model-visible tool names, component identifiers, or runtime backend identifiers rather than using registration order to overwrite an existing contribution.

#### Scenario: Two providers expose the same tool name
- **WHEN** two enabled providers attempt to expose the same model-visible tool name
- **THEN** catalog assembly fails with a conflict diagnostic and neither ambiguous binding becomes callable

#### Scenario: Same inputs produce a catalog digest
- **WHEN** the same verified plugin set and configuration revisions are assembled repeatedly
- **THEN** the resulting contribution ordering and catalog digest are identical

### Requirement: Plugin lifecycle and health isolation
The system SHALL track provider lifecycle and health states and SHALL expose only enabled and healthy contributions to new Runs.

#### Scenario: Provider health check fails
- **WHEN** an isolated provider fails its bounded health check
- **THEN** its tools are excluded from new Run manifests while other healthy providers remain available

#### Scenario: Result processor crashes
- **WHEN** a non-security result processor raises an exception for a tool result
- **THEN** the related invocation records a bounded processing failure without crashing unrelated providers or executing another action

### Requirement: Plugin configuration is schema-driven and secret-safe
The system SHALL expose plugin and tool configuration from validated schemas, persist generic provider and tool enablement state, and represent secrets only as credential references.

#### Scenario: UI requests the tool catalog
- **WHEN** the settings client requests available providers and tools
- **THEN** the API returns dynamic labels, availability, enablement, configuration schema, and safe diagnostics from the assembled catalog

#### Scenario: Provider requires an API credential
- **WHEN** a provider configuration includes a secret field
- **THEN** the stored configuration, API response, logs, and Run snapshot contain only a credential reference and never the secret value

### Requirement: Run snapshots freeze plugin behavior identities
The system SHALL freeze the plugin, provider, tool schema, executor, effect analyzer, processor, validator, and resolved configuration revision identities used by each Run.

#### Scenario: Analyzer changes while Run waits for approval
- **WHEN** a Run resumes after its bound effect analyzer version or digest has changed
- **THEN** the pending invocation is not executed until the new identity is explicitly accepted under policy

#### Scenario: Display metadata changes only
- **WHEN** only a non-behavioral label or description changes after a Run snapshot is frozen
- **THEN** the Run can resume using the frozen behavioral bindings without gaining new authority

### Requirement: External providers execute through bounded transports
The system MUST execute untrusted or external provider code through an isolated, resource-bounded transport and SHALL reserve in-process execution for built-in or administrator-managed providers.

#### Scenario: External provider returns oversized output
- **WHEN** an isolated provider response exceeds the configured result limit
- **THEN** the invocation fails with a safe transport error and the oversized payload is not admitted into the ToolCall result or model context

#### Scenario: External provider requests host services directly
- **WHEN** an external provider attempts to access a host capability outside its authorized ToolExecutionContext
- **THEN** the transport denies the access and records an auditable policy failure

