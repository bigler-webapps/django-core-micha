# WORK ORDER NOTIF-26 (django-core-micha + ui-core-micha) — reach model + subscriptions

**EXECUTION DIRECTIVE.** If you are the implementer reading this work order as your own
specification: this section is NOT addressed to you. It tells the Orchestrator how to invoke you.
You ARE that invocation — do NOT shell out to `codex exec`.

Implement through `codex exec` in the background — invoked **directly via Bash** (never the
`debugger`/`*_coder` Agent wrappers) with **both** flags `--skip-git-repo-check` and
`--dangerously-bypass-approvals-and-sandbox`, prompt passed as a positional argument from this
file. Fall back to direct Claude implementation only on Codex quota / rate-limit / non-zero exit.

**Envelope authored by the Expertenchat 2026-08-06; revised 2026-08-06 after a plan review.
Part B (implementation map) is filled by the Orchestrator on `git pull`.**

> **Revision note.** This WO originally shipped as two — NOTIF-26 (reach model) and NOTIF-27
> (subscriptions). A plan review found they both reshape the **same** `preferences/` response and
> both rebuild the **same** `NotificationSettings.jsx`, while each claimed to be independent of the
> other. Operator decision 2026-08-06: **merge into one WO.** NOTIF-27 is `dropped` in the register
> and its file is a pointer here. The two axes remain conceptually distinct and are kept as separate
> scope blocks; they are implemented and reviewed as one diff because they share two surfaces.

## GOAL

Two changes to the notifications platform that touch the same surfaces:

1. **Reach, not transport.** An app declares *what a notification is for* — must it reach the user,
   or may it wait until they look — instead of enumerating channel names. Which active channel
   fires stays the **user's** choice, resolved by the existing preference tiers.
2. **Recipients for unowned events.** Today `notify()` requires an explicit `recipients` list
   (`notifications/api.py:68`), so an event nobody triggered — a nightly job, a CLI-started run —
   has no addressee at all. Resolve recipients from users who **explicitly subscribed** to a
   category.

Expected outcome: a consumer registration states reach and never a channel list; an app can emit
into a category and reach its subscribers; and one settings surface expresses both, driven by data
rather than by per-app props.

## WHY

Both live consumers already make the reach decision — the long way round:

- jg's messaging type is `default_channels == eligible_channels == ["email","push"]` with `chip`
  excluded. That is literally "reach me, do not merely show me", written as transport.
- cockpit lists the same three eligible channels for every type
  (`cockpit/backend/notify/apps.py:96`) and varies only the defaults.

And for subscriptions: hram's long sweeps (NOTIF-16) start both from the web UI (owner known) and
headlessly from `manage.py` (no owner). A per-invocation `--notify-user` flag was considered and
rejected by the operator on 2026-08-06 because it **fails silently** — a forgotten flag produces no
error, only a notification that never arrives, noticed exactly when someone is waiting for it.

Two apps and ~4 registration sites is the cheapest this migration will ever be; every further
adoption (hram NOTIF-16, spesix NOTIF-17) cements the current model.

## SCOPE — AXIS 1: REACH

**A. A reach declaration on `NotificationType`** (`src/django_core_micha/notifications/types.py`).

**It is not a boolean.** All three states occur in the estate today and all three must be
expressible: **passive only** · **active only** (jg's messaging) · **both** (cockpit's status
types). Name and shape are the implementer's call; the semantics are fixed here:

- **passive** = in-app surfaces the user only sees when present.
- **active** = surfaces that reach out and interrupt.
- The app never names a concrete channel, on either side of the axis.

**Passive granularity is deliberately atomic.** `chip` and `popup` are both passive dispatchers;
a type does **not** get to pick between them, because letting it pick would reintroduce exactly the
channel list this WO removes. No currently-registered type uses `popup`, so nothing is lost. State
this in the docstring — a reviewer found it genuinely ambiguous otherwise.

**B. Reconcile with `feed_visible`.** "Passive" and `feed_visible` (NOTIF-19, `views.py:160,210`
via `iter_feed_hidden_type_keys()`) overlap: both govern whether a notification is visible when the
user looks. Two independent ways to say the same thing will contradict each other. Decide ONE
relationship and document it — either `feed_visible` becomes derived from reach, or it is narrowed
to a distinct question and the difference is stated. Do not leave both free-floating.

