# AG-UI Protocol Adapter Implementation Inventory

Baseline: 2026-08-17. The current worktree contains an advanced AG-UI implementation wired to a development-only first-party preview, with established-chat parity and rollout gates still open.

Task reconciliation: 72 of 76 tasks have direct code/test evidence; 4 remain open.

## Implemented Evidence

| Area | Current code/evidence | Assessment |
|---|---|---|
| Protocol baseline | `contracts/`, exact frontend dependencies, `backend/app/interfaces/ag_ui/compatibility.py`, golden fixtures | implemented |
| Durable correlation | AG-UI database model, migration, binding repository and restart/concurrency tests | implemented |
| Inbound/API | strict schemas, capability endpoint, input adapter, feature-gated HTTP/SSE route, resume/cancel bindings | implemented |
| Public projection | lifecycle/text/reasoning/tool/State/Activity/interrupt projector, stable identifiers, bounded sanitized tool arguments, encoder | implemented; tool results intentionally remain status/error-only |
| Safety/recovery | sanitizers, event limits, safe Patch generation, revision/cursor fallback, idempotent source handling | implemented for focused scenarios |
| Frontend foundation | `frontend/src/agui/transport.ts`, `store.ts`, `batching.ts`, `components.tsx`, `useAgUiConversation.ts`, `AgUiChatPage.tsx` | wired to the first-party entry behind an environment switch |
| Verification | focused backend projection/route tests plus the full backend and frontend suites pass | strong automated evidence; browser acceptance gates remain |

## Important Gaps

- `AgUiChatPage` is a compact replacement surface, not a migration of the established conversation shell. Conversation navigation, existing specialized graph/process UX and visual parity are not demonstrated by the current page.
- Tool results expose a safe status/error envelope; expanding them to allowed public output remains a separate policy decision rather than an implicit projection.
- Disconnect handling preserves partial state locally but does not yet prove full authoritative reconnect reconciliation in the integrated chat.
- `ag_ui_enabled` is default-off and the frontend preview requires development mode, the `/__dev/ag-ui` path, and `VITE_AG_UI_ENABLED=true`, matching the staged rollout contract.
- Automated suites do not replace browser accessibility/reconnect coverage, development rollout evidence, or default-transport gates.

## Remaining Work Order

1. Move established-chat stream orchestration behind the transport-neutral store without replacing the existing product shell.
2. Run browser parity, accessibility, reconnect and native-rollback verification.
3. Record development-only security/accessibility/latency rollout evidence.
4. Make AG-UI the default only after the remaining gates pass; keep native transport removal out of this change.

## Dependencies and Boundaries

- Persisted Astra RunEvent and Run snapshots remain the sole authority; AG-UI is a public projection, never a runtime dependency.
- The Hook proposal must consume the same canonical facts independently and must not route Hook delivery through AG-UI.
- Entry-point wiring alone is not evidence of first-party parity or rollout acceptance.
