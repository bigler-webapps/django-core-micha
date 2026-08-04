# WORK ORDER MSG-13 (django-core-micha) — push notifications display the raw i18n key

**EXECUTION DIRECTIVE.** If you are the implementer reading this as your own spec, this section is not
addressed to you — it tells the Orchestrator how to invoke you; you ARE that invocation, do not shell
out to `codex exec`. Orchestrator: implement through `codex exec` in the background, invoked **directly
via Bash** (never the `debugger`/`*_coder` Agent wrappers) with **both** flags `--skip-git-repo-check`
and `--dangerously-bypass-approvals-and-sandbox`, prompt passed as a positional argument from this
file. Fall back to direct Claude implementation only on Codex quota / rate-limit / non-zero exit.

Feature-level WO (Expertenchat envelope). Operator report 2026-08-04 with a screenshot from staging: a
browser push notification whose **title and body both read `messaging.new_message`**.

> **ID note:** this is dcm's own `MSG-13`. `jg-ferien` has an unrelated `MSG-13` — its **done** row for
> the ucm Thread fetch-on-open fix (`d48cebf`, ui-core-micha 2.21.2, "Noch keine Nachrichten"). Different
> repo, different namespace. **Always say which repo when referencing one across repos** — these two have
> already been confused once, on 2026-08-04.

## TIER
Tier 2 — shared-core, user-facing on every app with push enabled. Independent `reviewer` mandatory.
**`sec_reviewer` mandatory for scope C** — the operator has ruled that message content may go into the
push; `sec_reviewer` confirms the implementation honours the ruling's limits.

## THE DEFECT — three layered problems, all verified 2026-08-04

**1. There is no translation catalogue at all.** `messaging/notifications.py:45` sends
`{"title_key": "messaging.new_message", "body_key": "messaging.new_message", ...}`, and
`notifications/dispatch.py:54` renders it with `gettext(title_key).format(**params)`.

`find src -name '*.po'` returns **nothing** — dcm ships no `.po` files whatsoever. `gettext()` returns
its msgid verbatim when no catalogue entry exists, so the recipient sees the raw key.

**And it fails silently.** `gettext` does not raise for a missing entry, so the `except` branches at
`dispatch.py:55-57` and `:60-62` — which exist precisely to log a fallback — **never fire**. There is
no warning, no error, nothing. The notification pipeline reports success.

**2. Title and body are the same key.** Even with a catalogue, `title_key == body_key ==
"messaging.new_message"`, so the notification would render the identical string twice — exactly what the
screenshot shows, only translated.

**3. The interpolation has nothing to interpolate.** `params` carries only
`{"message_id": str(message.id)}`. `.format(**params)` is wired up, but no sender name, no conversation
title, no excerpt is ever passed. A translated string could therefore say no more than "Neue Nachricht".

This is the same shape as `delivered_count`: a mechanism fully built — key, params, `gettext`,
`.format()` — whose inputs were never supplied.

## SCOPE

**A. Make the text render.** Two coherent routes; pick one and state why:

1. **Ship catalogues** — add `.po`/`.mo` for de/en/fr covering every notification key dcm emits, and
   wire compilation into the build. Keeps the existing design.
2. **Stop using `gettext` for notification content** and have the emitting app supply resolved text, or
   move resolution to the client that already owns i18n.

Route 1 is the smaller change and matches the existing intent. **Route 2 deserves genuine
consideration**: dcm is a library, its consumers each own an i18n stack, and a second catalogue in the
library is a second place translations can drift. Whichever you choose, **every notification key dcm
emits must be covered — audit them, do not fix only `messaging.new_message`.**

**B. Distinct title and body.** A push needs a short title and an informative body. Decide the shape
(what the title says, what the body says) and give them separate keys.

**C. Useful `params` — what the push is allowed to say.**

**CORRECTION 2026-08-04 — an earlier version of this section overstated the exposure and would have
given `sec_reviewer` the wrong threat model.** It claimed the content "transits a third party" and
"leaves the encrypted domain", implying the push provider can read it. **It cannot.**

dcm sends via `pywebpush` with the subscription's `p256dh`/`auth` keys and VAPID
(`notifications/delivery.py:187-193`) — that is RFC 8291 Web Push payload encryption. The payload is
encrypted with keys the **browser** generated; the push service (Mozilla autopush, FCM, APNs) relays an
opaque ciphertext it has no key for.

The real exposure is **device-local**, and it is genuinely smaller:

- the service worker decrypts the payload and hands plaintext to the OS notification system,
- the OS renders it on a **lock screen without authentication** — anyone glancing at the device reads it,
- it may persist in the OS notification history and in device backups.

Two things follow that put this in proportion:

- **Fernet-at-rest is not breached by this.** dcm decrypts the body anyway to serve REST and to compose
  the digest email; the app decrypts on every read. Push is not a new decryption boundary.
- **The digest email is the weaker channel, not push.** A digest transits SMTP and sits in a mailbox in
  plaintext, readable by the mail provider. Content in an end-to-end-encrypted push is strictly safer
  than the same content in an email the project already sends.

**RULED BY THE OPERATOR, 2026-08-04: show the sender name and the message text.** This is settled — do
not re-open it, and do not narrow it on your own judgement.

Concretely: title = sender name, body = the message text (see scope B for the exact split).

The operator was told, and accepted, the exposure as corrected above: the body is readable on a locked
device and may persist in OS notification history. It is **not** readable by the push service, and it
is not a new decryption boundary. `sec_reviewer` confirms the implementation honours the ruling's
limits; it does not re-litigate the ruling.

Implementation requirements that follow from it:

- **Truncate the body.** A push has no useful length budget and a long message would be clipped
  arbitrarily by the OS. Truncate deliberately (state the limit) with an ellipsis, so the cut is the
  product's decision rather than the platform's.
- **Non-text messages need a sensible body**, not an empty string: an attachment-only send, a poll, an
  announcement. Decide the fallback wording per kind and route it through the same catalogue as scope A.
- **A deleted or edited message must not resurface its old text.** A push already delivered cannot be
  recalled, but do not construct new pushes from soft-deleted content — check `deleted_at` before
  composing.
- **Per-recipient safety**: `_render_content` runs under
  `translation.override(_recipient_language(user))` and the same content dict is rendered per recipient
  — verify the sender name and body cannot leak across recipients, and that a recipient only ever
  receives content from a conversation they participate in.

**D. A missing catalogue entry must not be silent.** The `except` branches were written to log a
fallback but cannot fire for the actual failure mode. Detect the real case — a rendered string identical
to its key is the cheap signal — and log it at warning level with the key. Without this, the next
missing key ships to users exactly as this one did.

**E. A per-user "hide preview" toggle — operator-requested 2026-08-04, IN scope.**

Add it to ucm's existing notification settings (`ui-core-micha`
`notifications/NotificationSettings.jsx`, which already renders `Switch`es over a preferences object,
e.g. `email_opt_in` at `:164`). Persist it on dcm's existing `NotificationPreference`
(`notifications/models.py:26`) — **do not introduce a new model or a second preferences mechanism.**

When the preference is off, the push falls back to a content-free body (the scope-B wording for "new
message" without sender or text). The **title may keep the sender name or not — state which you chose**;
"hide preview" is ambiguous about the title and an unstated reading will be wrong for someone.

**Default: preview ON**, matching the operator's ruling. A user opting out is the exception, not the
baseline.

**Scope E spans two repos and this WO owns only the dcm half.** dcm ships the preference field on
`NotificationPreference`, exposes it on the preferences API, and makes push composition respect it —
that half must be independently correct, with preview ON as the default, so dcm is shippable before any
UI exists. **The `Switch` in `ui-core-micha`'s `NotificationSettings.jsx` is a companion change and is
NOT deliverable from this repo** — flag it in the completion note so a `ui-core-micha` WO gets written.
Do not mark scope E complete on the dcm side and let the UI silently never arrive.

## NON-GOALS / DO NOT TOUCH
- Do not change the service worker's rendering (`ui-core-micha` `notifications/serviceWorker`,
  `showNotification(payload.title, {body: payload.body})`). It reads `title`/`body` correctly; the
  defect is that they arrive unrendered.
- Do not change push subscription, VAPID, or delivery transport — **push delivery works**, only the
  content is wrong.
- Do not change `_recipient_language` or the per-recipient override.
- Do not touch the in-app notification feed's own rendering beyond what shares `_render_content`; if a
  change would affect it, say so explicitly rather than discovering it in review.
- Do not narrow scope C's ruling. (The preview toggle is now scope E, in scope.)

## RISKS
- **Scope C is the one that can do lasting harm, and the operator has accepted that risk.** A push
  cannot be recalled and is visible on a locked device. The residual risks the implementation must
  contain are therefore the ones it controls: an untruncated body, a body built from soft-deleted
  content, and any cross-recipient leakage. Tests 5 and 6 exist for these.