**C. Fallback when no active channel is available — bounded.** Operator decision 2026-08-06,
refined after review: a type declaring active, for a user with no usable active channel (no email
address, no `PushSubscription`, or all active channels opted out), **degrades to passive ONLY IF
the type declares passive at all.**

For an **active-only** type (jg's messaging) there is nothing to degrade to, and injecting a chip
would break the parity requirement in scope I. Such a notification is then **undeliverable for that
user** — which is already today's behaviour — and the requirement "must not silently vanish" is
satisfied by **visibility, not by delivery**: the ucm settings surface must show that no active
channel is configured, so the user can see why they receive nothing. Do not invent a channel outside
the type's own reach declaration.

## SCOPE — AXIS 2: SUBSCRIPTIONS

**D. A subscription-based recipient resolver**: given a category, return the users who subscribe.

**The hard requirement:** the resolver must key on an **explicitly set** opt-in and must **NOT**
reuse `is_channel_enabled()` (`notifications/prefs.py:9-22`). That function's bottom precedence tier
reads, verbatim: *"email/push default False (opt-in), all other channels (chip/todo/popup) default
True."* A fan-out filtered through it would deliver a **chip to every user in the app on day one**,
with nobody subscribed. Enforce opt-in at recipient resolution: **no explicit subscription → not a
recipient → nothing on any channel.** Put this reasoning in the resolver's docstring; it is exactly
the "inconsistency" a later reader would helpfully "fix" back into a broadcast.

**E. Subscription storage must be distinguishable from a channel override.**
`NotificationCategoryChannelPreference` (`notifications/models.py:65`) currently means *"my channel
override for a category whose notifications I already receive"*. A subscription means *"add me as a
recipient for a category I would otherwise never be addressed in"* — a different consent. A review
flagged that carrying both in one row shape with no distinguishing field is itself a path to the
accidental broadcast test D-2 guards against. Either add an explicit discriminator or use separate
storage; do not overload the existing row silently. If this needs a new model or a field,
that is `[approval schema]` — stop for the operator before writing the migration.

**F. Empty-subscriber guard.** `notify()` authors the `Notification` row **before** the recipient
loop (`api.py:81-88`), so a zero-recipient call leaves an orphan row. Resolve first, return early
when nobody subscribes, do not author the row.

**G. Categories become discoverable data.** The `preferences/` endpoint (`notifications/urls.py:17`,
`NotificationPreferenceView`) reports the categories **this app** has registered (the in-memory type
registry already knows them), each with a label key resolvable through the text registry so it can
be translated.

## SCOPE — SHARED SURFACES

**H. One coherent `NotificationSettings`** (`ui-core-micha/src/notifications/NotificationSettings.jsx`,
today a flat email/push/preview toggle list). It must, in one design: express reach in user terms
("how should this reach me") rather than listing transports; show the scope-C "no active channel
configured" state; and render server-reported categories with a subscription toggle. **No new
props** — the component is Pattern 3 in `webapp-management/SHARED_CAPABILITIES.md` ("ucm owns
everything") precisely because it needs no host knowledge, and a prop carrying "hram has sweeps"
would break that and need another prop for the next app.

**I. Migrate the existing consumers, at behaviour parity.**

- **cockpit**: `backend/notify/apps.py:88-96` plus its assertions in
  `backend/tests/test_notifications.py:77-94`. Six status types, splitting into "both" and
  "passive only".
- **jg's messaging type is NOT registered in jg.** It is registered in **shared dcm code** —
  `src/django_core_micha/messaging/notifications.py:34-38`
  (`register_messaging_notification_type`), called from `jg-ferien/backend/messaging/apps.py:19`
  with no per-app override; jg is currently its only caller. **Do not go looking in
  `jg-ferien/backend/events/`** — an earlier draft of this WO pointed there and it is wrong: that
  directory holds todo-channel code, which this WO explicitly excludes. Changing the shared
  registration affects every future app adopting messaging notifications, which is a wider blast
  radius than "migrate jg" suggests — treat it as shared-core, not app-local.
- Parity is the bar: after migration each existing type resolves to exactly the same channel set for
  the same user state as before. jg's chip exclusion must survive as "active only".

## NON-GOALS / DO NOT TOUCH

- **The todo channel is out of the reach axis.** Todos are provider-mode, derived live from app
  state (`notifications/todo/registry.py`), merged into the feed by the view, and their
  `TodoDispatcher` is a no-op stub; they carry their own lifecycle (`due`, `remind_before`,
  `persist_until_done`, `always_visible`, self-heal). Say so explicitly in the docstring.
- **`urgency` stays as it is.** Stored (`models.py:172`), serialized to the client
  (`serializers.py:38`), consumed by nothing. Tempting to repurpose — do not: it is per-call with
  stored history, reach is per-type. Separate cleanup, separate WO.
- **Do not derive "unowned" from a NULL owner FK.** Emitting into a subscription category is the
  call site's explicit decision. Consumer FKs are typically `SET_NULL` (e.g.
  `hram/backend/runs/models.py:74`), so a deleted user would otherwise turn that user's whole
  notification history into an estate-wide broadcast.
- No new transport channels; no changes to the dispatchers themselves.
- No change to `notify()`'s signature beyond what the axes strictly require; the per-call
  `channels=` override stays.
- Not any app adoption (hram NOTIF-16, spesix NOTIF-17) and not hram's pin bump (NOTIF-28).
- No `develop → main` promotion of either repo.

## TIER

**Tier 2 — shared core, two repos, two live consumers, plus a shared messaging registration that
every future adopter inherits.** Independent `reviewer` mandatory; `ui_reviewer` mandatory for the
ucm diff, spawned **concurrently** in the same background batch. `[approval schema]` if scope E
needs a model or field change — stop first.

## RISKS

- **Silent delivery drift on migration** — a type quietly gaining or losing a channel for some user
  state. Parity tests are the gate, not the build.
- **Accidental broadcast** — the default-`True` passive tier makes this the *easy* mistake, not an
  exotic one. Test 5 exists solely for it.
- **Overloaded consent rows** (scope E) reaching the same outcome by a different route.
- **Privacy on subscription categories.** A subscription makes one user's system events visible to
  every subscriber, including parameters carried in `content` (rendered server-side for
  email/push). Content for subscription-category events must carry nothing not meant for a wider
  audience — and this is now a test, not just prose (test 6).
- **Two overlapping concepts** if scope B is skipped or half-done — the most likely way this WO
  leaves the codebase worse than it found it.
- Fan-out dispatch is synchronous inside `notify()`: N subscribers means N sends inline on the
  caller's thread. Note the characteristic; do not optimise speculatively.
- Publishing both packages makes every later adopter depend on these shapes.

## REQUIRED TESTS (write these; the Orchestrator runs them)

Narrow, across both repos — not a full suite:

1. **Reach → channel resolution** per user-preference state: an active-declaring type resolves to
   the user's chosen active channel(s), and to none when they have opted all of them out.
2. **Active-without-passive** (jg's case): no chip and no feed entry is produced for such a type.
3. **Bounded fallback** (scope C): a user with no usable active channel receives a *both*-type
   passively; an *active-only* type stays undelivered for that user and the "no active channel"
   state is reported to the settings surface. Both halves in one test file — the pair is the point.
4. **Parity for every migrated consumer** — for each cockpit type and the shared messaging type, the
   resolved channel set is identical before and after, across the default state and one explicit
   opt-in state. This is the regression guard for the whole reach axis.
5. **The default-`True` trap is closed**: a user who has never touched notification settings
   receives **nothing at all** — no chip, no feed entry — for a subscription-category event. Assert
   absence on the passive channels, not just on email.
6. **Privacy constraint** (new, from review): a subscription-category notification delivers to
   subscribers without exposing content fields the emitting call site did not mark as shareable —
   pin whatever mechanism the implementer chooses for this, so the constraint is enforced rather
   than merely documented.
7. **Empty subscriber list** (scope F): no `Notification` row is authored.
8. **Categories endpoint** lists the current app's registered categories and no others, each with a
   resolvable label key.
9. **`feed_visible` reconciliation** (scope B): one test pinning the decided relationship.
10. ucm: `NotificationSettings` renders the reach axis, the no-active-channel state, and
    server-reported categories with a working subscription toggle — with no new required props.

## TARGET REPOS

`C:\Users\biglmi\Documents\webapps\django-core-micha` and
`C:\Users\biglmi\Documents\webapps\ui-core-micha`. Both currently on `main` (neither has a
`develop`); commit to the trunk per `AGENTS.md`. Never the workspace root.

**Secondary repo touched by scope I:** `cockpit` — its registration + tests, landing on cockpit's
`develop`, with cockpit's own scoped tests green. (jg needs no repo-local change: its type lives in
shared dcm code, see scope I.)

## SEQUENCING

Must be **published (PyPI + npm) before NOTIF-28**, hram's single pin bump. NOTIF-16 consumes both
axes and is blocked on 28.

## MINI-HANDOVER

```
Orchestrator: implement work-orders/NOTIF-26.md in django-core-micha (+ ui-core-micha, + the cockpit
registration migration in scope I). git pull first, read the WO — this is the MERGED order (former
NOTIF-27 is folded in and dropped). Load-bearing: the reach axis is not a boolean (three states);
the scope-C fallback applies ONLY to types that declare passive; the subscriber resolver must key on
an explicit opt-in and must NOT reuse is_channel_enabled() (its bottom tier defaults chip/popup/todo
to True and would broadcast to everyone). jg's messaging type is registered in SHARED dcm code
(src/django_core_micha/messaging/notifications.py:34-38), not in jg-ferien/backend/events/. Then
follow orchestrate-codex.
```

---

## PART B — IMPLEMENTATION MAP (Orchestrator, filled 2026-08-06)

Three sequenced Codex runs, one per repo (cross-repo, cannot share one `cwd`). Run 2 depends on
Run 1's finalized API contract; Run 3 depends on both packages' versions being bumped (not
necessarily yet published) so its pin bump has a real target.

### Context package — verified against landed code 2026-08-06

**`src/django_core_micha/notifications/types.py`** (53 lines) — `NotificationType` is a
`@dataclass` with `default_channels: list[str]` / `eligible_channels: list[str]`
(lines 16-17), `feed_visible: bool = True` (line 24, already has a docstring-style comment
distinguishing it from the todo registry). `_REGISTRY: dict[str, NotificationType]` module-level
dict (line 31); `register_notification_type`/`get_notification_type`/`iter_feed_hidden_type_keys`
(lines 34-52) are the only public surface. This is where the reach declaration replaces
`default_channels`/`eligible_channels` — **or coexists during migration**, implementer's call, but
by end-of-WO no registration site should pass a raw channel list to express reach.

**`src/django_core_micha/notifications/router.py`** (33 lines, NOT explicitly named in the
Envelope but load-bearing — read it) — `resolve_channels(ntype, user, override=None)` (line 14) is
the ONLY place `default_channels`/`eligible_channels` are consumed:
`base = override if override is not None else ntype.default_channels` (line 23),
`eligible = [c for c in base if c in ntype.eligible_channels]` (line 24), then per-channel
`is_channel_enabled` + critical-force check (lines 26-31). Scope C's bounded fallback
(active→passive only if the type declares passive) belongs here: today `resolve_channels` has no
concept of "no active channel available" degrading anywhere — that branch must be added. The
per-call `channels=` override (`api.py:92`, NON-GOALS line 161) narrows `base`, so it must keep
working against whatever replaces `default_channels`.

**`src/django_core_micha/notifications/prefs.py`** (55 lines) — `is_channel_enabled(user,
category, channel)` (line 9), 4-tier precedence, bottom tier at lines 52-54: `chip/todo/popup`
default `True`, `email/push` default `False`. **Do not touch this function's precedence order** —
the subscription resolver (scope D) must be a NEW function that never calls this one, per the WO's
explicit prohibition.

**`src/django_core_micha/notifications/api.py`** (106 lines) — `notify(...)` (line 68) authors the
`Notification` row via `_get_notification_with_retry` at lines 81-88, THEN loops
`_normalize_recipients(recipients)` at line 90. Scope F's "resolve first, return early" must sit
between the type lookup (line 74) and the row-authoring call (line 81) — if the category is
subscription-driven and resolves to zero recipients, return before line 81 without creating the
`Notification`. Scope D's resolver output must be able to substitute for or extend the `recipients`
kwarg for subscription-category calls — decide whether that's a new `notify()` kwarg or a
resolve-then-pass-recipients helper the call site uses; either way, `recipients` stays required
(NON-GOALS line 160: no signature change beyond what the axes strictly require).

**`src/django_core_micha/notifications/models.py`** — `NotificationCategoryChannelPreference`
(lines 65-96): `user, category, channel, enabled`, unique on `(user, category, channel)`, no
discriminator field. Scope E needs either a new boolean/enum field here (e.g. distinguishing
"override" vs "subscription" rows) or a separate model — **either path is `[approval schema]`,
stop and ask the operator before writing the migration**, per the WO. `Notification.urgency`
(line 172) and `NotificationChannelDefault`/`NotificationPreference` (lines 26-62) are untouched.

**`src/django_core_micha/notifications/views.py`** — `CanonicalInboxView`/`CanonicalUnreadCountView`
(lines 150-213) both `.exclude(notification__notification_type__in=iter_feed_hidden_type_keys())`
(lines 160, 210) — scope B's target. `NotificationPreferenceView` (lines 36-42) only serializes the
legacy `NotificationPreference` row via `NotificationPreferenceSerializer` (email/push/preview
booleans) — it has no category surface today. Scope G's categories-in-`preferences/` and scope D's
subscription toggle need either new fields on this view+serializer or a sibling endpoint; either
way it must report only the CURRENT APP's registered categories (read `_REGISTRY` filtered to
`category` values in-process — no cross-app leak) with a label key resolvable through the existing
text registry (see `messaging/notification_texts.py` for the pattern already in use).

