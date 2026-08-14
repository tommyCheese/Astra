# Context Compaction Implementation Inventory

Baseline: 2026-08-12. This inventory reflects the current package boundaries and separates canonical/audit persistence from model-visible projections.

## Current Owners

| Role/path | Current owner | Implemented behavior | Remaining proposal scope |
|---|---|---|---|
| Conversation | `backend/app/application/run_management/conversations/context.py` | V2 semantic checkpoint input, automatic/manual compaction, legacy input migration, status projection | recovery/fault testing, evaluation, staged rollout |
| Shared compaction | `backend/app/application/context_compaction/` | policy, accounting, prompts/parsing, semantic generation, deterministic emergency, validation, CAS installation, large tool-output handling | richer reference-access validation, telemetry, concurrency/crash hardening |
| Standard/trusted root | canonical `backend/app/application/agent_runtime/` composition and loop, with context services under `services/context/` | protected prefixes, pre-model/post-tool checks, checkpoint + recent tail injection, canonical completion remains state-driven | damaged/incompatible checkpoint regeneration and long-loop evaluation |
| Quick/trusted child | `backend/app/application/subagents/` plus `application/context_compaction/child.py` | isolated child window, manifest/contract hashes, retained continuation answers and remaining budget | data-label/purpose validation and classified result when protected context cannot fit |
| Persistence/accounting | `backend/app/infrastructure/repositories/context_compaction.py`, usage repositories and common checkpoint schemas | idempotency/CAS/supersession and reported-versus-estimated usage inputs | crash-point, worker recovery and lifecycle analytics coverage |

## Confirmed Invariants

- Full Runs, Turns, ToolCalls, Artifacts and Evidence remain authoritative and are never deleted by compaction.
- Active model context is rebuilt from a protected prefix, one compatible Astra checkpoint and a bounded chronological raw tail.
- Standard paths do not synthesize trusted-only TaskContract or Plan state.
- Child compaction never receives full parent history, sibling state, credentials or hidden reasoning.
- Compaction uses Astra's generic model generation boundary and never depends on provider-specific compaction endpoints or opaque items.

## Remaining Work Map

- Tasks 7.5 and 7.7: complete child reference authorization semantics and protected-prefix overflow outcomes.
- Tasks 9.1–9.6: recovery compatibility, regeneration, telemetry/redaction, concurrency and crash-point proof.
- Tasks 10.1–10.8: deterministic fixtures, repeated compaction, portability, end-to-end long loops, comparative evaluation, rollout and documentation.

## Dependencies and Review Notes

- The proposal depends on the current canonical runtime and Subagent schemas, not on legacy `backend/app/runner/*` paths.
- The future Hook system may observe and apply narrowly defined admission at the compaction boundary, but cannot mutate protected prefixes or checkpoints.
- Review should focus on the remaining correctness/evaluation gates; package relocation and the main V2 compaction algorithm are already implemented.
