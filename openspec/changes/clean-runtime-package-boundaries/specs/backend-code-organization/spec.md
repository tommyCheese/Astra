## ADDED Requirements

### Requirement: Runtime implementation ownership is explicit
Environment-specific runtime adapters SHALL live under infrastructure runtime ownership, while bootstrap modules SHALL contain only application/container construction and lifecycle wiring.

#### Scenario: Maintainer locates Trusted execution integration
- **WHEN** a maintainer navigates from the Agent Loop capability slot to its Trusted implementation
- **THEN** the concrete adapter is located under `infrastructure.runtime` rather than `infrastructure.bootstrap`

### Requirement: Read projections have projection ownership
HTTP-facing and run-facing read projections SHALL live under interface or application projection packages and SHALL NOT be represented as infrastructure repositories.

#### Scenario: Maintainer locates run view construction
- **WHEN** a maintainer searches for construction of the public run view
- **THEN** the projection has one canonical path whose package name identifies it as a projection