**`src/django_core_micha/notifications/urls.py`** (27 lines) — `preferences/` at line 17 binds to
`NotificationPreferenceView`. Add any new path(s) here (e.g. `preferences/categories/` or fold into
the existing response) — either is the implementer's call.

**`src/django_core_micha/notifications/serializers.py`** (57 lines) — `NotificationPreferenceSerializer`
(lines 7-10) is `email_opt_in/push_opt_in/push_preview_opt_in` only. A new serializer (or extension)
is needed for categories + subscription state + the scope-C "no active channel configured" signal
per type. `urgency` passthrough at line 38 (`CanonicalNotificationSerializer`) is untouched.

**`src/django_core_micha/messaging/notifications.py`** (115 lines) —
`register_messaging_notification_type(app_key)` (line 23) builds
`NotificationType(key=..., default_channels=["email","push"], eligible_channels=["email","push"],
feed_visible=False)` at lines 34-38. This is the "active only" case (no `chip` in either list) AND
independently `feed_visible=False` — after scope B's reconciliation this type's `feed_visible` may
become derived rather than separately declared; if so, this call site's `feed_visible=False` kwarg
either goes away or must still evaluate to hidden. This file is SHARED CODE (jg is merely the only
caller today, via `jg-ferien/backend/messaging/apps.py:19`, which passes no channel args and needs
NO changes) — treat any signature change here as a wider-blast-radius edit than "migrate jg".