- Adding catalogues to a library affects every consumer's translation story; a key colliding with a
  consumer's own namespace would silently change their text. Namespace dcm's keys clearly.
- `.format(**params)` on translator-supplied strings is an injection surface for malformed
  placeholders — that is what the `except` was for. Keep it, and make it actually reachable (scope D).
- Fixing only `messaging.new_message` leaves every other emitted key broken and the next report
  identical. Audit them.

## REQUIRED TESTS TO WRITE
Narrow and behavioural. Do NOT run the full suite.

1. A rendered notification's title is **not** equal to its `title_key` — the regression test for this
   exact report. It must **fail against current code**; prove it.
2. Title and body differ for a new-message notification.
3. Every notification key dcm emits renders to a non-key string in de, en and fr — parametrise over the
   emitted keys rather than asserting one.
4. Scope D: a missing catalogue entry produces a warning naming the key.
5. Scope C: the push payload contains the sender name and the message text, **truncated at the stated
   limit**, and a soft-deleted message never produces a body carrying its old text.
6. Scope C: a recipient never receives a push whose body comes from a conversation they do not
   participate in — the cross-recipient leakage guard.

7. Scope E: with the preference **off**, the push body carries **no** message text and no sender name
   in the body — assert the absence, not merely that the body differs.
8. Scope E: the preference **defaults to on** for a user who has never set it, and an existing
   `NotificationPreference` row without the field behaves as on.

**Non-vacuity:** test 1 must fail today; test 5's truncation assertion must fail if the limit is
removed; test 6 must fail if the participation check is dropped; test 7 must fail if the preference is
ignored during composition.

## TEST SCOPE FOR THE GATE (orchestrator)
`notifications/` and `messaging/`. Compare before/after via `git stash` and state the measured baseline.

## TARGET REPO
`C:\Users\biglmi\Documents\webapps\django-core-micha`. Branch `develop` if it exists, else `main`.
Publish + version bump per the repo's release flow.

## MINI-HANDOVER (pastable)

> Repo: `C:\Users\biglmi\Documents\webapps\django-core-micha` (branch `develop` if it exists, else
> `main`). Work order: `work-orders/MSG-13.md` — read it fully, then follow the `orchestrate-codex`
> skill.
>
> Push notifications render as the literal string `messaging.new_message`, title and body both.
> **Verified: dcm ships no `.po` files at all**, so `gettext()` returns the msgid verbatim — and because
> that is not an exception, the `except` branches written to log a fallback never fire. The failure is
> completely silent and the pipeline reports success.
>
> **Push delivery itself works — do not touch the transport or the service worker.** ucm reads
> `payload.title`/`payload.body` correctly; they simply arrive unrendered.
>
> **Scope C is RULED — no gate stop:** the operator decided 2026-08-04 to show **sender name + message
> text**. Read the correction in scope C before forming a threat model: the push service **cannot** read
> the payload (`pywebpush` + VAPID = RFC 8291 end-to-end encryption), and this is not a new decryption
> boundary — the real exposure is a locked device's screen and the OS notification history. An earlier
> version of this WO said otherwise and was wrong. `sec_reviewer` confirms the implementation honours
> the ruling's limits — truncation, non-text fallbacks, no body from soft-deleted content, no
> cross-recipient leakage — rather than re-litigating the ruling.
>
> **Scope E (per-user preview toggle) spans two repos and this WO owns only the dcm half** — the
> preference field, its API exposure, and push composition respecting it, defaulting to ON. The `Switch`
> in `ui-core-micha`'s `NotificationSettings.jsx` needs its own WO; flag it in the completion note so it
> does not silently never arrive.
>
> Fix **all** emitted keys, not just this one, or the next report is identical.

## IMPLEMENTATION MAP (Orchestrator, part B — within the envelope above; do not pre-solve, this points at seams)

**Target repo confirmed:** `develop` does not exist in this repo (only `main` + short-lived `feat/*`/`fix/*`
remote branches, none checked out). Work on `main` per the WO's own fallback rule.

