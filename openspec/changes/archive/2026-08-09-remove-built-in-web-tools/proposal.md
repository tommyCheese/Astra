## Why

Astra no longer intends to provide first-party Web search or page-fetch capabilities. Keeping the built-in Web provider, configuration, runtime image, UI entries, and compatibility tests would preserve an unsupported attack surface and force future tool-runtime changes to account for dead behavior.

## What Changes

- **BREAKING**: Remove the built-in `web_search` and `web_fetch` tools and the `astra.web` provider.
- Remove Web-specific sandbox runtime configuration, search credentials, crawler configuration, processors, validators, effect analyzer wiring, and test fixtures.
- Remove Web provider/tool settings records through a migration so retired identities do not remain selectable.
- Remove frontend labels, status messages, documentation, deployment variables, and tests that imply Web support.
- Keep the generic plugin runtime capable of hosting independently supplied network-read providers; no compatibility alias or silent replacement is introduced.
- Existing historical Run, ToolCall, event, and catalog snapshot data remains readable.

## Capabilities

### New Capabilities

- `built-in-web-tool-retirement`: Defines the removal boundary, absence guarantees, persisted-state cleanup, and historical-data behavior for the retired built-in Web tools.

### Modified Capabilities

<!-- Existing Web capability specs become obsolete when this breaking change is archived. -->

## Impact

- Built-in plugin contributions and Web tool implementation modules.
- Runtime settings, deployment configuration, sandbox images, APIs, UI labels, docs, tests, and database migration chain.
- Mock model/test flows that currently rely on Web tools must use synthetic plugin tools or non-Web completion flows.
- Historical records keep their original names for audit-only display and are not executable.
