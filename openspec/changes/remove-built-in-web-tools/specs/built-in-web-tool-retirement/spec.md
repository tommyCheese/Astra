## ADDED Requirements

### Requirement: Built-in Web tools are absent
The system MUST NOT register, expose, resolve, or execute built-in tools named `web_search` or `web_fetch`, and MUST NOT load a built-in provider identified as `astra.web`.

#### Scenario: Build the application tool catalog
- **WHEN** Astra assembles the built-in plugin catalog
- **THEN** neither retired tool name nor the retired provider appears in manifests, settings, or model-visible capabilities

#### Scenario: Model selects a retired tool
- **WHEN** a model decision names `web_search` or `web_fetch`
- **THEN** normal registry resolution rejects the decision without invoking a compatibility executor

### Requirement: Retired Web configuration is unavailable
Runtime and Tool Settings APIs MUST NOT return or accept Web provider configuration, Web credentials, crawler parameters, or state toggles for the retired identities.

#### Scenario: Update retired tool state
- **WHEN** a client submits a state update for `web_search`, `web_fetch`, or `astra.web`
- **THEN** the API returns the standard unknown-target error and persists no configuration

#### Scenario: Read Runtime Settings
- **WHEN** a user opens Runtime or Tool Settings
- **THEN** no Web tool, provider, search credential, crawler option, or Web runtime image control is displayed

### Requirement: Active persisted Web state is removed
The migration chain SHALL delete active tool and provider setting rows for the retired identities without deleting historical Run audit data.

#### Scenario: Upgrade an existing database
- **WHEN** a database contains Web tool/provider settings and upgrades to the retirement revision
- **THEN** those active settings are removed while historical ToolCalls, events, evidence, and catalog snapshots remain readable

### Requirement: Generic network plugins remain supported
Removing first-party Web tools MUST NOT introduce tool-name checks that prohibit independently supplied, trusted plugin tools from declaring policy-governed network-read effects.

#### Scenario: Load a third-party network-read tool
- **WHEN** a trusted plugin contributes a differently named tool with valid network-read declarations
- **THEN** the generic plugin catalog and invocation pipeline process it according to normal trust, permission, and isolation policy

### Requirement: Product surfaces make no Web support claim
Frontend labels, deployment configuration, operator documentation, and executable tests SHALL NOT claim that Astra ships Web search or fetch support.

#### Scenario: Scan active product sources
- **WHEN** active code, configuration, and documentation are inspected after the change
- **THEN** retired identities occur only in the retirement migration, negative tests, and historical OpenSpec records
