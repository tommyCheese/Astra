# Cleanup baseline

Recorded before implementation with `backend/scripts/analyze_backend_architecture.py`.

| Metric | Baseline |
| --- | ---: |
| Production lines | 61,167 |
| Production modules | 302 |
| Classes | 764 |
| Functions/methods | 2,461 |
| Public symbols | 1,190 |

`backend/scripts/check_backend_architecture.py` passed before implementation.

## Final metrics

| Metric | Baseline | Final | Change |
| --- | ---: | ---: | ---: |
| Production lines | 61,167 | 60,619 | -548 |
| Production modules | 302 | 302 | 0 |
| Classes | 764 | 762 | -2 |
| Functions/methods | 2,461 | 2,425 | -36 |
| Public symbols | 1,190 | 1,189 | -1 |

The final architecture check passes. All moved concepts have one canonical import path; no forwarding modules remain at the old paths.
