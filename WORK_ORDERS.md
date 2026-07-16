# WORK_ORDERS.md — django-core-micha

Work-order register for this repo. Lightweight directory (not the full orders):
one row per WO with its implementation status. Convention, schema, and maintenance
rules are defined centrally in `webapps/AGENTS.md` → "Work-Order Register".

**Scope note:** this register starts with the 2026-07 resilience workstream;
older work is in `git log`.

## Workstream prefixes

| Prefix | Workstream |
|---|---|
| `RES-*` | Runtime/network resilience (healthz bounding, retry on external calls) |

Introduce a new prefix when none fits and add it here. New WOs always get a
prefixed ID; never reuse a bare flat number across workstreams.

## Register

| ID | Titel | Beschreibung | Datum | Status | Commit(s) | Notiz |
|---|---|---|---|---|---|---|
| RES-1 | Bounded healthz + sync-secrets push retry | Per-check wall-clock timeout in `/api/healthz` (fast 503 instead of gateway 504 on dependency stalls); Redis socket timeouts + Postgres connect_timeout + TCP keepalives in settings_base; retry + failure aggregation (non-zero exit) around the `gh secret set` push loop | 2026-07-16 | done | 3e8f01f | v2.25.0 → 2.26.0 (publish-from-main). Reviewed independently (2 rounds): round 1 confirmed a real gap — per-request `ThreadPoolExecutor` workers are non-daemon and get joined by `concurrent.futures.thread`'s process-wide shutdown hook regardless of `shutdown(wait=False, ...)`, so a sustained outage leaks threads that block graceful restarts (contradicted the original "no accumulation" claim). Resolved per operator decision: kept the design (no shared pool, no daemon threads), added Postgres TCP keepalives (~60s dead-peer detection) to bound leaked-thread lifetime, corrected risk wording in views.py docstring + CHANGELOG. Round 2: no findings. App pin bumps explicitly OUT of scope (per-app operator decision; every develop push = staging deploy). CHANNEL_LAYERS untouched (pubsub-layer history); no app-wide statement_timeout |
