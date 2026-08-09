## 1. Remove Built-in Provider

- [x] 1.1 Remove `astra.web`, `web_search`, and `web_fetch` from built-in plugin assembly and default inventory
- [x] 1.2 Delete unused Web tool, sandbox adapter, effect analyzer, result processor, validator, and provider-specific helpers
- [x] 1.3 Remove Web-specific runtime settings, credentials, crawler configuration, and runtime image configuration

## 2. Persistence and API

- [x] 2.1 Add a migration that removes active Web tool/provider settings while preserving historical audit records
- [x] 2.2 Ensure dynamic settings APIs return unknown-target errors for retired identities and never expose them in catalog responses
- [x] 2.3 Add migration and API regression tests for the retirement boundary

## 3. Product Surfaces

- [x] 3.1 Remove frontend Web tool labels, status/configuration UI, and API assumptions
- [x] 3.2 Remove deployment variables and operator/user documentation that advertise built-in Web support
- [x] 3.3 Replace Web-specific mock flows with synthetic plugin fixtures where generic runtime coverage is still required

## 4. Verification

- [x] 4.1 Run repository-wide scans proving retired identities remain only in migration, negative tests, and historical change artifacts
- [x] 4.2 Run backend tests, frontend lint/build/tests, migration checks, and strict OpenSpec validation
