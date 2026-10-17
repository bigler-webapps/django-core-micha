# DCM-AUTH-6 — Social auto-connect only for provider-verified addresses (closes S264)

# A. Envelope

**Goal.** Close the P1 finding S264 from the 2026-10-17 estate audit
(`webapp-management/SECURITY_FINDINGS.md`). Expected outcome: a social login can only be linked to an
existing local account automatically when the identity provider itself vouches for the e-mail address, so a
Microsoft identity with an attacker-chosen `mail` attribute can no longer take over an account.

**Background (from the finding).** The `pre_social_login` receiver `force_auto_connect_on_email_match`
(`auth/signals.py`) takes `sociallogin.email_addresses[0].email`, finds the local user by e-mail and connects
whenever the *local* address is verified — it never checks whether the *provider* verified it. allauth's
Microsoft provider never marks addresses verified (`mail` / `userPrincipalName`), and no `TENANT` is set
(allauth default `common`; many `project.yaml` files declare `MICROSOFT_TENANT_ID: common`). allauth's own
`SOCIALACCOUNT_EMAIL_AUTHENTICATION` (enabled in `settings/settings_base.py`) already matches verified
addresses only; the dcm receiver bypasses it.

**Scope (operator decision 2026-10-17: verified addresses only, no tenant pinning).**
1. Automatic linking of a social login to an existing local account happens only when the provider marks
   the address as verified **and** the local address is verified. Google logins keep auto-linking as today.
   Microsoft logins no longer auto-link to existing accounts.
2. Social accounts that are already linked keep logging in unchanged.
3. A social login whose (unverified) address matches an existing local account neither logs that account
   in nor creates a second account with the same e-mail; the request ends in allauth's normal refusal path
   for that situation (no silent link, no duplicate user).
4. Release: minor version bump with a CHANGELOG entry stating the behaviour change (Microsoft no longer
   auto-links existing accounts; users link Microsoft by logging in first).

**Non-goals / do not touch.** Tenant pinning or tenant allowlists; a new "connect social account" UI;
admin MFA (S267); invite and user-admin rules (DCM-AUTH-5); app pin bumps (separate per-app work orders).

**Risks.** Existing users who relied on Microsoft auto-linking at first social login can no longer do so —
they must log in with their password (or an already linked provider) first; the refusal path must not
reveal whether an account with that e-mail exists beyond what allauth already reveals.

**Required tests (write as part of the implementation).**
- Microsoft social login with an unverified address equal to a verified local account → no SocialAccount
  created, not logged in as that user, no duplicate user.
- Google social login with a verified address equal to a verified local account → linked and logged in
  (unchanged).
- Local address unverified → no link (unchanged behaviour).
- Already linked Microsoft and Google accounts → log in unchanged.

**Target repo.** `django-core-micha`, branch `main`. **Tier 3** (change inside shared core, authentication).

# B. Implementation map — ADDRESSED TO THE IMPLEMENTER

Work from this package; do not explore broadly. Part A is the authoritative spec; this map fixes names, shapes and the two places that must change together. One dispatch; complete, no stubs. This is an authentication change in shared core: when in doubt, do NOT link.

### Target repo working directory (absolute)