**Do-not-touch confirmed by inspection:** `notifications/todo/registry.py` (74 lines) is a fully
separate registry (`_PROVIDERS`/`_CONFIGS`/`_CANDIDATE_USERS`) with its own lifecycle fields — no
reach/channel concept exists there and none should be added.

**Existing tests to extend/adapt** (`src/django_core_micha/notifications/tests/`):
`test_notification_api.py` (router/notify mechanics — `test_router_override_is_narrowed_to_eligible_channels`,
`test_router_respects_opt_out_but_keeps_chip`, `test_critical_router_forces_available_default_but_not_unavailable_push`),
`test_canonical_notification_api.py::test_feed_visible_policy_hides_only_explicitly_hidden_registered_types`
(scope B's existing pin), `test_preference_matrix.py` (precedence tiers — do not break),
`test_dispatch.py::test_resolve_channels_still_excludes_popup_for_types_that_do_not_declare_it`.
New test files/functions per the WO's "REQUIRED TESTS" list (1-9 land in dcm; 4's cockpit half and
messaging half both touch dcm-visible fixtures — coordinate with the cockpit run).

**Versioning:** `pyproject.toml` currently `2.40.6`. This is additive-shaped scope-A/B/C/D/E/F/G
work — a minor bump unless scope E's `[approval schema]` model change is deemed to warrant more;
implementer proposes, Orchestrator confirms before the version-bump commit. `CHANGELOG.md` style:
`## [x.y.z] — YYYY-MM-DD` then `### Added`/`### Changed` prose citing NOTIF-26.

