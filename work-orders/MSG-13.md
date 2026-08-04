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
**`sec_reviewer` mandatory for scope C** — it decides whether message content leaves the encrypted
domain.

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

A push notification is delivered through a **third-party push service** and rendered by the operating
system, typically on a lock screen. Putting message content into it means that content:

- leaves the encrypted domain (messaging bodies are encrypted at rest via the app's Fernet ring),
- transits a third party,
- is displayed without authentication to anyone holding the device.

**`sec_reviewer` must rule on what may appear**, and this WO does not pre-empt it. The realistic
options, from safest: sender name only · sender + conversation name · sender + excerpt. **Do not add an
excerpt on the implementer's own judgement.** If the ruling is "no content at all", then scope B's body
must work without one, and that is a fine outcome.

Whatever is chosen must be **per-recipient safe**: `_render_content` already runs under
`translation.override(_recipient_language(user))`, so the same content dict is rendered per recipient —
verify nothing recipient-specific leaks across recipients.

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
- Do not add message content to the payload without the `sec_reviewer` ruling from scope C.

## RISKS
- **Scope C is the one that can do lasting harm.** An excerpt in a push cannot be recalled and is
  visible on a locked device. Err toward less.
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
5. Scope C, once ruled: the payload contains exactly what was approved and **nothing more** — assert the
   absence of message body text if the ruling excludes it. This is the privacy line; assert it
   explicitly rather than trusting the implementation.

**Non-vacuity:** test 1 must fail today, and test 5 must fail if the excluded field is added back.

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
> **Scope C is a privacy decision and `sec_reviewer` rules on it, not the implementer:** a push goes
> through a third-party service and renders on a lock screen, so putting message content there takes it
> out of the encrypted domain and shows it without authentication. Do not add an excerpt on your own
> judgement. And fix **all** emitted keys, not just this one, or the next report is identical.

## PROGRESS CONTRACT
Emit `PLAN: <steps>` up front, then a single-line `PROGRESS: [<n>/<total>] <action>` before every
relevant action and `PROGRESS: [<n>/<total>] done` on completion, spaced so no gap exceeds ~2 min,
stdout unbuffered, and exactly one final `RESULT: DONE|BLOCKED <reason>`.
