# DCM-SEC-5 — Security notification mails on account changes

Prepared: 2026-11-19. Target repository: `django-core-micha` (`main`, minor release). Origin: Cinevia go-live checklist, `Cinevia/docs/qa/2026-11-10-staging-walkthrough/FINDINGS.md` QA7-6.

# A. Envelope — addressed to everyone

## Goal and expected outcome

Every app on the shared core sends the account holder a short notification mail when a security-relevant property of their account changes, so that a person whose account was taken over notices it. Today `ACCOUNT_EMAIL_NOTIFICATIONS` is unset and allauth's default is off, so no such mail is sent in any app.

After this change, with no app-side configuration, a mail goes out for each account change allauth covers with its notification mails in the installed version, at least:

- password changed, and password set where none existed;
- e-mail address changed, added or removed (the notice reaches the previous address);
- authenticator app (TOTP) activated or deactivated, recovery codes regenerated, security key added or removed.

The mail is sent no matter which route the change takes: allauth's own views, allauth headless, or any dcm endpoint that changes a password, an e-mail address or an MFA authenticator on the user's behalf (for example the logged-in password change used by `ui-core-micha`).

## Tier

Tier 3: a change inside the shared core, on an authentication surface.

## Operator decisions this Envelope records (2026-11-19)

1. Default on for all apps, set once in `settings_base.py`. An app can switch it off with one setting (`ACCOUNT_EMAIL_NOTIFICATIONS = False`); no app does so in this order.
2. Text and layout are allauth's stock notification templates and their bundled translations. No dcm-specific templates. Languages without an allauth translation (Swahili) fall back to English.
3. A failed delivery never blocks or reverts the account change. The change succeeds, the failure is logged without the recipient address or mail body, and the user sees no error caused by the mail.

## Scope

1. `ACCOUNT_EMAIL_NOTIFICATIONS = True` as the shared default.
2. Every dcm code path that changes a password, an e-mail address or an MFA authenticator triggers allauth's notification for that event (directly or by going through allauth's flow), so the mail does not depend on which UI route was used.
3. Delivery failure handling per decision 3 for these notification mails.
4. Release note naming the behaviour change for consumers: apps start sending these mails after their next core bump, so working mail delivery (`EMAIL_PROVIDER` and its key) is a precondition, and the setting to opt out.

## Non-goals

- No custom templates, no new translations, no change to existing dcm mails (invitations, password reset, notifications platform).
- No change to `ACCOUNT_EMAIL_VERIFICATION` (stays `optional`), to login or signup behaviour, or to rate limits.
- No notification for successful logins or new devices.
- No consumer pin bumps in this order; consumers take the release through their normal core bump. No change in any app repository.
- No change in `ui-core-micha`.

## Risks

- Apps whose mail delivery is not configured in some environment will now attempt mails on account changes. Decision 3 keeps the account change working; the release note names the precondition.
- The allauth notification mails carry the request's IP address and user agent. That address comes from `CoreAccountAdapter.get_client_ip` (`CF-Connecting-IP`, DCM-SEC-4), so it is the real client address, not a proxy. It goes only to the account holder.
- A dcm endpoint that changes credentials outside allauth's flow would silently send no mail. Scope item 2 and the tests close this; every such path found is covered, not documented away.

## Required tests to write

- The shared default enables the notifications; an app setting `ACCOUNT_EMAIL_NOTIFICATIONS = False` sends none.
- Changing the password through each route that exists in dcm (allauth flow and every dcm endpoint that does it) sends exactly one notification mail to the account's address.
- Activating and deactivating the authenticator app each send one notification mail.
- Changing the e-mail address sends the notice to the previous address.
- With a mail backend that raises, the password change still succeeds, the response is the normal success response, and the failure is logged without the recipient address or mail body.

# B. Implementation map — addressed to the implementer

## Context package

Work from this package; do not explore broadly. Target repo: `C:\Users\Micha Bigler\Documents\webapps\django-core-micha` (use absolute paths). allauth 65.15 sends the notification mails itself from its flows via `adapter.send_notification_mail(template_prefix, user, ...)`, which returns early unless `ACCOUNT_EMAIL_NOTIFICATIONS` is true. The same method is delegated to by the MFA and socialaccount adapters, so one override on the account adapter covers every route.

