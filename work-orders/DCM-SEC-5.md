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

To be filled by the Orchestrator.

# C. Orchestrator only — NOT ADDRESSED TO THE IMPLEMENTER

To be filled by the Orchestrator.

## Gate record