**Route decision for scope A — steer, do not dictate:** there is **no gettext catalogue infrastructure
anywhere in this repo** (`find -iname '*.po'` / `*.mo` → nothing; the only `LOCALE_PATHS`/`.po` machinery
is Django's own admin translations). But there IS a working precedent for exactly this problem, already
shipped and presumably tested: `emails/email_texts.py` — `SUPPORTED_LANGUAGES = ("de", "fr", "en")`,
`get_preferred_language(user)` resolving `user.profile.language` with an `en` fallback, and per-language
text built in plain Python (dicts / f-strings), no `gettext`, no `.po` compile step. Route 2 (stop using
`gettext`, resolve keys through a small in-repo language table, same shape as `email_texts.py`) fits the
codebase as it already stands; Route 1 (ship `.po`/`.mo` + wire compilation into the build) would be the
first gettext catalogue in the repo with no existing build step to hook into. Pick the route, but if you
pick Route 1 explain why the `email_texts.py` precedent doesn't apply.

**Files to touch:**

- `notifications/dispatch.py:35-64` — `_recipient_language(user)` (existing, reads
  `user.contact_profile.language`, defaults `"de"`, already validates against `{"de","en","fr"}` — reuse
  as-is) and `_render_content(content, user, transient)` (existing, currently does
  `gettext(title_key).format(**params)` — this is the function that must stop returning the raw key).
  `params = {**params, **(transient or {})}` already exists — `transient` is where per-message dynamic
  data (sender name, message text) belongs; `content["params"]` is only `{"message_id": ...}` today.
  The `except Exception` fallback-to-key branches at `:55-57`/`:60-62` are the ones scope D must make
  distinguishable from a genuine render (a caught exception vs. a silently-unresolved key look the same
  today — a missing catalogue entry hits neither branch, it just returns the key with no exception at all).
- `messaging/notifications.py:34-51` — `notify_message()` is the sole caller for the messaging type. It
  already computes `transient={"title": decrypt_text(...), "body": decrypt_text(...)}` from
  `message.title`/`message.body` (**note: `Message.title` is an optional message title field, e.g. for
  polls/announcements — NOT the sender's name**; sender name must come from `message.sender`, the FK at
  `messaging/models.py:137`, e.g. `message.sender.get_full_name()` with a fallback for a null/deleted
  sender). This is the function to change for scope B (distinct title/body keys) and scope C (pass
  sender name + truncated body text into `transient`/`params`, respecting the preview preference). Check
  `message.deleted_at` (`messaging/models.py:145`) before building body text — a soft-deleted message
  must not have its text pulled into a push composed after deletion.
- `messaging/notifications.py:15-31` — `register_messaging_notification_type()` — audit for any other
  emitted keys in this app; scope A/D must cover **every** key dcm emits, not just `messaging.new_message`
  — grep the whole repo for other `notify(type=..., content={"title_key": ...})` call sites (this is the
  only one in `messaging/`, but check `notifications/` and any other app module for its own event types).
- `notifications/models.py:26-31` — `NotificationPreference` (`email_opt_in`, `push_opt_in` booleans,
  no migration surprises — straightforward `BooleanField` additions elsewhere in this model). Add the
  scope-E field here, e.g. `push_preview_opt_in = models.BooleanField(default=True)` — **default True**
  per the ruling. New migration required.
- `notifications/serializers.py:7-10` — `NotificationPreferenceSerializer.Meta.fields = ["email_opt_in",
  "push_opt_in"]` — add the new field so `notifications/views.py:36` `NotificationPreferenceView`
  (`RetrieveUpdateAPIView`, `get_or_create(user=self.request.user)`) exposes it on the existing
  `preferences/` endpoint with no new view/URL needed.
- `notifications/delivery.py:153-183` `_send_push(...)` — no change expected (payload shape
  `{"title", "body", "url"}` stays the same); confirm scope C's truncation happens before this, not here.
- `notifications/admin.py:12-14` — cosmetic, add the new field to `list_display` if trivial, not required.

**Do not touch:** `notifications/prefs.py` (a *different*, apparently-legacy preference-resolution path
mentioned in its own comments — the WO's target is the live `NotificationPreference.push_opt_in`/new field
that `_send_push`/the serializer actually use; if `prefs.py` turns out to be load-bearing for scope E,
flag it rather than silently expanding scope).

**Migration:** one new Django migration for the `NotificationPreference` field addition — check existing
migration numbering in `notifications/migrations/` before naming it.

**Test layout convention:** find the existing test file(s) for `notifications/dispatch.py` and
`messaging/notifications.py` (search `tests/` for `_render_content`, `notify_message`, or
`NotificationPreference`) and extend them — do not create a parallel test module if one already covers
this code.

## PROGRESS CONTRACT
Emit `PLAN: <steps>` up front, then a single-line `PROGRESS: [<n>/<total>] <action>` before every
relevant action and `PROGRESS: [<n>/<total>] done` on completion, spaced so no gap exceeds ~2 min,
stdout unbuffered, and exactly one final `RESULT: DONE|BLOCKED <reason>`.
