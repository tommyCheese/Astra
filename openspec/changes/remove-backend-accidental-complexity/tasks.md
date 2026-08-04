## 1. Baseline and deletion inventory

- [x] 1.1 Record exact production metrics and rank one-use classes, forwarding modules, compatibility paths, and duplicate internal representations.
- [x] 1.2 Classify candidates by live caller, framework or domain responsibility, external-boundary impact, and safe deletion strategy.

## 2. Compatibility and forwarding cleanup

- [x] 2.1 Remove unsupported legacy adapters, aliases, fallback branches, and their dedicated exports or tests.
- [x] 2.2 Remove package facades and forwarding functions that have one canonical owner, migrating all imports without compatibility re-exports.

## 3. Runtime abstraction reduction

- [x] 3.1 Replace stateless single-operation runtime policy and projection classes with cohesive typed functions.
- [x] 3.2 Remove dedicated runtime input/result containers that only copy an existing canonical object or obscure a consecutive execution flow.
- [x] 3.3 Co-locate consecutive root-agent stages when their separate modules provide no independent policy or lifecycle.

## 4. Subagent abstraction reduction

- [x] 4.1 Replace stateless one-operation subagent helpers with functions or merge them into their actual aggregate owner.
- [x] 4.2 Remove redundant subagent transfer objects and forwarding boundaries while preserving authorization, budget, recovery, and fan-in behavior.

## 5. Persistence simplification

- [x] 5.1 Consolidate run repository fragments and projections that share one session, aggregate, transaction boundary, and caller set.
- [x] 5.2 Consolidate memory persistence fragments that only forward or copy values, preserving provenance, lifecycle, and publication behavior.
- [x] 5.3 Delete repository support modules, methods, and result containers made redundant by the consolidation.

## 6. Contract and architecture controls

- [x] 6.1 Reuse canonical internal models and delete one-use field-for-field input, result, and projection classes outside trust boundaries.
- [x] 6.2 Extend architecture validation to report unjustified compatibility paths, one-operation internal classes, and net complexity-budget regressions.
- [x] 6.3 Document retained abstraction justifications, deleted paths, migration map, and before/after metrics.

## 7. Verification

- [x] 7.1 Run focused runtime, subagent, repository, memory, plugin, and API regression suites after their deletion cohorts.
- [x] 7.2 Run Ruff, architecture validation, the full backend test suite, strict OpenSpec validation, and diff checks.
- [x] 7.3 Confirm a net reduction in production modules, classes, functions or methods, public symbols, and physical lines from the recorded baseline.
