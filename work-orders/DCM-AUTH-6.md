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

_To be filled by the Orchestrator (context package, preamble, progress contract) before dispatch._

# C. Orchestrator only — NOT ADDRESSED TO THE IMPLEMENTER

> Implementer: STOP here. Integration, verification, reviews and git belong to the Orchestrator.

Source: night audit 2026-10-17, S264; requirements round in session
`local_4987837b-7646-4b63-b3ab-1fd970f129bd`. Report back to: `local_4987837b-7646-4b63-b3ab-1fd970f129bd`.
