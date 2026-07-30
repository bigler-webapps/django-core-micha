# WORK ORDER NOTIF-19 (django-core-micha) — transient dispatch params + feed-visibility policy

**EXECUTION DIRECTIVE.** Implement through `codex exec` in the background — invoked **directly via
Bash** (never the `debugger`/`*_coder` Agent wrappers) with **both** flags `--skip-git-repo-check`
and `--dangerously-bypass-approvals-and-sandbox`, prompt passed as a positional argument from this
file. Fall back to direct Claude implementation only on Codex quota / rate-limit / non-zero exit.

Seams verified against dcm `main` @ `184655c` (version 2.34.0, clean).

## TIER
Tier 2 — this is the shared notifications platform, published to **public PyPI** and consumed by 14
app repos. The change is additive and backward-compatible, but a regression here is cross-app.
Independent `reviewer` mandatory.

## ORIGIN
Raised by two findings during the NOTIF-14 review in `jg-ferien` (see that repo's
`work-orders/NOTIF-14.md`, section B.8, and the orchestrator's security pass):

1. **Confidentiality.** `notify()` persists everything a dispatcher needs to render into
   `Notification.content` (a plain `JSONField`). jg's `Message.body`/`Message.title` are
   Fernet-`EncryptedTextField`s with a data migration and an **audited** privileged-read path, so
   persisting a cleartext excerpt there creates a second, unencrypted, unaudited copy of content the
   app deliberately encrypts. There is currently no way to give a dispatcher a value **without**
   persisting it.
2. **Feed scope.** `CanonicalInboxView` lists every `NotificationRecipient` row for the user and
   excludes only `category="todo"`; `CanonicalUnreadCountView` counts the same way. Neither filters
   by channel or intent. A type registered without the `chip` channel is therefore still fully
   visible in the canonical bell feed — so "deliver by email/push only, never show in the bell" is
   not currently expressible.

Both are the same root cause: the platform has no way to express *delivery-only* notifications.

## GOAL
Make "delivery-only" a first-class, policy-driven notion: let an author pass render values that are
never persisted, and let a registered type declare that it does not belong in the canonical feed.

## EXPECTED OUTCOME
- An author can pass `transient=` params to `notify()`; they reach the email/push dispatchers for
  rendering and are **never** written to `Notification.content`.
- A `NotificationType` can declare `feed_visible=False`; the canonical feed list and unread count
  both exclude those types.
- Every existing caller and every already-persisted row behaves exactly as before (both features
  default to today's behaviour).

## SCOPE

**A. `transient` params that are never persisted.**
- `src/django_core_micha/notifications/api.py:67` — add a keyword-only `transient=None` to
  `notify()`. It MUST NOT be merged into `content` and MUST NOT reach
  `Notification.objects.get_or_create_by_dedup` (the persisted row must be byte-identical to what it
  is today for a caller that passes no `transient`).
- `api.py:91` — forward it to `dispatch(..., ctx=transient)`.
- `dispatch.py:154` — `dispatch(channel, *, notification, recipient, ctx=None)`, forwarding `ctx` to
  `dispatcher.deliver(notification, recipient, ctx)`. **The `ctx=None` parameter already exists on
  the `Dispatcher` protocol (`dispatch.py:31`) and on all five concrete dispatchers (`:67`, `:84`,
  `:99`, `:114`, `:122`) and is currently never populated** — this WO finally wires it. No dispatcher
  signature changes.
- `dispatch.py:44` — `_render_content(content, user, transient=None)`, merging
  `{**content["params"], **(transient or {})}` before `.format()`. Transient wins on key collision.
- `EmailDispatcher.deliver` (`:84`) and `PushDispatcher.deliver` (`:99`) pass their `ctx` through.
- **`ChipDispatcher` (`:67`) and `PopupDispatcher` (`:122`) MUST NOT receive or embed transient
  values.** They push `notification.content` over the WebSocket; keeping them clean is the entire
  point of the feature. Leave both bodies unchanged.

**B. Make a rendering failure visible.**
`_render_content` currently wraps both `.format()` calls in a bare `except Exception` and silently
falls back to the raw key. With transient params a missing key becomes a realistic mistake, and the
current behaviour would ship a literal `{excerpt}` as an email body with no signal. Log a warning
(module logger already exists at `dispatch.py:12`) on the fallback path. Keep the fallback itself —
do not start raising.

**C. `feed_visible` policy flag.**
- `types.py:15` — add `feed_visible: bool = True` to the `NotificationType` dataclass. It must be
  **keyword-defaulted** so every existing construction site keeps working unchanged.
- `types.py` — add a helper returning the set of registered type keys with `feed_visible=False`
  (e.g. `iter_feed_hidden_type_keys()`), mirroring the existing `iter_registered_todo_types()`
  accessor style used by the views.
- `views.py:155` `CanonicalInboxView.get_queryset` and `views.py:196`
  `CanonicalUnreadCountView.get` — additionally exclude `notification_type__in=<hidden keys>`,
  alongside the existing `.exclude(notification__category="todo")`.
- **Unregistered types stay visible.** A `Notification` row whose `notification_type` is no longer in
  the registry must NOT be hidden — the exclusion is driven by an explicit `feed_visible=False`
  registration, never by absence from the registry. This matters for historical rows.

**D. Version bump.** `pyproject.toml` `version = "2.34.0"` → `"2.35.0"` (minor: additive features).
**Do the bump as the LAST edit**, and see the release note below — pushing `main` with an increased
version auto-publishes to public PyPI.

## DO NOT TOUCH
- The persisted shape of `Notification` / `NotificationRecipient` / `NotificationDelivery` — **no
  migrations**, no new model fields.
- `router.py` / `prefs.py` — channel resolution and the four-tier preference precedence are out of
  scope.
- `ChipDispatcher` / `PopupDispatcher` delivery bodies, and `delivery.py`'s
  `NOTIFICATION_ENVELOPE` contract.
- The todo channel, `todo/`, and the existing `category="todo"` exclusion (leave it as is; do not
  "unify" it with the new flag in this WO).
- Any consuming app repo. jg's adoption is NOTIF-14 and lands separately, after this releases.

## RISKS
- **Cross-app regression** is the headline risk: 14 repos consume this package. Both features must
  be strictly additive — a caller that passes neither `transient` nor `feed_visible` must produce a
  byte-identical persisted row, the same dispatch calls, and the same feed contents as today. Prove
  this with tests rather than asserting it.
- **Transient leakage** — the whole feature fails if a transient value ends up in `content`, in the
  chip/popup WS payload, or in the dedup key. Test each of those three negatively.
- **Feed exclusion over-reach** — excluding by category instead of by type key, or hiding
  unregistered historical types, would silently empty users' existing bells. This is a data-visible
  change with no migration to undo it.
- **`.format()` collision semantics** — decide and test whether transient or persisted params win
  (spec says transient) so the precedence is not accidental.

## REQUIRED TESTS / ACCEPTANCE
Written as part of implementation; the orchestrator runs them. Extend the existing
`src/django_core_micha/notifications/tests/` suites (`test_dispatch.py`, `test_notification_api.py`,
`test_canonical_notification_api.py`) following their established fixture style — note their
`_REGISTRY` save/restore pattern, which any new registration test must follow.
- `transient` params render into the email/push title/body;
- `transient` params are **absent** from the persisted `Notification.content` and do not change
  `dedup_key`;
- chip and popup WS payloads are unchanged and contain no transient value;
- transient wins over a same-named persisted param;
- a missing param falls back to the raw key **and logs**;
- `feed_visible=False` hides a type from both `feed/` and `feed/unread-count/`;
- `feed_visible` defaults to `True`, and an unregistered `notification_type` stays visible;
- a caller passing neither new argument gets today's exact behaviour (regression guard).

## TARGET REPO / WORKING DIRECTORY
`C:\Users\biglmi\Documents\webapps\django-core-micha`. Platform repo: commit directly to `main`
(no feature branches). **Do not push** — the orchestrator reviews, runs the suite, and pushes.

## RELEASE NOTE (orchestrator, not Codex)
`.github/workflows/publish.yml` publishes to **public PyPI** on push to `main` whenever
`pyproject.toml`'s version increases. The operator has explicitly authorised the 2.35.0 release once
the change is green and reviewed. The release is public and cannot be unpublished.

## PROGRESS CONTRACT
Emit a `PLAN: <step1> | <step2> | …` line up front, then a single-line
`PROGRESS: [<n>/<total>] <present-tense action>` **before every relevant action** (file opened, file
edited, command run) and `PROGRESS: [<n>/<total>] done` on step completion, spaced so no gap exceeds
~2 min. stdout unbuffered. Exactly one final `RESULT: DONE|BLOCKED <reason>`.

## MINI-HANDOVER (paste into a fresh Orchestrator session)
```
Orchestrator: implement work-orders/NOTIF-19.md in django-core-micha (main). git pull first, read the
WO, then follow orchestrate-codex (Codex-first, own independent review, commit on green; the PyPI
release is operator-authorised).
```