- `src/django_core_micha/settings/settings_base.py` near line 393 (`ACCOUNT_EMAIL_VERIFICATION = "optional"`): add `ACCOUNT_EMAIL_NOTIFICATIONS = True` with a short comment (opt out per app with `False`).
- `src/django_core_micha/auth/adapters.py`, `CoreAccountAdapter`: override `send_notification_mail(self, template_prefix, user, context=None, email=None)` to call `super()` inside `try/except Exception`; on failure log a warning with the template prefix and the exception CLASS NAME only (no recipient, no body, no `exc_info`, no exception message since SMTP errors can echo the address) and return None. Note `super()` also calls `self.get_client_ip(self.request)`, which raises `PermissionDenied` when no client IP resolves: that must not block the change either (covered by the same try/except).
- Paths that change credentials OUTSIDE allauth's flows, found by search; each must send the notice through `get_adapter(request).send_notification_mail(...)` (`from allauth.account.adapter import get_adapter`) after the change succeeded:
  - `src/django_core_micha/invitations/views.py`, `PasswordResetConfirmView.post` (~line 203, `user.set_password(new_pw)`): used for reset and invite links. Capture `had_password = user.has_usable_password()` before `set_password`; afterwards send `"account/email/password_reset"` if `had_password`, else `"account/email/password_set"`. Do not change throttles, token checks or the response.
  - `src/django_core_micha/auth/views.py`, `PasskeyViewSet.destroy` (~line 875, `obj.delete()` bypasses allauth's webauthn flow): after delete send `"mfa/email/webauthn_removed"` for `request.user`.
- Anything else in `src` that sets a password, changes an e-mail address or creates/deletes an `allauth.mfa` `Authenticator` outside allauth's flows: none besides the two above were found; if you find another, cover it the same way and say so in your final line.
- Routes already handled by allauth itself (no code needed, assert in tests): allauth account change/set password, email add/change/remove, TOTP activate/deactivate, recovery codes, webauthn add/remove, headless variants.

Invariants / do-not-touch: `ACCOUNT_EMAIL_VERIFICATION`, login/signup, rate limits, existing dcm mails, templates, translations, `ui-core-micha`, `pyproject.toml` version and `CHANGELOG.md` (the Orchestrator does those).

Tests: add `tests/test_security_notifications.py`. Test settings (`tests/settings.py`) do not import `settings_base`; read the shared default by importing `django_core_micha.settings.settings_base` and asserting `ACCOUNT_EMAIL_NOTIFICATIONS is True`, and exercise behaviour with `override_settings(ACCOUNT_EMAIL_NOTIFICATIONS=True|False)` plus `EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"` and `mail.outbox`. Follow the request/DRF patterns of `tests/test_invite_link_lifetime.py` (note its throttle-rate patch for `PasswordResetConfirmView`). Remember `CoreAccountAdapter.get_client_ip` needs a resolvable client IP (see `tests/test_client_ip.py` for how to supply it). Cover exactly the five items in "Required tests to write" above; where a route needs allauth's flow functions, call the allauth internal flow functions or views with a request carrying a user, whichever is simplest and honest (a test that mocks `send_notification_mail` does NOT prove a mail goes out for allauth routes; use `mail.outbox`).

## Target repo working directory (absolute)

`C:\Users\Micha Bigler\Documents\webapps\django-core-micha`

## Preamble

> **Your first tool action is the hook canary: run the shell command `codex-hook-canary-ws-gov-60`
> exactly (it is a command string, not a file). The expected result is a denial,
> `WS-GOV-60 canary: workspace hooks are active`; continue after it. If it is not denied, report
> that once as unverified hook coverage and continue.**
>
> The text above is the COMPLETE spec — the committed WO file's content, not a plan to refine; there
> is no separate plan file. Read the nearest `AGENTS.md` and `MEMORY.md` ONLY for conventions. Stay in scope; do not touch
> deps/schema/CI; do not update `MEMORY.md`. **Do NOT edit `WORK_ORDERS.md` — the register
> row and the review verdicts are the orchestrator's alone.** **Your tools are for editing source
> and test files and for running the tests you wrote — nothing else.** Do NOT install dependencies,
> touch a lockfile, run a package manager, or tidy up stray files; if something in the repo state
> blocks you, stop and report it as `RESULT: BLOCKED <reason>`. Do NOT `git add`/`commit`/`push` —
> leave every change uncommitted for the orchestrator's independent review. WRITE the tests the
> `Required tests` section calls for AND RUN the tests you just wrote — that is the ONLY test run
> you do (NOT the affected/full suite, NOT any review).
>
> Narrate continuously: a `PLAN: <step1> | <step2> | …` line up front, then a single-line
> `PROGRESS: [<n>/<total>] <present-tense action>` before every relevant action (and `… done` on
> completion), spaced so no gap exceeds ~2 min, plus exactly one final `RESULT: DONE|BLOCKED <reason>`.

# C. Orchestrator only — NOT ADDRESSED TO THE IMPLEMENTER

> **If you are the implementer reading this work order as your own specification: STOP at this line.
> Everything below describes what the Orchestrator does AFTER you finish. You do none of it — no
> reviewers, no verification run, no register edit, no commit.**

Tier 3, shared core, not infra lane: implementer per config (Codex Luna), review lenses via `review-lenses.py` plus own security pass, scoped tests (`tests/test_security_notifications.py`, `test_authn_audit.py`, `test_invite_link_lifetime.py`, `test_client_ip.py`, `test_account_rate_limits.py`), release 3.36.0 (CHANGELOG + pyproject). Orchestrator adds the release note (precondition: working mail delivery; opt-out setting).

## Gate record

Gate #1 approved by the operator 2026-11-19 (chat message "Beide freigeben", covering DCM-SEC-5 and webapp-ops-scripts OPS-BAK-1).