### RUN 1 — django-core-micha (cwd: `C:\Users\biglmi\Documents\webapps\django-core-micha`)

Scope: A, B, C, D, E (stop for `[approval schema]` if a model/field change proves necessary — do
NOT write the migration until the operator confirms), F, G, and the shared messaging registration
migration (scope I, `messaging/notifications.py` only — NOT jg-ferien, which needs no change).
Required tests: 1, 2, 3, 5, 6, 7, 8, 9, and the messaging-type half of 4 (jg's type resolves
identically before/after). Do NOT touch `todo/registry.py`, `urgency`, `NotificationChannelDefault`,
or `NotificationPreference`. Do NOT bump `pyproject.toml`/`CHANGELOG.md` yet — that happens once at
Orchestrator finalize, after review, alongside Run 3's pin-bump target is known.

### RUN 2 — ui-core-micha (cwd: `C:\Users\biglmi\Documents\webapps\ui-core-micha`)

Scope: H only — rebuild `src/notifications/NotificationSettings.jsx` (currently 221 lines, zero
props, fetches via `getNotificationPreferences()`/patches via `patchNotificationPreferences()` from
`./api`, renders exactly Email + Push/Push-preview toggle blocks). Must consume Run 1's FINAL
`preferences/` response shape (the Orchestrator pastes the actual finalized fields into this run's
prompt after Run 1 lands — do not guess the contract). Zero new props (Pattern 3,
`webapp-management/SHARED_CAPABILITIES.md` — ucm owns everything). `NotificationBell.jsx` and
`NotificationsProvider.jsx` need NO changes (verified: both are channel/category-agnostic, treat
the feed as opaque). Required test: 10. Do NOT bump `package.json` yet.

