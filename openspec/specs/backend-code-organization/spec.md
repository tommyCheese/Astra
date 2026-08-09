# backend-code-organization Specification

## Purpose
TBD - created by archiving change organize-backend-code-kinds. Update Purpose after archive.
## Requirements
### Requirement: Capability-first code organization
The backend SHALL organize production code by Agent or business capability first and by code role within that capability second; it SHALL NOT create global technical buckets for unrelated domain objects, validators, utilities, or services.

#### Scenario: Locating a model normalization rule
- **WHEN** a maintainer navigates from the model client capability to response normalization code
- **THEN** the package path identifies both `model_clients` as the capability and `normalization` as the code role

### Requirement: Stable code-role semantics
Role subpackages SHALL have non-overlapping responsibilities: models contain structural types, validation contains invariant checks, utilities contain deterministic side-effect-free operations, policies contain decisions, services coordinate use cases, and transports implement external communication.

#### Scenario: Reviewing a validation module
- **WHEN** a module is placed under a `validation` package
- **THEN** it performs validation or parsing checks without persistence, network calls, or use-case orchestration

### Requirement: Controlled package granularity
The backend SHALL create a role subpackage only when multiple related modules or a multi-module feature slice benefit from grouping, and SHALL keep a type beside its sole consumer when separation would add navigation without reuse or clarity.

#### Scenario: Encountering one local dataclass
- **WHEN** a dataclass is used only by one short service implementation
- **THEN** the dataclass remains colocated instead of receiving a standalone module or package

### Requirement: Descriptive utility ownership
Pure helper functions SHALL remain inside their owning capability and use purpose-specific module names; production code SHALL NOT introduce generic `utils.py`, `helpers.py`, or `common.py` dumping grounds.

#### Scenario: Adding deterministic formatting logic
- **WHEN** a contributor adds a reusable pure formatter for a tool capability
- **THEN** the formatter is placed in that tool capability with a name describing the transformation rather than in a global utility module

### Requirement: Single import owner
Each moved concept SHALL have one canonical production import path, and the backend SHALL migrate consumers without retaining compatibility re-exports or forwarding modules at the old path.

#### Scenario: Completing a module-role migration
- **WHEN** a concept moves from a flat capability root into a role subpackage
- **THEN** production code and tests import the canonical new path and the old module is absent

### Requirement: Net structural reduction
The completed organization SHALL reduce the total production module count, class count, and Python line count from the recorded baseline. A class SHALL be retained only when it represents a framework contract, a meaningful domain type, state across calls, or a boundary with multiple substitutable implementations.

#### Scenario: Reviewing a stateless wrapper
- **WHEN** a class only forwards one operation and has no state or substitutable implementations
- **THEN** the implementation uses a direct function or call and removes the wrapper class

### Requirement: Behavior-preserving organization
The reorganization SHALL preserve HTTP, SSE, persistence, model-provider, tool-execution, approval, and Agent decision behavior.

#### Scenario: Running the backend verification suite
- **WHEN** the role-based package migration is complete
- **THEN** architecture checks, API contracts, persistence checks, plugin discovery, and the complete backend test suite pass without a behavior compatibility layer

