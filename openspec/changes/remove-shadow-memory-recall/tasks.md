## 1. Runtime contract

- [x] 1.1 Replace the legacy mode field with recall_enabled and migrate persisted shadow/off/on safely.
- [x] 1.2 Remove shadow retrieval while preserving historical audit compatibility.
- [x] 1.3 Add Run session identity, session namespaces, and migrate/remove production workspace Memory.

## 2. Product interface and documentation

- [x] 2.1 Send stable browser-session identity and update frontend settings, types, and tests.
- [x] 2.2 Correct the Memory article's recall modes and Task/session/Task Workspace boundaries.

## 3. Verification

- [x] 3.1 Update backend tests for settings migration, session isolation, and workspace rejection.
- [x] 3.2 Run frontend and backend suites, builds, migrations, and strict OpenSpec validation.