### RUN 3 — cockpit (cwd: `C:\Users\biglmi\Documents\webapps\cockpit`)

Scope: scope I bullet 1 — migrate `backend/notify/apps.py`'s six-type `policies` tuple (lines
81-88 in the file as it stands: three "both" types `deploy_failed`/`workflow_failed`/`monitor_down`
at `["chip","push","email"]`, three "passive only" types `deploy_recovered`/`workflow_recovered`/
`monitor_recovered` at `["chip"]`, shared `eligible_channels=["chip","push","email"]`) to the new
reach declaration at parity. Update `backend/tests/test_notifications.py`'s
`test_status_notification_type_policies` (lines 76-96, currently asserts `default_channels`/
`eligible_channels` as raw lists — parametrize/rewrite to assert resolved channel sets per user
state instead, matching the WO's requirement-4 framing). Bump `backend/requirements.txt`'s
`django-core-micha` pin (currently stale at `2.40.3`, three releases behind) and
`frontend/package.json`'s `@micha.bigler/ui-core-micha` pin (currently `2.26.4`, already current —
recheck) to the versions Run 1/Run 2 will publish. Lands on cockpit's `develop`. Cockpit's own
scoped tests must stay green.

### Sequencing / progress contract (all three runs)

Each Codex invocation follows the standard `PLAN:` / `PROGRESS: [n/total] <action>` / `RESULT:
DONE|BLOCKED <reason>` contract (see `references/codex-wo-template.md`). None of the three runs
commits — Orchestrator reviews the complete assembled diff (all three repos) together, runs the
independent `reviewer` + concurrent `ui_reviewer` (ui_reviewer scoped to the ucm + cockpit-frontend
diff), runs each repo's affected-tests gate, THEN bumps both packages' versions/CHANGELOGs and
commits all three repos in the same finalize pass.
