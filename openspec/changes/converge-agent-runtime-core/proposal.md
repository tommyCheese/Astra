## Why

Astra currently implements standard, trusted, recovery, planning, and Run lifecycle orchestration across `fast_agent_runtime`, `agent_runtime`, `runner`, and `run_management`, while the product contract says answer modes share one Agent Loop and differ primarily in planning and trusted completion behavior. This duplication makes the main execution path difficult to read, lets safety behavior drift between modes, and keeps legacy and future-facing platform concerns inside the core runtime.

The change has three equal, non-negotiable outcomes: a clean architecture with explicit ownership, clean code with less duplication and indirection, and unchanged working product behavior. A slice is not complete if it improves only one or two of these outcomes.

## What Changes

- Replace the separate Fast and Trusted controllers with one minimal typed Agent Loop. Its fixed lifecycle is limited to loading the current state, building model input from registered contributions, obtaining one decision, routing a control or action outcome, recording the resulting observation, and converging on `continue`, `wait`, `complete`, `blocked`, `failed`, or `cancelled`. The migration SHALL evolve directly toward this target rather than preserving two controllers as the intended intermediate architecture.
- Dynamically compose all non-essential behavior as typed Runtime capability plugins selected by the frozen Run Profile. Planning, reflection, verification, CompletionGate, Memory context/writeback, Subagent delegation, Skill context, evidence handling, and similar capabilities MUST contribute through bounded lifecycle contracts rather than adding branches to the Loop.
- Express standard and trusted behavior as two plugin compositions over that Loop: standard loads only the lightweight mandatory safety/action capabilities; trusted additionally loads planning, reflection, verification, and CompletionGate capabilities without owning a second control loop or tool-execution path.
- Keep Runtime capability plugins distinct from externally discovered Tool Provider Plugins. Runtime composition uses platform-registered, typed, trusted implementations frozen for the Run; it MUST NOT scan Task Workspaces or dynamically import untrusted code into the API process.
- Make `run_management` own Run creation, dispatch, continuation, recovery entry, and terminal persistence; make `planning` own trusted DAG state and scheduling as a Runtime capability plugin; eliminate the generic `runner` ownership layer.
- Replace duplicated Fast/Trusted tool, approval, error, cancellation, and recovery branches with shared contracts and contract tests before deleting old paths.
- Isolate Memory consolidation/AutoDream, Evolution, Credential administration, and other non-iteration control planes behind application ports; they may contribute bounded context or receive outcomes but do not participate in core loop routing.
- Delete repeated Schema → dataclass → domain object → projection chains. A representation may remain only when it owns a real boundary or invariant: validation of untrusted external input, database mapping, domain behavior that cannot live at either boundary, or a deliberately redacted/versioned public view. Package layering alone is not justification for another model.
- Use one canonical runtime representation for decisions, actions, observations, outcomes, checkpoints, and capability identities. Runtime capability plugins MUST consume that representation directly and MUST NOT introduce capability-local mirror DTOs.
- Provide a short, continuous code-reading path from Run dispatch to one Agent iteration and from an action to its persisted observation.
- Retire `legacy-standard-v1` only after repository data and continuation compatibility checks prove no resumable consumer remains; do not silently rewrite historical Runs.
- Preserve HTTP, SSE, database, permission, approval, Sandbox, Workspace, Artifact, Skill, Memory, Subagent, and cancellation behavior during the refactor.
- Add no new product capability in this change. Moving an incomplete capability behind a peripheral adapter MUST preserve its currently supported API and behavior; removing or enabling that capability requires a separate product decision.

Every implementation slice must pass all three acceptance gates:

1. **Architecture cleanliness**: one owner per responsibility, one Runtime/Loop, deterministic typed capability composition, no forbidden dependency or compatibility facade, and a simpler import graph.
2. **Code cleanliness**: net deletion of duplicated models, mappings, wrappers, branches, modules, classes, and public symbols; names and files express real concepts; no new abstraction without at least one distinct invariant or replaceable consumer.
3. **Functional correctness**: unchanged HTTP/OpenAPI, SSE, persisted state, approval, recovery, cancellation, security, workspace/artifact, Skill, Memory, Subagent, and answer-mode behavior, proven by characterization, contract, integration, migration, and frontend tests appropriate to the slice.

Implementation priority is fixed as follows:

1. Converge Fast and Trusted onto one Runtime, then collapse overlapping orchestration ownership across `runner`, `agent_runtime`, and `run_management`.
2. While converging each vertical slice, remove every mirror Schema/dataclass/domain/projection representation that does not enforce a distinct invariant; do not postpone model cleanup until after path migration.
3. Move AutoDream, Evolution, Credential Broker administration, and other capabilities that are not fully integrated with serving behavior out of the core Runtime; if later validated, they may return only as typed capability plugins or peripheral adapters.
4. Finish with a short core-loop reading path in a small number of cohesive modules, with platform capabilities visible as explicit adapters.

## Capabilities

### New Capabilities

- `agent-runtime-core-cohesion`: Defines ownership, the minimal single Loop, typed capability-plugin composition, two mode compositions, peripheral adapter rules, and readability/duplication constraints for the Agent runtime.

### Modified Capabilities

- `answer-mode-selection`: Clarifies that standard and trusted modes must share the same action-safety and invocation kernel and may differ only through explicit planning, reflection, verification, and completion policies.
- `general-agent-reasoning`: Defines a single iteration outcome protocol and prevents Run lifecycle, DAG scheduling, and peripheral control planes from creating alternative Agent action pipelines.

## Impact

- Primary backend areas: removal of `app/application/fast_agent_runtime` as an independent controller, convergence on a minimal Loop and capability-plugin contracts in `app/application/agent_runtime`, elimination of generic `app/application/runner` ownership, DAG behavior moving to a planning capability, Run use-case ownership in `app/application/run_management`, typed runtime composition in `app/infrastructure/bootstrap`, and their tests.
- Supporting areas: consolidation of runtime schemas/contracts and public projections, tool invocation and permission composition, recovery projections, architecture rules, benchmarks, and backend design documentation.
- Public APIs and persisted schemas are expected to remain compatible during convergence. Any later removal of a persisted runtime identity or compatibility reader requires a separate explicitly breaking change.
- The change SHALL reduce production modules, classes, mapping functions, public symbols, and duplicated mode-specific tests rather than merely moving files. Any surviving intermediate representation must be listed with its distinct owner and invariant in the design inventory.
- Completion requires before/after architecture inventory, import-graph validation, focused Fast/Trusted behavior parity tests, full backend and frontend verification, and an explicit record of any intentionally retained compatibility surface.
