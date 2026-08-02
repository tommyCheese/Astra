## 1. Slash Command State and Matching

- [x] 1.1 Extract testable Composer helpers that detect a slash command range only at valid command boundaries and preserve URLs, paths, ordinary slash text, selection ranges, and IME composition.
- [x] 1.2 Add derived Skill option filtering across name, description, and qualified identity with deterministic ordering, selected-state annotation, and an explicit no-results state.
- [x] 1.3 Unify slash selection and the existing attachment-menu selection behind one deduplicated `selectedSkillIds` update path.

## 2. Composer Interaction and Visual Treatment

- [x] 2.1 Implement the Composer-anchored Skill listbox with active-option state, pointer selection, Arrow/Home/End navigation, Enter selection, Escape cancellation, focus restoration, and correct form-submission suppression.
- [x] 2.2 Remove the active slash query range on selection without changing surrounding message text or caret semantics.
- [x] 2.3 Add a persistent selected-Skill token rail with Skill icon/name, non-color selected semantics, individual remove controls, and empty-input Backspace removal of the last token.
- [x] 2.4 Add light, dark, narrow-layout, focus-visible, empty-state, and reduced-motion styling that matches Astra's Codex-like highlighted token treatment without obscuring the textarea or send controls.
- [x] 2.5 Add localized accessible labels and status text for opening, filtering, selecting, removing, unavailable selection, and no matching Skills.

## 3. Submission and Deterministic Activation

- [x] 3.1 Normalize selected qualified identities before Run creation and send them only through the existing `skill_ids` field, proving slash command text never enters the submitted goal or conversation history.
- [x] 3.2 Clear message, slash state, and selected Skill tokens only after successful Run creation; retain the complete draft on network, API, Catalog, or activation failure and clear it when starting a new conversation.
- [x] 3.3 Harden Run creation validation so explicit `skill_ids` are unique qualified identities, every identity resolves against the newly frozen Catalog, and multi-Skill activation succeeds atomically within the creation transaction.
- [x] 3.4 Verify and, where needed, enforce event ordering so every explicit `skill.activated` record and prompt binding exists before the first model invocation, with the exact frozen revision and digest.

## 4. Verification and Regression Coverage

- [x] 4.1 Add frontend unit tests for command boundaries, URL/path non-triggers, filtering, no-results, duplicate selection, keyboard/pointer interaction, Escape, caret restoration, Backspace removal, and accessible listbox/token semantics.
- [x] 4.2 Add App integration tests for highlighted tokens, attachment/slash state parity, exact `skill_ids` payloads, clean user-message content, successful one-Run consumption, failed-submission retention, new-chat clearing, dark theme, and narrow layouts.
- [x] 4.3 Add backend API and runtime tests proving explicit pre-activation cannot be skipped by a direct `finalize`, stale or revoked identities fail before model execution, multiple selections are atomic, and historical Runs keep their frozen revision.
- [x] 4.4 Run frontend i18n checks, TypeScript lint, Vitest, production build, targeted backend Skill/Run tests, and OpenSpec validation; document any unrelated pre-existing warnings.
