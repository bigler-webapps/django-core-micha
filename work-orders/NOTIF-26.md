# WORK ORDER NOTIF-26 (django-core-micha + ui-core-micha) — active/passive delivery model

**EXECUTION DIRECTIVE.** If you are the implementer reading this work order as your own
specification: this section is NOT addressed to you. It tells the Orchestrator how to invoke you.
You ARE that invocation — do NOT shell out to `codex exec`.

Implement through `codex exec` in the background — invoked **directly via Bash** (never the
`debugger`/`*_coder` Agent wrappers) with **both** flags `--skip-git-repo-check` and
`--dangerously-bypass-approvals-and-sandbox`, prompt passed as a positional argument from this
file. Fall back to direct Claude implementation only on Codex quota / rate-limit / non-zero exit.

**Envelope authored by the Expertenchat 2026-08-06. Part B (implementation map) is filled by the
Orchestrator on `git pull`.**

## GOAL

Let an app declare **what a notification is for** instead of **how it is transported**. Today every
consumer enumerates transport names (`default_channels=["chip","email"]`,
`eligible_channels=[...]`) in its type registration. Replace that with a semantic **reach**
declaration on `NotificationType`; the concrete active channel stays the **user's** choice, resolved
by the existing preference tiers.

Expected outcome: a consumer registration states reach, never a channel list; adding a future active
channel (SMS, Slack, …) requires no change to any app's type registration.

## WHY

Both live consumers already make this semantic decision — the long way round:

- `jg-ferien/backend/messaging/test_platform_flow_smoke.py:265-269` asserts its messaging type is
  `default_channels == ["email","push"]` with `"chip" not in eligible_channels`. That is literally
  "reach me, do not merely show me" written as transport.
- `cockpit/backend/notify/apps.py:96` lists `eligible_channels=["chip","push","email"]` uniformly
  for every type and varies only the defaults.

The estate is at the last cheap moment to fix this: two apps, ~4 files. Every further adoption
(hram NOTIF-16, spesix NOTIF-17) cements the transport-enumeration model instead.

## SCOPE

**A. A reach declaration on `NotificationType`** (`src/django_core_micha/notifications/types.py`).
It is **not a boolean** — jg's case is *active without passive* (no bell entry at all), so the model
must express at least: passive only · active only · both. Name and shape are the implementer's
call within this envelope; the semantics are fixed here:

- **passive** = in-app surfaces the user only sees when present (chip / popup).
- **active** = surfaces that reach out and interrupt (email / push).
- The app never names a concrete active channel. Which active channel fires is resolved per user
  from the existing preference tiers (`prefs.py:9-22`) and technical availability
  (`router.py:6` `_is_technically_available`).

**B. Reconcile with `feed_visible`.** "Passive" and `feed_visible` (NOTIF-19,
`views.py:160,210` via `iter_feed_hidden_type_keys()`) overlap: both govern whether a notification
is visible when the user looks. Two independent ways to say the same thing will contradict each
other. Decide ONE relationship and document it in the module docstring — either `feed_visible`
becomes derived from reach, or it is explicitly narrowed to a distinct question and the difference
is stated. Do not leave both free-floating.

**C. Fallback when no active channel is available.** Operator decision 2026-08-06: a type that
declares active, for a user with no usable active channel (no email address, no `PushSubscription`,
or every active channel opted out), **degrades to passive**. It must not silently vanish. Surface
the "no active channel configured" state in the ucm settings component so the user can see that an
active-reach notification will only ever reach them passively.

**D. Migrate both existing consumers** — cockpit (`backend/notify/apps.py:88-96` + its assertions in
`backend/tests/test_notifications.py:77-94`) and jg-ferien (`backend/events/`, plus
`backend/messaging/test_platform_flow_smoke.py:265-269`). **Behaviour parity is the bar**: after the
migration each existing type must deliver to exactly the same channels for the same user state as
before. jg's chip exclusion must survive as "active only".

