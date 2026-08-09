## Context

The plugin runtime now discovers tools from provider contributions, but Astra still ships a dedicated `astra.web` provider containing `web_search` and `web_fetch`. Web-specific code spans provider assembly, sandbox wrappers, settings, environment variables, result processing/validation, UI labels, tests, docs, and deployment images. Historical records also contain these tool identities.

## Goals / Non-Goals

**Goals:**

- Remove all executable and configurable first-party Web tool behavior.
- Ensure new Runs never expose or resolve the retired names.
- Remove Web-specific core/provider code rather than leaving disabled branches.
- Preserve read-only historical audit data.
- Keep generic third-party network-read plugin support intact.

**Non-Goals:**

- Delete historical ToolCalls, events, snapshots, evidence, or artifacts.
- Ban all future plugins from performing policy-approved network reads.
- Add replacement search/fetch tools.

## Decisions

### Delete the provider contribution and implementation

Remove `astra.web` from built-in assembly and delete its Web tool, sandbox adapter, processor, validator, and analyzer code when no remaining consumer exists. The generic plugin pipeline remains unchanged.

### Purge active settings but preserve history

Add a migration that deletes active `tool_settings` rows for `web_search` and `web_fetch` and `tool_provider_settings` rows for `astra.web`. Historical ToolCalls and catalog snapshots remain untouched because they are audit records, not executable configuration.

### Fail closed without compatibility names

Requests to update either retired tool identity or the retired provider use the existing unknown-target response. Model decisions naming a retired tool fail normal registry resolution. No alias, tombstone executor, or automatic replacement is registered.

### Remove product claims and fixtures

Delete settings labels, environment variables, docs, and first-party test fixtures that suggest Web support. Generic plugin integration tests use synthetic tool identities so the plugin system remains covered independently.

## Risks / Trade-offs

- [Old clients expect Web settings entries] → Treat this as an intentional breaking removal and document the unknown-target behavior.
- [Historical UI needs tool labels] → Render stored names generically; do not re-register executable tools merely for presentation.
- [Mock flows depend on Web tools] → Rewrite them around synthetic plugin fixtures or direct completion behavior.
- [Web-only modules have hidden consumers] → Use repository-wide import/name scans plus full backend/frontend suites before deletion is complete.

## Migration Plan

1. Remove provider registration and active configuration surfaces.
2. Delete unused implementation modules and update generic fixtures.
3. Apply the active-settings cleanup migration.
4. Update UI/docs/deployment files and run repository-wide scans.
5. Rollback restores schema-compatible setting rows only if explicitly downgraded; removed runtime code still requires application rollback.
