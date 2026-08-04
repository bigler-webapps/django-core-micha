# WORK ORDER MSG-13 (django-core-micha) — push notifications display the raw i18n key

**EXECUTION DIRECTIVE.** If you are the implementer reading this as your own spec, this section is not
addressed to you — it tells the Orchestrator how to invoke you; you ARE that invocation, do not shell
out to `codex exec`. Orchestrator: implement through `codex exec` in the background, invoked **directly
via Bash** (never the `debugger`/`*_coder` Agent wrappers) with **both** flags `--skip-git-repo-check`
and `--dangerously-bypass-approvals-and-sandbox`, prompt passed as a positional argument from this
file. Fall back to direct Claude implementation only on Codex quota / rate-limit / non-zero exit.

Feature-level WO (Expertenchat envelope). Operator report 2026-08-04 with a screenshot from staging: a
browser push notification whose **title and body both read `messaging.new_message`**.

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

**C. Useful `params` — and this is a PRIVACY decision, not a UX one.**

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

The operator was told, and accepted, what this means: the message body becomes visible on a locked
device without authentication, and transits a third-party push service. **Note explicitly for the
record** that jg encrypts message bodies at rest under a Fernet ring — this decision deliberately
places the same content outside that boundary for the push channel. `sec_reviewer` should confirm the
implementation matches this ruling, not re-litigate the ruling itself.

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

**D. A missing catalogue entry must not be silent.** The `except` branches were written to log a
fallback but cannot fire for the actual failure mode. Detect the real case — a rendered string identical
to its key is the cheap signal — and log it at warning level with the key. Without this, the next
missing key ships to users exactly as this one did.

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

**Non-vacuity:** test 1 must fail today; test 5's truncation assertion must fail if the limit is removed; test 6 must fail if the participation check is dropped.

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
> text**, having been told it makes the body readable on a locked device and puts it through a
> third-party service, outside the Fernet boundary that protects it at rest. Implement that; do not
> narrow it yourself and do not build a preview toggle. `sec_reviewer` confirms the implementation
> matches the ruling — truncation, non-text fallbacks, no resurrection of soft-deleted text, and no
> cross-recipient leakage — rather than re-litigating the ruling.
>
> Fix **all** emitted keys, not just this one, or the next report is identical.

## PROGRESS CONTRACT
Emit `PLAN: <steps>` up front, then a single-line `PROGRESS: [<n>/<total>] <action>` before every
relevant action and `PROGRESS: [<n>/<total>] done` on completion, spaced so no gap exceeds ~2 min,
stdout unbuffered, and exactly one final `RESULT: DONE|BLOCKED <reason>`.
