# Notifications Platform — Design

Status: **Approved design** (2026-07-17). Home of the canonical base; consumed by all apps
(jg-ferien, cockpit, …). Phased P1 → P2a/b/c → P3.

## Principle

Decouple **Message** (what/why) from **Delivery** (how). One authoring point → a **Router**
resolves the channel set → per-channel **dispatchers** deliver → **one canonical status**, every
surface is a **projection** of it. Preferences are a **category × channel** matrix. `base-owns-all`
is reached by **relocate + generalize** (not rewrite). The type registry is **code-first**
(checked-in, reviewable — no per-environment DB drift).

## Locked decisions (must hold before P2)

### D1 — Two production modes, one canonical status
jg's task engine is deliberately **stateless-derived** (no persisted task model; todos are computed
live from domain state, only dismissals/overrides/sent-log are overlay tables). Its strongest
property is **self-healing**: "payment due" disappears the moment payment lands, with no lifecycle
to maintain. The base preserves this by supporting **two modes** under one status model:

- **(a) Event-authored** — `notify(...)` creates the canonical `Notification` eagerly; transports
  fire immediately. Needs an explicit **resolver hook or expiry** to close it when the underlying
  state resolves.
- **(b) Provider-derived** — providers derive todos **live** from domain state (stateless,
  self-healing); only **status overlays** (dismissed / done / delivery-log) persist. The canonical
  status is **materialized lazily**, keyed on `dedup_key = (type, notifiable, recipient)`. Transports
  for derived todos **piggyback the daily window-scan** (that is their "push moment"); the surface
  itself derives live.

Per-type **`resolution` semantics** live in the registry: `user-done | state-resolved | expired`.
Coupling to mode: `state-resolved` is **automatic** for derived todos (provider stops emitting =
self-heal); event-authored types must declare a resolver or an expiry. **The canonical status is
still one thing** — it hangs on `(type, notifiable, recipient)` regardless of mode.

