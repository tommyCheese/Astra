## 1. Compatibility audit

- [x] 1.1 Inventory and classify legacy persisted-data compatibility across backend, frontend, and migrations.
- [x] 1.2 Add strict-current-schema tests for each retained persistence boundary.

## 2. Runtime cleanup

- [x] 2.1 Remove legacy runtime profile and Agent Profile snapshot migration paths.
- [x] 2.2 Remove legacy Agent state, plan graph, result, and reasoning-policy migration paths.
- [x] 2.3 Remove legacy Memory namespace, kind, shadow, and workspace metadata paths.
- [x] 2.4 Remove legacy permission and other persisted-record compatibility projections.
- [x] 2.5 Remove obsolete frontend persisted-data types and display branches.

## 3. Database baseline and reset

- [x] 3.1 Replace incremental Alembic history with one current-schema baseline.
- [x] 3.2 Stop the local backend and remove active, sidecar, and backup Astra database files.
- [x] 3.3 Create a fresh database from the baseline and restart the local backend.

## 4. Verification

- [x] 4.1 Run backend and frontend lint, tests, and production build.
- [x] 4.2 Verify empty-database startup and create/read current Task, Run, Memory, and settings data.
- [x] 4.3 Run strict OpenSpec validation and document the removed compatibility surface.
