## 1. Baseline and ownership audit

- [x] 1.1 Record current production architecture metrics and the Standard, Trusted, and Run read-model module sequences
- [x] 1.2 Confirm every selected companion module has no independent consumer, policy, lifecycle, transaction, or substitution boundary

## 2. Standard Runtime checkpoint

- [x] 2.1 Merge standard checkpoint serialization and interrupted-action recovery into canonically named `standard_checkpoint.py`
- [x] 2.2 Migrate production and test imports and delete the old `standard_state.py` and `standard_recovery.py` paths without compatibility exports
- [x] 2.3 Run focused Standard Runtime checkpoint, recovery, permission, and result-unknown tests

## 3. Trusted Runtime composition

- [x] 3.1 Move Trusted Runtime composition values beside their capability composition owner
- [x] 3.2 Migrate all consumers and delete `trusted_state.py` without a compatibility export
- [x] 3.3 Run focused Trusted Runtime composition, execution, finalization, and recovery tests

## 4. Run read-model navigation

- [x] 4.1 Move Run read operations into the Run view owner and delete the thin query service module
- [x] 4.2 Migrate API and test imports and run focused Run API/read-model tests

## 5. Documentation and verification

- [x] 5.1 Update the backend module map with canonical entry paths, necessary boundaries, and before/after navigation sequences
- [x] 5.2 Record final production metrics and verify net module, symbol, and line reduction with no new hard-limit violation
- [x] 5.3 Run stale-import search, architecture checks, OpenSpec validation, and the complete backend test suite
