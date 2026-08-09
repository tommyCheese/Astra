## 1. Remove Retired Conversion Surface

- [x] 1.1 Delete the unreferenced built-in Web output normalizer and raw result-to-grounding-fragment adapter
- [x] 1.2 Prune grounding identity helpers and concrete search/source schemas that become unreachable

## 2. Preserve Generic Grounding Contracts

- [x] 2.1 Remove obsolete conversion-only tests while retaining ledger, projection, persistence, validator, and plugin evidence coverage
- [x] 2.2 Add an architecture regression check proving retired Web implementation modules are absent and active production modules remain reachable or declared dynamic resources

## 3. Verification

- [x] 3.1 Run focused grounding, plugin, architecture, and import checks
- [x] 3.2 Run the complete backend test suite, strict OpenSpec validation, and whitespace checks
