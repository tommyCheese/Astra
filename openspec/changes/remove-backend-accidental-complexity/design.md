## Context

The backend currently contains 61,883 production lines across 307 Python modules, with 792 classes and 2,492 functions or methods. Its broad product scope explains part of this size, but the implementation also repeats state across boundary models, creates classes for stateless one-step transformations, fragments aggregates across forwarding repositories and projections, and retains transitional compatibility behavior. The refactor must be deletion-first, preserve supported behavior, and remain safe in a dirty worktree containing earlier completed refactors.

## Goals / Non-Goals

**Goals:**

- Remove demonstrably redundant code and abstractions rather than merely moving or renaming them.
- Make the root-agent execution path and persistence operations readable through fewer indirections.
- Reduce production modules, classes, methods, public symbols, and lines from the recorded baseline.
- Preserve supported HTTP, persistence, permission, audit, recovery, tool, memory, and subagent behavior.
- Turn abstraction justification and compatibility removal into enforceable architecture rules.

**Non-Goals:**

- Removing supported product capabilities solely to reach a numerical target.
- Changing the database schema or public HTTP payloads.
- Introducing a generic repository framework, dependency-injection framework, or new abstraction layer.
- Combining unrelated capabilities into large miscellaneous modules.

## Decisions

### 1. Delete in evidence-backed cohorts

Each deletion cohort starts with usages, tests, and ownership boundaries. Code is removed when it is unreachable, duplicates an existing canonical representation, merely forwards to one owner, or provides a class boundary without state, polymorphism, framework integration, or domain identity.

Alternative considered: merge files until module counts fall. Rejected because moving unchanged code reduces navigation quality without reducing accidental complexity.

### 2. Prefer cohesive functions for stateless internal stages

Internal classes with one operation and no meaningful lifecycle or alternate implementation become named functions owned by the capability module. Closely coupled input/result wrappers are removed when explicit parameters and an existing canonical result make the flow clearer.

Alternative considered: retain every class for dependency injection. Rejected because tests can patch functions or inject the actual external dependency, and hypothetical substitution does not justify permanent indirection.

### 3. Keep external boundaries, compress internal representations

Pydantic request/response models and SQLAlchemy records remain at true external boundaries. Between those boundaries, the runtime uses the smallest existing canonical model instead of creating parallel projection or transfer objects that only copy fields.

Alternative considered: reuse ORM models everywhere. Rejected because it couples runtime logic to database sessions and weakens serialization and trust boundaries.

### 4. Consolidate by aggregate ownership, not generic infrastructure

Run and memory persistence fragments are merged only when they share the same aggregate, transaction, and callers. Shared SQL helpers remain local and explicit; no generic CRUD base repository is introduced.

Alternative considered: a universal repository superclass. Rejected because it hides domain queries and usually trades repeated lines for indirect behavior.

### 5. Compatibility code needs a live caller and a removal owner

Legacy branches, adapters, aliases, and fallback representations without a supported caller are deleted. Remaining compatibility code must be identified by the architecture audit with its current caller and removal condition.

### 6. Measure net simplification

The implementation records before/after production lines, modules, classes, methods, and public symbols. A cohort does not count as simplification if it only moves the same implementation or replaces direct code with an equally large framework.

## Risks / Trade-offs

- [Risk] Removing an internal adapter breaks a persisted legacy record → Mitigation: trace database values and fixtures, retain only compatibility paths exercised by supported persisted states, and run migration/recovery tests.
- [Risk] Collapsing stages makes a module too large → Mitigation: group only consecutive operations with the same owner and keep the existing architecture line limit.
- [Risk] Fewer internal types weaken validation → Mitigation: preserve validation at trust and persistence boundaries and use explicit parameters and return types internally.
- [Risk] Dirty-worktree edits overlap earlier refactors → Mitigation: inspect diffs before every cohort and never reset unrelated changes.
- [Risk] Numerical targets encourage harmful deletion → Mitigation: require behavior tests and an explicit rationale for every retained or removed abstraction.

## Migration Plan

1. Record the exact baseline and an inventory of compatibility paths and one-use abstractions.
2. Remove dead compatibility and forwarding code with focused tests.
3. Simplify runtime and subagent stateless stages, migrating all imports atomically.
4. Consolidate repository/projection fragments by aggregate, preserving transactions and result semantics.
5. Remove redundant internal contracts and update architecture enforcement.
6. Run focused suites after each cohort, then Ruff, architecture validation, full tests, strict OpenSpec validation, and diff checks.
7. Roll back an individual cohort by reverting only its explicit patch if a supported behavior cannot be preserved.

## Open Questions

None. The user explicitly prioritized deletion and accepts large internal changes; supported external behavior remains the safety boundary.