### D2 — Router precedence
```
effective_channels = eligible ∩ (override ?? default) ∩ prefs
```
A per-message `override` **cannot** overturn a user opt-out (else prefs are decoration). A separate
**`force`** path exists **only** for types registered `critical` — that is the legitimate use of
urgency in the router. `force` means "ignore the category opt-out", **not** "bypass channel
availability" (no push to a user without a subscription). The **rollout default matrix = opt-out**
(matches today's behaviour) so nothing silently stops delivering on publish day.

## Ownership (what goes where)

### dcm (`django-core-micha`) — the core
- `Notification` (canonical): `type`, `category`, `urgency`, content (i18n key + params + link),
  **`notifiable`** (GenericFK content_type+object_id, **indexed**), **`dedup_key`** (first-class),
  `created_at`, scope. Retention/TTL from day one.
- `NotificationRecipient`: per-recipient `seen / dismissed / done` + timestamps (the projection source).
- `NotificationDelivery` **(new)**: per-`(recipient, channel)` delivery record — `sent_at`, status,
  retries, digest-threshold. **jg's `TaskReminderSent` is absorbed here** (no parallel dedup).
- `Router` / dispatch service (D2).
- Channel dispatchers (interface `deliver(notification, recipient, ctx)`): **Email, Web-Push, Chip
  exist** (`delivery.py`); **Todo** (P2, relocated engine); **Popup** (P3).
- `NotificationPreference` — **EXISTS** (`models.py`, "Per-user delivery-channel consent"). Extend
  with the **`category`** dimension + migrate existing rows. **Not greenfield** — a schema change on
  a model in production (cockpit) → additive, approval-gated.
- Provider / type **registry** (code-first): apps register domain providers + per-type policy.
- Relocated task-engine core (windowing `shouldIncludeTask`, dismissal, override, digest scaffold),
  generalized off jg's `Event` FK onto the generic `notifiable` ref.
- Generic **window-scan / digest management command** (see Infra).

### ucm (`ui-core-micha`) — the surface renderers
- Notifications context (WS / list / unread) — **single WS owner** invariant (below). Extend.
- Chip / bell surface — exists.
- Generic **Todo renderer** (dashboard/list) — reads the todo projection; app supplies i18n keys.
- **Popup** = reuse the `OnboardingProvider` **wizard renderer only** — NOT its sequential
  onboarding-progress store (D-F7): popup seen-status lives on `NotificationRecipient`, transient
  per message.
- **Preferences UI** (category × channel matrix) — new; may lag P1.

### Apps (jg-ferien, cockpit, …) — consumers
- Register domain **providers** (jg: `payment`, `cook_fill`, `packing`, `travel_info`, … as plugins
  into the todo channel) + per-type policy.
- Author messages via `notify(...)`.
- Supply app-specific **i18n content** (keys + translations).
- Do **not** reimplement transports/surfaces.

## What relocates (relocate + generalize, not rewrite)
| Today | Target | Effort |
|---|---|---|
| jg task-engine **logic** | → dcm todo channel | cleanly movable |
| jg task **providers** | stay in jg (registered plugins) | small |
| jg `ReminderDismissal` / `EventTaskOverride` / `TaskReminderSent` (bound to `Event` FK + kinds) | → dcm, **generalized** to `notifiable` + type-key; sent-log → `NotificationDelivery` | **the hard part** (model refactor + data migration) |
| ucm onboarding wizard | = popup channel (renderer reuse) | hook only |
| dcm `Notification` + `deliver_push_email` + `NotificationPreference` + `PushSubscription` + S112 consumer | = transports + chip + prefs under the router | formalize/extend (all EXIST) |

## Type registry (code-first)
Checked-in policy per type (reviewable, no DB drift):
```yaml
payment_due:
  category: finance
  mode: derived            # derived | event
  resolution: state-resolved   # user-done | state-resolved | expired
  default_channels:  [todo, push, email]
  eligible_channels: [todo, push, email, chip]   # popup NOT allowed for this type
  persistUntilDone: true
  window: { base: zahlungsfrist, remindBefore: P7D }
  critical: false
```

## A typical message (target state)
```python
notify(
    type="payment_due",              # stable key; policy/defaults live with the type
    recipients=registration.user,
    category="finance",
    content={
        "title_key": "Notif.Payment.TITLE",
        "body_key":  "Notif.Payment.BODY",
        "params":    {"amount": "450 CHF", "due": "2026-07-17"},
        "link":      {"kind": "event-section", "event": event.id, "section": "finance"},
    },
    notifiable=registration,         # generic ref (replaces Event FK)
    channels=None,                   # None => Router decides; or override e.g. ["todo","push"]
)
```
1. dcm upserts ONE `Notification` (+ `NotificationRecipient`) by `dedup_key`.
2. Router: `default_channels` ∩ prefs(finance: push on, email off) ⊕ override → `{todo, push}`.
3. Dispatchers fire; a `NotificationDelivery` row per (recipient, channel).
4. User sees a dashboard **todo** AND a **push**; tapping the todo sets `done` on the ONE status →
   push/chip/popup projections clear.
5. Prefs: user turns finance→push off → next time todo only. Derived types self-heal (payment lands
   → provider stops emitting → `state-resolved`).

## Infra
- **Daily window-scan / digest** = a generic dcm **management command**; apps declare it in
  `project.yaml infra.scheduled_commands` (reuses **CI-3**, the same mechanism TE-3 uses). One scan
  drives all derived-todo windows + digests.
- **WS**: extended/new consumers stay **S112** (`BaseSecureConsumer` + inventory test). All surfaces
  share **one** WS connection via `NotificationsContext` — **single-owner invariant** (no second
  socket; pubsub-history lesson).
- **Retention / TTL janitor** for notifications from P1.
- GenericFK costs: index `(content_type, object_id)`; **orphan cleanup** via signal/janitor on
  domain-object delete (no FK constraint); **GenericPrefetch** in list endpoints.

## Chip vs Messaging
jg messaging (user↔user chat) **stays its own domain**. Only the **bell badge aggregation** is
unified. Out of scope for the notifications base.

## Phases
- **P1** — canonical `Notification` + `NotificationRecipient` + `NotificationDelivery` + Router +
  category×channel prefs (extend existing model + migrate) + formalize existing transports/chip +
  switch chip/bell to the canonical API + retention janitor. Prefs-UI may lag. → **fixes the acute
  cross-surface nag now.**
- **P2 (expand-contract)** —
  - **P2a**: dcm lands the relocated+generalized task engine (todo channel) + generic models —
    additive, defaults, alongside jg's old path.
  - **P2b**: jg adopts, registers providers, migrates data **while the old path still runs**.
  - **P2c**: jg's old task models/engine removed **only after** P2b is verified. **No in-place rename.**
- **P3** — popup channel via the ucm wizard renderer. Uncritical.

## Rollout discipline
Every phase = a release train **dcm → ucm → app pin-bumps**, with a **registry live-check before
pinning** (dcm/ucm publish from main, no staging). `AbstractNotification` changes are **additive
with defaults only** — each consumer app gets a migration on pin-bump, planned per app; hram
(dcm 2.19) and cockpit (v0.7.0 prod) must be unaffected until they opt in. Schema migrations are
approval-gated.

## Gates
D1 + D2 are decided (above) — the two places the earlier draft described two different systems at
once. They must hold before P2 begins.

---

# Addendum — concrete execution plan (2026-07-18)

Extends the approved design above with: a third locked decision (D3), the verified jg ground truth
(measurement 2026-07-18), the pre-P2 paper-test gate, and the work-order breakdown. The core design
above is unchanged.

## D3 — Canonical model is concrete in dcm (retire swappable)  [RATIFIED 2026-07-18]

The relocation table above says the dcm `Notification` "EXISTS — formalize/extend". Measurement
correction: **the concrete `Notification` does NOT exist in dcm.** dcm ships only `AbstractNotification`
(`notifications/models.py`, `abstract = True`, swappable via `NOTIFICATION_MODEL`); the concrete table
lives in the **consumer** — cockpit's `notify.Notification` (`class Notification(AbstractNotification):
pass`, empty subclass — the swappable flexibility is provably unused).

**Decision: make the canonical `Notification`/`Recipient`/`Delivery` models concrete in dcm; retire the
swappable `AbstractNotification` pattern.** Rationale:
- The extension seam is unused (empty subclass, verified).
- dcm **already ships migrations** for its concrete models (`notifications/0001_initial` creates
  `NotificationPreference` + `PushSubscription` under `app_label = django_core_micha_notifications`).
  `Notification` is the lone exception *because* it is abstract → today a field-add is a
  `makemigrations` fanout into every consumer.
- After D3 the schema **authorship** centralizes: one reviewed dcm migration file per change, instead
  of N app-authored ones. **Application stays per-app** (each consumer bumps the pin and runs `migrate`
  on deploy — D3 removes the fanout, not the migrate-on-deploy step).

Cost: a one-time cockpit **cross-app table move** (`notify.Notification` →
`django_core_micha_notifications.Notification`) — data migration + drop of the old table, expand-contract,
with data preservation. This is P1's cockpit step (below), not a field-add.

*(This is the decision referred to as "D1 (swappable exit)" in planning discussion; renamed D3 here to
avoid colliding with the design's existing D1.)*

## Verified ground truth (jg measurement 2026-07-18) — corrections to the relocation table

Two rows above are over-optimistic; the measurement sharpens them:

- **"jg task-engine logic → cleanly movable"** — mostly true, but three latent debts must be paid
  *during* generalization, not assumed away:
  1. **Three divergent "task kind" vocabularies** coexist: `ReminderDismissal.KIND_CHOICES` (9, incl. a
     dead `registration` kind), `EventTaskOverride.TASK_KEY_CHOICES` (8), and `TaskReminderSent.task_key`
     (~10 in practice — adds `checklist`, `registration_incomplete`, which are in neither enum). The base
     must reconcile these into **one taxonomy** before they can share a type-key.
  2. **`leadAdjustable` set is triplicated** (model constant `LEAD_ADJUSTABLE_TASK_KEYS` + `TASK_CONFIG`
     inline flags + serializer validation) — collapse to one source on relocation.
  3. **Correction to the earlier "two out-of-band providers" claim:** only `build_checklist_tasks` truly
     bypasses `materialize_task` (hardcodes `severity="medium"` inline, no window-gate, no override hook).
     `_registration_incomplete_items` **runs through** `materialize_task` (own standalone config), bypassing
     only PROVIDERS dispatch + overrides. **P2-pre normalization applies to `checklist` only.**

- **"`ReminderDismissal` … → the hard part"** — confirmed, and harder than stated. `ref_id` is a
  `CharField(64)` freetext with **four target model types + one prefix-less outlier**:
  `"{kind}:{event.id}"` (5 kinds) · `"cook_fill:{Meal.id}"` · `"program_signup:{TimeBlock.id}"` ·
  **`duty` → bare `{DutyAssignment.id}`, no kind prefix**. Plus: dead `registration` kind, and
  `profile_complete` removed in migration `0055` → **audit for orphaned rows** before relocation
  (`ReminderDismissal.filter(kind="profile_complete")`). The reparse migration ships with a **documented
  loss-tolerance** (unparseable → expire; dismissals are low-value). `EventTaskOverride` and
  `TaskReminderSent` are **clean** (real `Event` FK + closed enum) → FK→notifiable schema change only,
  no freetext reparse.

- **jg is already a dcm-notifications consumer, NOT greenfield:** no `backend/notifications/`, no
  `NOTIFICATION_MODEL` set, no jg-local WS consumer (asgi mounts dcm's `NotificationConsumer` directly).
  `messaging/.../reset_shared_notif_tables.py` proves jg already migrated OFF local notif/onboarding apps
  ONTO the shared dcm tables. The digest calls `deliver_push_email` (email+push, no WS). **Only the
  frontend `NotificationsContext.jsx` is jg-local** (WS-owner routing `message` → MessagingContext; no
  bell/inbox) → the P1c ucm context is backend-side already dcm-owned; only the frontend surface is local.

- **Encouraging for hram/spesix:** the engine **already supports state-only tasks** (`due=None` → always
  shown) and `alwaysVisible`. hram/spesix "run finished / build failed" types (no due date) fit today
  without touching the windowing machinery — a strong signal the abstraction carries.

## Gate G-P2 — paper-test before any P2 code

Before cutting P2a work orders: take **one concrete hram type and one spesix type** and run each through
the type registry on paper — classify as (a) **state-only** (trivial: `due=None`, resolution
`state-resolved`), (b) **windowed** (resolves a `due` base from its `notifiable` — standard path), or
(c) **neither** (needs an expression the language can't produce). Only case (c) breaks "abstract enough";
found on paper in ~1h, it is far cheaper than mid-migration. **P2a does not start until this passes for
3 real todo shapes (jg + hram + spesix).**

**Working input (2026-07-18):** the operator characterizes both hram and spesix as **state-only
"job done"** types (e.g. "engine run finished", "build failed" — no due date). That is case (a), which
the engine **already supports** (`due=None` → always shown, `resolution: state-resolved`) → paper-test
risk is **low** and the abstraction is expected to carry. G-P2 stays a real gate: confirm with the
concrete hram/spesix types before P2a, but it is not expected to block.

**RESOLVED 2026-07-27:** G-P2 is moot AS A 3-SHAPE TEST — hram/spesix are P1 `notify()` (state-only)
consumers, NOT todo consumers, so they contribute no todo shapes to validate against. jg is the SOLE
todo consumer; **NOTIF-P2-pre (a1eaac9) normalized jg's providers into ONE clean config/materialize
interface** — that normalized shape IS the validated interface NOTIF-8 lifts. NOTIF-8 therefore
proceeds CONSCIOUSLY from a single real shape, with the YAGNI guardrail (generalize only to jg's needs,
not speculatively) as the accepted mitigation of the rule-of-three risk (operator-approved). Gate
cleared — NOTIF-8 unblocked.

## Work orders & sequencing

Register prefix **`NOTIF-*`** (dcm register); app-side WOs live in their own repo registers. Each phase is
a release train **dcm → ucm → app pin-bumps** (publish-from-main, no staging → registry live-check before
pinning). `[approval]` = schema migration on a production model, approval-gated.

Re-sequenced 2026-07-18 (ucm measurement): the canonical READ side (inbox/unread/mark over
`NotificationRecipient` + WS status-change broadcast) was never a WO — the existing inbox views serve
the retiring swappable model (`get_notification_model`, `read_at`, direct user FK), NOT the canonical
`Notification`/`NotificationRecipient` split. It is a prerequisite for BOTH the ucm surface and the
cockpit migration, so it is interposed as the new **NOTIF-5**; the former ucm/cockpit WOs shift to
NOTIF-6/7 and P2/P3 shift +1. NOTIF-1..4 (done) are unchanged.

**P1 — canonical core (fixes the cross-surface nag; no jg task-engine work yet)**
| WO | Repo | Scope | Depends on | Gate |
|---|---|---|---|---|
| NOTIF-1 | dcm | Concrete `Notification` + `NotificationRecipient` + `NotificationDelivery` (dedup_key, notifiable GenericFK, `seen_at`/`dismissed_at`/`done_at`). Additive. | D3 ratified | ✓ landed 1044f70 |
| NOTIF-2 | dcm | Router (D2) + `notify()` + code-first type-registry loader | NOTIF-1 | ✓ landed 1e7a0dd |
| NOTIF-3 | dcm | Category×channel prefs: `NotificationChannelDefault` + `NotificationCategoryChannelPreference` (defaults + per-category overrides). **NO seed** — `is_channel_enabled` falls back to the LIVE legacy `email_opt_in`/`push_opt_in` (tier 3), preserving today with no staleness. `[approval]` | NOTIF-1 | ✓ landed c1797a0 |
| NOTIF-4 | dcm | Formal dispatchers (Email/Push/Chip + todo/popup stubs), R2 delivery-race fix, retention janitor | NOTIF-2 | ✓ landed b7c97d6, published 2.27.0 |
| NOTIF-5 | dcm | **Canonical READ API:** serializer for `NotificationRecipient`+content (no content-dereference on `notifiable`); `feed/` (list, paginated, self-scoped, `?status=unseen\|active\|done`), `feed/unread-count/` (`seen_at IS NULL AND dismissed_at IS NULL` — a dismissed-but-unseen item must not inflate the badge), `feed/mark/` (seen/dismissed/**done** — the one status all surfaces project; IDOR-safe, self-scoped). Routes are named `feed/*`, NOT `inbox/*` — the legacy `inbox/*` paths stay live and mapped to the OLD swappable-model views (`get_notification_model`) until NOTIF-7 retires them; `inbox/*` was unavailable to reuse. **WS status-change broadcast** (`{"type": "notification.status", "notification_id", "status": {"seen","dismissed","done"}}`, one per distinct affected notification, reflecting the full post-update tri-state) so a mark on one surface clears others live — the actual cross-surface-nag fix. **No migration** (recipient status fields already exist). | NOTIF-1..4 | ✓ landed abe576d, published 2.28.0 |
| NOTIF-6 | ucm | `NotificationsProvider`/`useNotifications` single-owner (WS + feed + unread) reading the NOTIF-5 canonical `feed/*` API + generic `NotificationBell`; additive exports (no breaking change). Prefs-UI may lag | NOTIF-5 | ✓ landed 6c63fb9, published ucm 2.11.0 |
| NOTIF-7 | cockpit | **Swappable-exit + cutover** (staged, 4 sub-stages: pin dcm 2.28.0/ucm 2.11.0 + regression → status-stream remodel to canonical event-authored `notify()` with i18n keys + `notifiable` + recovery resolver → frontend cutover to ucm `NotificationsProvider`/`NotificationBell` on `feed/*` → retire old inbox + `NOTIFICATION_MODEL`). **NO historical data migration** — existing `notify.Notification` rows are transient status events (operator: not relevant), discarded with the table at retirement; content mismatch (pre-rendered strings vs i18n keys) is thereby moot, going-forward events use proper keys. Staging-verified (cockpit `develop`), operator-gated `develop→main` promotion to prod still pending. `[approval]` | NOTIF-6 | ✓ landed (cockpit repo: 8119695/c08db3b/bc0c8a7/9f84bec) — P1 done |

**Gate G-P2 (paper-test)** — must pass before the P2 rows below are cut.

**P2 — task engine relocation (expand-contract; only after G-P2)**
| WO | Repo | Scope | Depends on |
|---|---|---|---|
| NOTIF-P2-pre | jg | Normalize `build_checklist_tasks` onto the config/materialize path; collapse the triplicated `leadAdjustable` set to one source; audit/clean `profile_complete` orphan rows | G-P2 | ✓ landed jg repo: a1eaac9 |
| NOTIF-8 | dcm | Land relocated+generalized engine (todo channel): windowing/dismissal/override/digest on generic `notifiable`+type-key; reconcile the 3 kind-vocabularies into one taxonomy; absorb `TaskReminderSent` into `NotificationDelivery` | G-P2 | ✓ landed |
| NOTIF-9 | jg | Adopt: register jg providers as plugins; **data-migrate** overlays (ref_id 4-type reparse with documented loss-tolerance; clean FK moves for override/sent) **while old path still runs** (P2b) — ✓ done bb0580b | NOTIF-8, NOTIF-P2-pre |
| NOTIF-10 | jg | **Read/write cutover** to canonical-only (derive/digest reads + 4 live endpoints; retire dual-write shims); tables stay (P2c-1). RE-SCOPED from "remove old engine" (unsafe — tables still live-queried) — see [notifications-messaging-roadmap.md](./notifications-messaging-roadmap.md) §4 | NOTIF-9 |
| NOTIF-11 | jg | **Drop** the 3 legacy overlay tables (guarded, one-way) after NOTIF-10 verified (P2c-2) `[approval]` | NOTIF-10 |

## Todo channel

NOTIF-8 provides the provider-derived todo channel. A consuming app registers one
`TodoTypeConfig` and seed provider with `register_todo_provider()` and separately
registers the matching `NotificationType` with `mode="provider"` and category
`"todo"`. The provider receives `(user, now)` and yields `TodoSeed` instances. A
seed's `notifiable` identifies one concrete occurrence for canonical notification
deduplication, while its optional `scope` identifies the (often broader) object to
which a `TodoOverride` applies; `scope` defaults to `notifiable`. A provider that
ever emits more than one seed of the same `type_key` with `notifiable=None` for one
user will collapse into a single `Notification` row (dedup keys only on type +
notifiable) — pass a real `notifiable` whenever a type can recur per user.

Call `sync_todos_for_user(user, now=None)` wherever a consumer needs a current todo
projection. It materializes only currently visible seeds into canonical
`Notification`/`NotificationRecipient` rows, honours `TodoOverride`, and never
resurfaces recipient rows already marked dismissed or done through `feed/mark/`.
`Notification.content` always carries the fully materialized payload (provider
content plus current `due`/`severity`) — kept fresh on every sync, since
`get_or_create_by_dedup` only applies its `content` default on first creation — so
`feed/` and every dispatcher see current values with no todo-channel awareness.
Providers that want digest scanning additionally supply `candidate_users_fn`; the
`send_todo_digests` command uses those candidates and only emails users actually
opted into the email channel. It claims each threshold as `pending` before sending
(the DB's partial unique constraint on `NotificationDelivery` is the real
concurrency guard, so concurrent runs cannot double-send), then flips it to `sent`
or `failed` after the real send attempt — mirroring `notify()`'s own
pending-then-resolved delivery pattern, so a send failure stays distinguishable
from a real success instead of being recorded as delivered either way.

**P3 — popup channel** (uncritical): NOTIF-12 (ucm, was NOTIF-11 — renumbered for the jg cutover/drop
split) hook the wizard renderer as the popup channel; seen-status on `NotificationRecipient`, not the
onboarding-progress store.

hram/spesix consume from P1 onward as their **first** notification implementation (they never diverge —
the reason for building the contract now).