**E. ucm side** — the settings surface must express the axis in user terms ("how should this reach
me") rather than listing transports, and show the C fallback state. Cross-repo per
`webapp-management/SHARED_CAPABILITIES.md`: dcm payload/registry and ucm rendering change together.

## NON-GOALS / DO NOT TOUCH

- **The todo channel is out of the axis.** Todos are provider-mode (`mode="provider"`), derived live
  from app state (`notifications/todo/registry.py`), merged into the feed by the view, and their
  `TodoDispatcher` is a no-op stub. They carry their own lifecycle (`due`, `remind_before`,
  `persist_until_done`, `always_visible`, self-heal). Reach does not apply to them; say so
  explicitly in the docstring rather than leaving it ambiguous.
- **`urgency` stays as it is.** It is stored (`models.py:172`), serialized to the client
  (`serializers.py:38`) and consumed by nothing. Tempting to repurpose — do not. It is per-call on
  the `Notification` with stored history; reach is per-type. Cleaning it up is a separate WO.
- No new transport channels. No change to the dispatchers themselves.
- No change to `notify()`'s signature beyond what the axis strictly requires. The per-call
  `channels=` override stays.
- Not the subscriber resolver (that is NOTIF-27) and not any app adoption (NOTIF-16/17).
- No `develop → main` promotion of either repo; no consumer pin bumps here (that is NOTIF-28).

## TIER

**Tier 2 — shared core, two repos, two live consumers.** Independent `reviewer` mandatory;
`ui_reviewer` mandatory for the ucm diff, spawned concurrently. No schema change is expected (the
registry is in-memory); if the chosen shape needs a migration, that is `[approval schema]` and stops
for the operator first.

## RISKS

- **Silent delivery drift on migration.** The failure mode is a type that quietly gains or loses a
  channel for some user state. This is why parity tests (below) are the gate, not the build.
- **Two overlapping concepts** if scope B is skipped or half-done — the most likely way this WO
  leaves the codebase worse than it found it.
- jg is the estate's only todo provider and its messaging types are the most channel-specific in the
  estate; it is the consumer most likely to reveal an over-simplified model.
- Publishing both packages makes every later adopter depend on this shape. Getting it wrong is
  expensive to reverse.

## REQUIRED TESTS (write these; the Orchestrator runs them)

Narrow, in both repos — not a full suite:

1. **Reach → channel resolution**, per user-preference state: a type declaring active resolves to
   the user's chosen active channel(s) and to none when the user has opted all of them out.
2. **Active-without-passive** (jg's case): no chip/feed entry is produced for such a type.
3. **Fallback (scope C)**: a user with no usable active channel receives the notification
   passively, and the "no active channel" state is reported to the settings surface.
4. **Parity for both migrated consumers**: for each existing cockpit and jg type, the resolved
   channel set is identical before and after the migration, across at least the default state and
   one explicit opt-in state. This is the regression guard for the whole WO.
5. **`feed_visible` reconciliation** (scope B): one test pinning the decided relationship, so a
   later change cannot silently reintroduce the contradiction.
6. ucm: the settings component renders the axis and the fallback state (component test).

## TARGET REPOS

`C:\Users\biglmi\Documents\webapps\django-core-micha` and
`C:\Users\biglmi\Documents\webapps\ui-core-micha`. Both currently on `main`. Commit to the trunk per
`AGENTS.md` (infra/platform: `develop` if it exists, else `main`). Never the workspace root.

Consumer repos (cockpit, jg-ferien) are touched **only** for the scope-D migration — that migration
lands in those repos on `develop` and needs their own scoped tests green.

## SEQUENCING

NOTIF-26 and NOTIF-27 are independent of each other and may land in either order, but **both must be
published before NOTIF-28** (hram's single pin bump) so hram bumps once rather than three times.
NOTIF-16 consumes the result of all three.

## MINI-HANDOVER

```
Orchestrator: implement work-orders/NOTIF-26.md in django-core-micha (+ the matching ui-core-micha
change, and the cockpit/jg consumer migration in scope D). git pull first, read the WO. The axis is
NOT a boolean (jg is active-without-passive), scope B (feed_visible reconciliation) is not optional,
and parity for the two existing consumers is the gate. Then follow orchestrate-codex.
```
