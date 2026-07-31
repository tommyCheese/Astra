## 1. Memory runtime controls

- [x] 1.1 Add bounded Memory runtime settings normalization, default merging, atomic persistence, and shared Settings activation to RuntimeProfileService
- [x] 1.2 Add typed read/update API behavior and coordinate dynamic AutoDream startup and shutdown
- [x] 1.3 Add backend tests for defaults, valid modes, invalid atomic rejection, restart persistence, and AutoDream lifecycle changes

## 2. Settings information architecture

- [x] 2.1 Add Agent and Experimental settings categories, move Agent Profile out of Runtime, and scope evolution messaging to Agent Improvement
- [x] 2.2 Build the Memory center with runtime settings, stored Memory, consolidation, and audit views using progressive disclosure
- [x] 2.3 Refactor MemoryWorkbench into reusable scoped views with basic and audit detail modes

## 3. Product polish and verification

- [x] 3.1 Add responsive styles and Chinese/English copy that explain instruction versus enforced-setting boundaries
- [x] 3.2 Add frontend API and interaction tests for navigation, Memory settings persistence, view separation, and failed-save preservation
- [x] 3.3 Update documentation and run backend/frontend tests, type-check, i18n, lint, and OpenSpec validation

## 4. Merge stored Memory and audit views

- [x] 4.1 Replace the duplicate activity-and-audit top-level tab with collapsible audit details inside stored Memory
- [x] 4.2 Remove obsolete view-mode plumbing and update navigation, progressive-disclosure, and single-fetch tests
- [x] 4.3 Update documentation and run frontend build, tests, i18n, and strict OpenSpec validation
