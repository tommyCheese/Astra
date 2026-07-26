# Rollout verification

Verified on 2026-07-26.

## Automated verification

- Backend: `pytest -q` — 423 passed, 8 skipped.
- Backend lint: `ruff check app tests` — passed.
- Migrations: upgraded to head, downgraded to `0017_simplify_modes`, then upgraded to head — passed.
- Frontend typecheck: `npm run lint` — passed.
- Frontend tests: `npm test -- --run` — 82 passed.
- Frontend production build: `npm run build` — passed; only the existing Vite chunk-size advisory remains.
- OpenSpec: `openspec validate add-parallel-dag-execution --strict` — valid.

## Runtime evidence

- Controlled integration test observed two independent Workers active at once and then delayed the fan-in node until both predecessors completed.
- Concurrent scheduler test proved a one-slot Run creates only one current attempt when two schedulers race.
- Timeout test proved a safe read-only action creates one new attempt and does not change its canonical PlanNode.
- Failure test proved all necessary descendants become blocked while an unrelated branch completes.
- Recovery test classified stale attempts into resumable, replayable and non-idempotent result-unknown groups.
- Replan test proved the old attempt becomes terminal before Plan version 2 is activated.
- Cancellation test proved execution terminal state, resource release, budget settlement and graph event persistence.

## Browser verification

The development complex DAG fixture was opened in the in-app browser at 1280×720.

- Verified 16-node fan-out/fan-in rendering with simultaneous running, waiting-resource, failed and transitively blocked branches.
- Verified active count `2`, slot usage `2/3`, wait labels and fan-in `N/M` labels.
- Verified zoom in, zoom out, center and active-node navigation controls.
- Verified expanded graph and conversation panes both measured 607 px wide.
- Verified accessible node names and the live status summary are present in the rendered accessibility tree.

Mobile viewport, OS high-contrast and reduced-motion browser emulation remain a manual release check; the corresponding responsive and media-query styles plus component coverage are present.

## Rollback

Set `AGENT_PARALLEL_EXECUTION_ENABLED=false` to restore single-slot scheduling without removing persisted NodeExecution history. Side-effecting or unknown-resource tools remain exclusive regardless of the flag.
