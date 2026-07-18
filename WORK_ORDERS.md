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
| `NOTIF-*` | Shared notifications platform (canonical model, router, category×channel prefs, todo channel) |

Introduce a new prefix when none fits and add it here. New WOs always get a
prefixed ID; never reuse a bare flat number across workstreams.

## Register

| ID | Titel | Beschreibung | Datum | Status | Commit(s) | Notiz |
|---|---|---|---|---|---|---|
| RES-1 | Bounded healthz + sync-secrets push retry | Per-check wall-clock timeout in `/api/healthz` (fast 503 instead of gateway 504 on dependency stalls); Redis socket timeouts + Postgres connect_timeout + TCP keepalives in settings_base; retry + failure aggregation (non-zero exit) around the `gh secret set` push loop | 2026-07-16 | done | 3e8f01f | v2.25.0 → 2.26.0 (publish-from-main). Reviewed independently (2 rounds): round 1 confirmed a real gap — per-request `ThreadPoolExecutor` workers are non-daemon and get joined by `concurrent.futures.thread`'s process-wide shutdown hook regardless of `shutdown(wait=False, ...)`, so a sustained outage leaks threads that block graceful restarts (contradicted the original "no accumulation" claim). Resolved per operator decision: kept the design (no shared pool, no daemon threads), added Postgres TCP keepalives (~60s dead-peer detection) to bound leaked-thread lifetime, corrected risk wording in views.py docstring + CHANGELOG. Round 2: no findings. App pin bumps explicitly OUT of scope (per-app operator decision; every develop push = staging deploy). CHANNEL_LAYERS untouched (pubsub-layer history); no app-wide statement_timeout |
| NOTIF-1 | Canonical Notification models | Concrete `Notification` + `NotificationRecipient` + `NotificationDelivery` in dcm (dedup_key first-class, notifiable GenericFK indexed, retention fields); additive new tables, existing consumers untouched `[approval schema]` | 2026-07-18 | planned | — | D3 ratified 2026-07-18 (swappable exit). First P1 WO. Full spec: `docs/design/notifications-platform.md` addendum |
| NOTIF-2 | Router + notify() + type registry | Router (D2 precedence `eligible ∩ (override ?? default) ∩ prefs` + `force` for critical) + `notify()` authoring API + code-first type-registry loader | 2026-07-18 | planned | — | Depends NOTIF-1. Registry/contract shape benefits from the G-P2 paper-test (hram/spesix state-only) running in parallel before this locks |
| NOTIF-3 | Category×channel prefs | Extend `NotificationPreference` with `category` (through-table user×category×channel) + seed migration from existing email/push booleans (opt-out default = today's behaviour) `[approval schema]` | 2026-07-18 | planned | — | Depends NOTIF-1. Prod model (cockpit) — data-preserving seed |
| NOTIF-4 | Dispatchers + retention janitor | Formalize Email/Web-Push/Chip as router dispatchers (`deliver_push_email` → dispatcher); retention/TTL janitor as a `scheduled_command` (CI-3 mechanism) | 2026-07-18 | planned | — | Depends NOTIF-2. Closes P1 dcm side → dcm release |
| NOTIF-5 | ucm context + chip/bell | `NotificationsContext` single-owner + chip/bell on canonical API; Prefs-UI may lag (ui-core-micha) | 2026-07-18 | planned | — | Depends dcm release (NOTIF-4). ucm repo |
| NOTIF-6 | cockpit swappable-exit + status remodel | cockpit `notify.Notification` → dcm canonical (cross-app table move, expand-contract, data-preserving) + status-stream → event-authored types with resolver + pin bumps `[approval schema]` | 2026-07-18 | planned | — | Depends NOTIF-5. cockpit repo. Closes P1 |
| NOTIF-P2-pre | jg | Normalize `build_checklist_tasks` onto config/materialize path; collapse triplicated `leadAdjustable` set to one source; audit/clean `profile_complete` orphan rows | 2026-07-18 | planned | — | jg repo. Gated on G-P2 paper-test |
| NOTIF-7 | Relocated todo engine | Land relocated+generalized engine (todo channel) on generic `notifiable`+type-key; reconcile 3 kind-vocabularies into one taxonomy; absorb `TaskReminderSent` into `NotificationDelivery` | 2026-07-18 | planned | — | P2a. Gated on G-P2. Depends NOTIF-1..4 |
| NOTIF-8 | jg adopt + data-migrate | jg registers providers as plugins; data-migrate overlays (ref_id 4-type reparse + duty prefix-less outlier, documented loss-tolerance; clean FK moves for override/sent) while old path runs | 2026-07-18 | planned | — | P2b. jg repo. Depends NOTIF-7 + NOTIF-P2-pre |
| NOTIF-9 | jg remove old engine | Remove jg's old task models/engine only after NOTIF-8 verified (no in-place rename) | 2026-07-18 | planned | — | P2c. jg repo. Depends NOTIF-8 |
| NOTIF-10 | Popup channel | Hook ucm wizard renderer as popup channel; seen-status on `NotificationRecipient`, not onboarding-progress store | 2026-07-18 | planned | — | P3, uncritical. ucm repo |