`C:\Users\Micha Bigler\Documents\webapps\django-core-micha` (branch `main`). Never the workspace root. Do not touch other repositories, `pyproject.toml`, `CHANGELOG.md`, `docs/`, `WORK_ORDERS.md` (version, changelog and register are the Orchestrator's), `settings/settings_base.py`. Run tests with the interpreter named in your dispatch note.

### Context package

- `src/django_core_micha/auth/signals.py`, receiver `force_auto_connect_on_email_match(sender, request, sociallogin, **kwargs)` on allauth's `pre_social_login` (about line 277): returns when `sociallogin.is_existing` or there are no `sociallogin.email_addresses`; takes `sociallogin.email_addresses[0].email`, finds the local `User` by `email__iexact`, checks that a local `EmailAddress` for that user and address is `verified=True`, and then calls `sociallogin.connect(request, user)`. It never looks at `sociallogin.email_addresses[0].verified`, the PROVIDER's verdict (allauth's Microsoft provider never marks addresses verified; Google does when the provider says so).
- `src/django_core_micha/auth/adapters.py`, `InvitationOnlySocialAdapter.is_open_for_signup(self, request, sociallogin)` (about line 14): returns `True` when a local user with `sociallogin.user.email` exists AND a local verified `EmailAddress` for it exists, `False` when there is no such user or no email. When the receiver does not connect an unverified-by-provider login, this method still answers True for the matching account, so allauth's social signup would continue for an address that belongs to an existing account (a second account or the allauth e-mail-conflict form) instead of a refusal. Both places must apply the same rule.
- `settings/settings_base.py`: `SOCIALACCOUNT_ADAPTER = InvitationOnlySocialAdapter`, `SOCIALACCOUNT_EMAIL_AUTHENTICATION = True` and `..._AUTO_CONNECT = True` (allauth's own matching only for provider-verified addresses), `SOCIALACCOUNT_AUTO_SIGNUP = True`, providers google (`EMAIL_AUTHENTICATION: True`) and microsoft. Not changed by this order.
- allauth objects: `allauth.socialaccount.models.SocialLogin(user=, account=SocialAccount(provider=, uid=, extra_data=), email_addresses=[EmailAddress(email=, verified=, primary=)])` (unsaved instances; `is_existing` is true when `user.pk` is set / the account is saved); `allauth.account.models.EmailAddress` is the local table. `pre_social_login` is `allauth.socialaccount.signals.pre_social_login`.
- Tests to model on: look at `tests/test_authn_audit.py` for how social settings and the auth stack are set up in this repo's test settings (`tests/settings.py` already lists `allauth.socialaccount`); the receiver and the adapter can be tested directly without the HTTP flow.

### Fixed design

1. **One helper** in `auth/signals.py` or a small shared module next to it (your choice, imported by both places): `provider_vouched_email(sociallogin)` returns the address (string) of the FIRST `sociallogin.email_addresses` entry whose `verified` flag is true, or `None`. Only addresses the provider itself marked verified can ever lead to a link. The existing `[0]`-only logic is replaced by this helper (a provider that lists a verified second address is honoured; an unverified first address is never used).
2. **Receiver:** `force_auto_connect_on_email_match` first returns without any action when `sociallogin.is_existing` (already linked accounts log in unchanged), when there is no provider-verified address, or when no local user has that address. With a provider-verified address and a matching local user, it still requires the local `EmailAddress` for that user and address to be `verified=True` (unchanged) and then connects as today. In every non-connect case it logs a warning that names only the provider and the reason code, not the e-mail address (keep the existing wording style but do not log the address for the unverified-provider case).
3. **Adapter:** `InvitationOnlySocialAdapter.is_open_for_signup` uses the same helper: it returns True only when the provider-verified address matches a local user AND the local address is verified (so Google's flow is unchanged); in every other case it returns False — including an UNVERIFIED provider address (Microsoft) that matches an existing local account, so allauth ends in its normal "signup closed" refusal path instead of creating a second account; the no-match and no-email cases stay False. The method must never create, modify or link anything.
4. **No other change:** the tenant, provider settings, allauth settings, the pending-registration flow, invite and user-admin rules, and already-linked login (`is_existing`) stay as they are. Do not add settings. Do not change what is revealed to the browser beyond what allauth already reveals.

### Tests (new file `tests/test_social_auto_connect.py`; existing tests stay green)

Test the receiver by sending `pre_social_login` (or calling the function) with unsaved `SocialLogin` objects and the adapter method directly, using real `User`, `EmailAddress` and `SocialAccount` rows. Cases from Part A: (1) Microsoft-style login (provider address `verified=False`) whose address equals a verified local account: after the receiver and the adapter ran, no `SocialAccount` exists for it, the sociallogin is not connected to that user (`sociallogin.user` is not the local user / `is_existing` false), the number of users is unchanged (no duplicate), and `is_open_for_signup` is False; (2) Google-style login (provider address `verified=True`) equal to a verified local account: connected (the `SocialAccount` is created for that user by `connect`, a request object may be a `RequestFactory` request with the session/messages middleware not required: if `connect` needs a request attribute, provide what it needs) and `is_open_for_signup` True; (3) provider-verified address but the LOCAL address is unverified: no link and `is_open_for_signup` False (unchanged behaviour); (4) already linked accounts: a saved Microsoft `SocialAccount` and a saved Google one attached to a local user: the receiver does nothing and the account stays linked (no second row, no change); (5) a provider that lists an unverified first address and a verified second one: only the verified second address is used; (6) no e-mail at all and no matching user: nothing happens, no exception; (7) case differences in the address (`USER@Example.com` against `user@example.com`) still match for a provider-verified login and still do NOT match for an unverified one; (8) the warning log for the unverified-provider case does not contain the e-mail address (caplog). Prove each refusal leaves the database unchanged (counts of `User`, `EmailAddress`, `SocialAccount`).

### Invariants

A social login is linked to an existing local account only when the provider vouches for the address and the local address is verified; Microsoft logins never auto-link; no duplicate account is ever created for a matching unverified address; already linked accounts log in unchanged; the refusal reveals nothing beyond what allauth already reveals.

### Preamble — REQUIRED block

> The text above is the COMPLETE spec — the committed WO file's content, not a plan to refine; there
> is no separate plan file. Read the nearest `AGENTS.md` and the app `MEMORY.md` ONLY for conventions.
> Stay in scope; do not touch auth/permissions/deps/schema/CI unless the spec says so; do not update
> `MEMORY.md`. **Do NOT edit `WORK_ORDERS.md` — the register row and the review verdicts are the
> orchestrator's alone.** **Your tools are for editing source and test files and for running the tests
> you wrote — nothing else.** Do NOT install dependencies, touch a lockfile, run a package manager, or
> tidy up stray files; if something in the repo state blocks you, stop and report it as
> `RESULT: BLOCKED <reason>` instead of fixing it. Do NOT `git add`/`commit`/`push` — leave every
> change uncommitted in the working tree for the orchestrator's independent review. WRITE the tests
> the `Required tests` section calls for AND **RUN the tests you just wrote** to confirm they execute
> and pass — that is the ONLY test run you do (NOT the affected/full suite, NOT any review). The
> orchestrator re-runs the authoritative set + does the independent review after you finish.
>
> Narrate continuously: a `PLAN: <step1> | <step2> | …` line up front, then a single-line
> `PROGRESS: [<n>/<total>] <present-tense action>` before every relevant action (and `… done` on
> completion), spaced so no gap exceeds ~2 min, stdout unbuffered, plus exactly one final
> `RESULT: DONE|BLOCKED <reason>`. Use explicit UTF-8 for all file I/O.

# C. Orchestrator only — NOT ADDRESSED TO THE IMPLEMENTER

> Implementer: STOP here. Integration, verification, reviews and git belong to the Orchestrator.

Source: night audit 2026-10-17, S264; requirements round in session
`local_4987837b-7646-4b63-b3ab-1fd970f129bd`. Report back to: `local_4987837b-7646-4b63-b3ab-1fd970f129bd`.
