# WORK ORDER NOTIF-27 (django-core-micha + ui-core-micha) — subscriptions for unowned events

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

Answer the question the platform cannot answer today: **who receives a notification that has no
owner?** `notify()` requires an explicit `recipients` list (`notifications/api.py:68`), so an event
nobody triggered — a nightly job, a CLI-started run, a system condition — has no addressee at all.

Expected outcome: an app can emit an event into a **category** and the platform resolves the
recipients from users who have **explicitly subscribed** to that category. Plus the settings surface
that lets a user subscribe, driven by data rather than by per-app props.

## WHY

hram's long-running sweeps (NOTIF-16) are started both from the web UI (owner known via
`Campaign.created_by`) and headlessly from `manage.py` (no owner). A per-invocation `--notify-user`
flag was considered and rejected by the operator on 2026-08-06 for a good reason: **it fails
silently.** A forgotten flag produces no error, only a notification that never arrives — noticed
exactly when someone is waiting for it. A subscription moves the decision from per-invocation
(forgettable) to per-type registration (declared once, reviewed once).

## SCOPE

**A. A subscription-based recipient resolver** in dcm: given a category, return the users who
subscribe to it.

**The hard requirement, and the reason this is not a one-liner:** the resolver must key on an
**explicitly set** opt-in, and must **NOT** reuse `is_channel_enabled()`
(`notifications/prefs.py:9-22`). That function's bottom precedence tier reads, verbatim: *"email/push
default False (opt-in), all other channels (chip/todo/popup) default True."* A fan-out that filters
by `is_channel_enabled` would therefore deliver a **chip to every user in the app on day one**,
without anyone subscribing. Opt-in must be enforced at recipient resolution: **no explicit
subscription row → not a recipient → nothing on any channel.** State this reasoning in the
resolver's docstring; it is exactly the kind of "inconsistency" a later reader would helpfully
"fix" back into a broadcast.

Storage: prefer the existing `NotificationCategoryChannelPreference`
(`notifications/models.py:63`) — per-user, per-category, per-channel — over a new model, unless the
implementer finds a concrete reason it cannot express "subscribed to category X". If a new model is
needed, that is `[approval schema]` and stops for the operator first.

**B. Empty-subscriber behaviour.** `notify()` creates the `Notification` row **before** the
recipient loop (`api.py:80-88`), so a zero-recipient call leaves an orphan row with no recipients.
Guard it: resolve first, return early when nobody subscribes, do not author the row.

**C. Categories become discoverable data.** The `preferences/` endpoint (`notifications/urls.py:17`)
reports the categories **this app** has actually registered (the in-memory type registry in
`types.py` already knows them), each with a label key resolvable through the existing text registry
so it can be translated.

**D. ucm `NotificationSettings` becomes category-driven** — it renders whatever the server reports,
with no new props. Operator decision 2026-08-06: explicitly **not** a per-app prop. The component is
Pattern 3 in `webapp-management/SHARED_CAPABILITIES.md` ("ucm owns everything") precisely because it
needs no host knowledge; a prop carrying "hram has sweeps" would break that and would need another
prop for the next app. Existing consumers (cockpit, jg) must see their own categories appear with
no call-site change.

## NON-GOALS / DO NOT TOUCH

- **Not the reach/channel model** — that is NOTIF-26. This WO does not change how a notification is
  transported, only who is resolved as a recipient and how categories surface in settings.
- **No change to owner-addressed dispatch.** An explicit `recipients` list keeps working exactly as
  today; the resolver is an additional path, not a replacement.
- **Do not derive "unowned" from a NULL owner.** Emitting into a subscription category is the call
  site's explicit decision. Consumer-side FKs are typically `SET_NULL` (e.g.
  `hram/backend/runs/models.py:74`), so a deleted user would otherwise turn a private notification
  into an estate-wide broadcast. The platform must not make that inference available by accident.
- Not the hram adoption (NOTIF-16), not the hram pin bump (NOTIF-28).
- No new channels, no todo-provider changes, no `develop → main` promotion.

## TIER

**Tier 2 — shared core, two repos.** Independent `reviewer` mandatory; `ui_reviewer` mandatory for
the ucm diff, spawned concurrently in the same batch. `[approval schema]` **only if** scope A turns
out to need a new model — in that case stop for the operator before writing the migration.

## RISKS

- **Accidental broadcast** — the single most consequential failure here, and the default-`True` chip
  tier makes it the *easy* mistake rather than an exotic one. Test 2 below exists solely for it.
- **Privacy/visibility.** A subscription category makes one user's system events visible to every
  subscriber, including any parameters carried in `content` (rendered server-side for email/push).
  Keep `content` for subscription-category events free of anything not meant for a wider audience;
  note this constraint where the resolver is documented.
- Fan-out size: one `Notification` with N `NotificationRecipient` rows is natively supported, but
  dispatch is synchronous inside `notify()` — a large subscriber list means N sends inline on the
  caller's thread. Note the characteristic; do not optimise speculatively.
- Changing the `preferences/` payload shape touches two shipped consumers.

## REQUIRED TESTS (write these; the Orchestrator runs them)

1. **Resolver returns only explicit subscribers** — a user with a subscription row is resolved; a
   user with no row is not.
2. **The default-`True` trap is closed** (the regression guard for this WO): a user who has never
   touched notification settings receives **nothing at all** — specifically no chip and no feed
   entry — for a subscription-category event. Assert the absence on the passive channels, not just
   on email.
3. **Empty subscriber list** (scope B): no `Notification` row is authored, no orphan.
4. **Categories endpoint** lists the categories registered by the current app and no others, each
   with a resolvable label key.
5. **Consumer non-regression**: cockpit's and jg's existing preference reads keep working against
   the changed payload.
6. ucm: `NotificationSettings` renders server-reported categories and persists a subscription
   toggle (component test), with no new required props.

## TARGET REPOS

`C:\Users\biglmi\Documents\webapps\django-core-micha` and
`C:\Users\biglmi\Documents\webapps\ui-core-micha`. Both currently on `main`. Commit to the trunk per
`AGENTS.md`. Never the workspace root.

## SEQUENCING

Independent of NOTIF-26; either order. **Both must be published before NOTIF-28** so hram bumps its
pins exactly once. NOTIF-16 consumes all three.

## MINI-HANDOVER

```
Orchestrator: implement work-orders/NOTIF-27.md in django-core-micha (+ the matching ui-core-micha
NotificationSettings change). git pull first, read the WO. The load-bearing rule: the resolver must
key on an EXPLICIT subscription row and must NOT reuse is_channel_enabled() — its bottom tier
defaults chip/popup/todo to True, which would broadcast to every user on day one. Test 2 is that
guard. Then follow orchestrate-codex.
```
