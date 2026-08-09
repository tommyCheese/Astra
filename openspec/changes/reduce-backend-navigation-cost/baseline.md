# Navigation-cost baseline

Recorded: 2026-08-10. Scope: `backend/app` production Python.

## Structural metrics

| Metric | Before |
| --- | ---: |
| Production lines | 60,619 |
| Modules | 302 |
| Classes | 762 |
| Public symbols | 1,189 |
| Functions and methods | 2,425 |
| Largest module | 770 lines |
| Largest function | 96 lines |
| Maximum measured complexity | 15 |

## Representative navigation sequences

| Use case | Before | Non-policy companion hop |
| --- | --- | --- |
| Standard execution checkpoint | `run_execution -> standard -> standard_state -> standard_recovery` | `standard_recovery` is only called by `StandardStatePort` and shares its checkpoint persistence protocol |
| Trusted execution composition | `run_execution -> trusted -> trusted_factory -> trusted_capabilities -> trusted_state` | `trusted_state` only contains values constructed and consumed by the capability factory/adapter |
| Run read model | `runs API -> query_service -> run_view -> RunUnitOfWork` | `query_service` immediately loads a record and applies the adjacent projection |

## Ownership audit

- `standard_recovery.py` has one production consumer, `standard_state.py`; its callback is the owner's private checkpoint writer and it has no independent implementation, lifecycle, transaction, or framework contract.
- `trusted_state.py` is imported only by `trusted_capabilities.py`, `trusted_factory.py`, `trusted.py`, and a finalization type-checking branch. Both dataclasses describe the exact capability graph assembled by `TrustedCapabilityFactory`; neither is persisted independently.
- `projections/query_service.py` has one production consumer, the Run API. Its three functions are field-preserving read-and-project wrappers over `run_view.py` and `RunUnitOfWork`.
- Canonical Loop ports, permission/effect policy, approval integrity, Unit of Work, recovery behavior, plugin runtime, and external adapters remain explicit boundaries and are excluded from merging.

## Final result

| Metric | Before | After | Net |
| --- | ---: | ---: | ---: |
| Production lines | 60,619 | 60,580 | -39 |
| Modules | 302 | 299 | -3 |
| Classes | 762 | 762 | 0 |
| Public symbols | 1,189 | 1,187 | -2 |
| Functions and methods | 2,425 | 2,425 | 0 |

The final sequences are `run_execution -> standard -> standard_checkpoint`,
`run_execution -> trusted -> trusted_factory -> trusted_capabilities`, and
`runs API -> run_view -> RunUnitOfWork`. Each removes one non-policy module hop.
