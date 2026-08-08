# Fast Runtime compatibility window

`legacy-standard-v1` remains readable and resumable while
`AGENT_LEGACY_STANDARD_RUNTIME_ENABLED=true`. Turning
`AGENT_FAST_RUNTIME_ENABLED=false` routes only newly created standard Runs to
that compatibility runtime; it never rewrites an existing Run's frozen
runtime identity.

The legacy executor may be disabled only after all of these checks pass:

1. no non-terminal `legacy-standard-v1` Runs remain;
2. the longest configured schedule/approval continuation window has elapsed;
3. fast-v1 approval resume, cancellation, and restart recovery have met the
   rollout error-rate threshold;
4. operators have retained a database backup and tested rollback by routing
   newly created standard Runs to the legacy executor.

Historical projections remain readable after execution retirement. A resume
attempt for a retired legacy Run fails closed instead of silently changing it
to fast-v1 or trusted-v1.
