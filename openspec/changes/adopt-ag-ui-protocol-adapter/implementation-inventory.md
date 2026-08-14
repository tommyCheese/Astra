# AG-UI Protocol Adapter Implementation Inventory

Baseline: 2026-08-12. The current worktree contains an advanced AG-UI implementation wired to a new first-party entry surface, with parity and rollout gates still open.

Task reconciliation: 64 of 76 tasks have direct code/test evidence; 12 remain open.

## Implemented Evidence

| Area | Current code/evidence | Assessment |
|---|---|---|
| Protocol baseline | `contracts/`, exact frontend dependencies, `backend/app/interfaces/ag_ui/compatibility.py`, golden fixtures | implemented |
| Durable correlation | AG-UI database model, migration, binding repository and restart/concurrency tests | implemented |
| Inbound/API | strict schemas, capability endpoint, input adapter, feature-gated HTTP/SSE route, resume/cancel bindings | implemented |
| Public projection | lifecycle/text/reasoning/tool/State/Activity/interrupt projector, stable identifiers, encoder | implemented with tool-data limitations |
| Safety/recovery | sanitizers, event limits, safe Patch generation, revision/cursor fallback, idempotent source handling | implemented for focused scenarios |
| Frontend foundation | `frontend/src/agui/transport.ts`, `store.ts`, `batching.ts`, `components.tsx`, `useAgUiConversation.ts`, `AgUiChatPage.tsx` | wired to the first-party entry behind an environment switch |
| Verification | 36 focused backend tests and 18 focused frontend compatibility/store/component/chat tests pass | strong component-level evidence; browser/full-suite gates remain |

## Important Gaps

- `AgUiChatPage` is a compact replacement surface, not a migration of the established conversation shell. Conversation navigation, existing specialized graph/process UX and visual parity are not demonstrated by the current page.
- Tool arguments are currently emitted as `{}` and tool results expose a safe status/error envelope rather than a reviewed allowed-output projection.
- Disconnect handling preserves partial state locally but does not yet prove full authoritative reconnect reconciliation in the integrated chat.
- `ag_ui_enabled` and `VITE_AG_UI_ENABLED` currently select AG-UI by default, despite the proposal requiring development-only rollout and acceptance gates before defaulting. This must be resolved during review; current defaults are not evidence that rollout tasks are complete.
- The focused tests do not replace full protocol conformance, all fault-injection cases, browser accessibility/reconnect coverage, development rollout or default-transport gates.

## Remaining Work Order

1. Close backend projection gaps: public tool data policy, exhaustive interrupt/recovery cases and conformance/fault injection.
2. Decide whether to migrate the established conversation shell or explicitly accept the smaller AG-UI surface; preserve native rollback either way.
3. Run browser parity, accessibility, reconnect and latency verification.
4. Enable development-only rollout; making AG-UI the default remains a separate gated decision inside this change.

## Dependencies and Boundaries

- Persisted Astra RunEvent and Run snapshots remain the sole authority; AG-UI is a public projection, never a runtime dependency.
- The Hook proposal must consume the same canonical facts independently and must not route Hook delivery through AG-UI.
- Entry-point wiring alone is not evidence of first-party parity or rollout acceptance.
