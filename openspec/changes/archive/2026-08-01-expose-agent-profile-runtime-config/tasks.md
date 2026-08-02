## 1. Runtime Profile backend

- [x] 1.1 Extend RuntimeProfileService to expose, validate, atomically persist, activate, and reset the complete Agent Profile document set
- [x] 1.2 Bind the active Runtime Agent Profile resolver into application startup and all new Run/Profile loading paths
- [x] 1.3 Add typed Runtime API request models and update/reset endpoints without changing public Run redaction

## 2. Runtime settings UI

- [x] 2.1 Extend frontend Runtime API types and client methods for Agent Profile updates and reset
- [x] 2.2 Add a four-document Markdown editor with source/version display, dirty state, save, validation errors, and restore-default behavior
- [x] 2.3 Add scoped responsive styles and English translations for the Profile editor

## 3. Verification and documentation

- [x] 3.1 Add backend service and API tests for default reads, valid activation, invalid atomic rejection, reset, and new Run snapshot behavior
- [x] 3.2 Add frontend tests for editing, saving, failed validation preservation, and restoring defaults
- [x] 3.3 Update Agent Profile documentation and run focused backend/frontend test, type-check, and i18n validation suites
