## 1. Persistence and context contracts

- [x] 1.1 Add a migration and ORM field for versioned conversation context state
- [x] 1.2 Add API schemas for context status, system command catalog, and command results
- [x] 1.3 Implement model-family context window resolution and deterministic Token estimation

## 2. Context projection and mutation

- [x] 2.1 Implement context projection over summaries, folded Run IDs, and visible completed Runs
- [x] 2.2 Implement bounded idempotent compact and clear mutations with active-Run guards
- [x] 2.3 Integrate automatic threshold compaction and capacity rejection into Run creation
- [x] 2.4 Make the Agent runtime consume the managed context projection

## 3. Command and status APIs

- [x] 3.1 Implement the registered `/compact` and `/clear` system command catalog and dispatcher
- [x] 3.2 Add conversation context status and command execution API routes

## 4. Composer experience

- [x] 4.1 Generalize slash detection and filtering so system commands coexist with Skill options
- [x] 4.2 Add frontend API clients, command execution lifecycle, and context status refresh
- [x] 4.3 Add an accessible responsive context indicator and unified command menu with translations

## 5. Verification

- [x] 5.1 Add backend unit and API coverage for window resolution, estimates, projection, auto/manual compact, clear, and guards
- [x] 5.2 Add frontend tests for unified slash navigation, command execution, failure recovery, and live context display
- [x] 5.3 Run focused backend/frontend suites and OpenSpec validation, then resolve regressions

## 6. Model-selector context integration

- [x] 6.1 Move live context usage and action state into the model selector and remove the standalone Composer row
- [x] 6.2 Update responsive styling, accessibility assertions, and focused frontend tests

## 7. Model-level context capabilities

- [x] 7.1 Replace string-only provider model configuration with backward-compatible model profiles and a maintained capability catalog
- [x] 7.2 Add read-only per-model context capacity, output limits, official source metadata, and fallback state to Model Settings
- [x] 7.3 Extend context status and Run creation contracts so the selected model profile drives the server-side effective budget

## 8. Composer context visualization

- [x] 8.1 Replace the model-selector text placeholder with an accessible SVG circular context indicator and exact detail tooltip
- [x] 8.2 Refresh live context state when model profile configuration changes and preserve responsive Composer geometry

## 9. Verification

- [x] 9.1 Add backend tests for official catalog limits, rejected client overrides, and fallback metadata
- [x] 9.2 Add frontend migration, settings, model-switch, circular-indicator, accessibility, and responsive tests
- [x] 9.3 Run focused backend/frontend suites, production build, OpenSpec validation, and visual browser verification

## 10. Context tooltip interaction refinement

- [x] 10.1 Restrict the exact-value tooltip to circular-indicator hover while preserving the selected model control's assistive description

## 11. Official model context catalog

- [x] 11.1 Make the server-maintained, officially sourced model catalog the only context-capacity authority and remove client overrides
- [x] 11.2 Remove manual context controls from Model Settings, migrate legacy profiles to model IDs only, and expose official documentation sources
- [x] 11.3 Update backend/frontend coverage, run full focused verification, and validate the OpenSpec change

## 12. User-facing terminology

- [x] 12.1 Remove catalog, fallback, verification, metadata, and command-registry implementation terms from context-related UI
- [x] 12.2 Update command copy and frontend assertions, then run focused verification
