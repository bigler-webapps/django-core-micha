# WORK ORDER DCM-AUTH-1 — Admin invite link valid for 30 days, password-reset link unchanged

Repo: `django-core-micha` (shared-core, trunk `main`). First consumer: Kira.

---

# A. Envelope — authored by the Expertenchat

## Goal & expected outcome

- Ziel: an admin-issued invite link (`/invite/<uid>/<token>/`, sent by `POST users/invite/` and
  shown by `GET users/<pk>/invite-link/`) stays valid for **30 days** instead of the current 3.
- Expected outcome: an invitee who opens the link on day 29 can still set a password and sign in;
  on day 31 the link is rejected. A password-reset link (`/reset/<uid>/<token>/`) keeps its
  current 3-day validity. Every consuming app gets the new lifetime by bumping its
  `django-core-micha` pin; no app-side code change is needed.

## Why

Today both links are minted by Django's `default_token_generator` and checked by the same
generator in `PasswordResetConfirmView`, so their lifetime is one shared value:
`PASSWORD_RESET_TIMEOUT`, which no app and not `settings_base.py` sets — the Django default of
3 days applies. Invitees regularly open the mail later than that (observed in Kira). Raising
`PASSWORD_RESET_TIMEOUT` would lengthen the reset link too, which the operator does not want.

## Decisions taken (operator, 2026-09-14)

- Invite lifetime **30 days**, configurable via a new dcm setting (default 30 days).
- Reset lifetime stays at Django's default 3 days. **The two lifetimes are independent**: a
  reset token must never be accepted through the longer invite window.
- Invite tokens and reset tokens are minted by **different generators** (own key salt), so a token
  of one kind is not valid as the other. Both are accepted by the existing confirm endpoint
  `users/password-reset/<uidb64>/<token>/`; the frontend (`ui-core-micha` `PasswordInvitePage`)
  is unchanged and still tells invite from reset by the URL path only.

## Scope

- `invitations/mixins.py` — `_build_frontend_url` mints the invite token (`is_new_user=True`)
  with the invite generator; the reset branch keeps `default_token_generator`.
- `invitations/views.py` — `PasswordResetConfirmView.get`/`.post` accept a token valid under
  either generator; everything else in the view (password validation, `EmailAddress` marking)
  unchanged.
- A new setting in `settings/settings_base.py` for the invite lifetime, ENV-overridable in the
  same style as `RECOVERY_REQUEST_TTL_MINUTES`, default 30 days. Document it in the same place the
  other auth settings are documented.
- `emails/email_texts.py` — the three `INVITE_BODY` texts (en/de/fr) gain one sentence stating
  how long the link is valid, rendered from the configured value (never a hardcoded "30"), in
  the wording style of `PENDING_REGISTRATION_BODY`.

## Explicit non-goals / do-not-touch

- The password-reset lifetime and `RESET_BODY` texts.
- The self-signup pending-registration link (`auth/views.py`, `send_pending_registration_email`,
  24 h, signed `pending_token`) — a different flow, untouched.
- The QR-signup token, access codes, recovery requests, throttles, permissions
  (`has_invite_admin_rights`, `IsInviteAdminOrSuperuser`), audit events.
- `ui-core-micha` — no frontend change. Any consuming app — the pin bump is a separate WO per app.
- No new endpoint, no schema/migration, no dependency change.

## Tier · precondition / gate

- **Tier 3** — auth surface inside shared-core (token lifetime governs first-time access to every
  consuming app).
- No precondition. Publishes as a new dcm release; consumption is per-app and separately ordered.

## Risks

- **Lengthening the wrong link.** If the confirm view simply tries both generators and the
  invite generator's timeout is applied to a `default_token_generator` token, the reset link
  silently becomes 30 days. The key-salt separation is what prevents that; the tests below pin
  it.
- **A subclass that changes nothing.** Django's `PasswordResetTokenGenerator.check_token` reads
  `settings.PASSWORD_RESET_TIMEOUT` directly inside the method; a subclass that only sets an
  attribute or overrides `_make_token_with_timestamp` does not change the lifetime. The
  30-day/31-day tests exist to catch exactly this.
- **Why 30 days is acceptable.** The token hash covers the user's password hash, `last_login`
  and email, so an invite token dies the moment the invitee sets a password or signs in, and an
  admin can revoke by changing the account's email. A long window widens exposure only for
  never-used invites in a mailbox.
- Blast radius: every app that bumps its pin. The behaviour change is additive (longer invite
  window, new setting with a default); nothing existing is renamed or removed.

## Required tests to WRITE (you write them and run YOUR OWN new ones; the orchestrator's run is the gate)

New test module under `tests/`, using the existing `User` fixtures/DB setup, driving the real
`_build_frontend_url` + `PasswordResetConfirmView` (request factory or API client), with time
controlled by patching the generator's `_now`:

1. An invite link is accepted by `GET users/password-reset/<uid>/<token>/` at +29 days and
   rejected at +31 days.
2. A reset link is accepted at +2 days and **rejected at +4 days** — proves the reset lifetime is
   still 3 days and is not widened by the confirm view accepting invite tokens.
3. An invite token minted at T is rejected after the user sets a password (the natural
   invalidation the 30-day window relies on).
4. The invite lifetime setting is honoured when overridden (e.g. `override_settings` to 1 day →
   rejected at +2 days).
5. `render_invite_email` output contains the configured lifetime in all three languages and no
   longer differs by hardcoded numbers.

Not to write, only run by the orchestrator: the existing auth-related set
(`pytest tests/ -k "auth or invite or reset or throttle"`), including `test_auth_throttles.py`
and `test_authn_audit.py`, to confirm nothing around the confirm view or the invite endpoints
moved.

---

# B. Implementation map — filled by the Orchestrator — ADDRESSED TO THE IMPLEMENTER

## Context package

### Files to change

- **NEW** `src/django_core_micha/invitations/tokens.py` — the invite token generator.
- `src/django_core_micha/invitations/mixins.py:63-73` (`_build_frontend_url`) — mint with the new
  generator when `is_new_user`.
- `src/django_core_micha/invitations/views.py:130-177` (`PasswordResetConfirmView.get`/`.post`) —
  accept either generator.
- `src/django_core_micha/settings/settings_base.py:368-373` — new setting, next to
  `RECOVERY_REQUEST_TTL_MINUTES`.
- `src/django_core_micha/emails/email_texts.py:82-110` (`INVITE_SUBJECT`/`INVITE_BODY`,
  `render_invite_email`) — add the lifetime sentence.
- **NEW** test module, e.g. `tests/test_invite_link_lifetime.py`.

### 1. New generator — `invitations/tokens.py`

`django.contrib.auth.tokens.PasswordResetTokenGenerator.check_token` (Django 6.1, the version
installed here — read `site-packages/django/contrib/auth/tokens.py` yourself if it differs) reads
`settings.PASSWORD_RESET_TIMEOUT` **directly inside the method body**:

```python
def check_token(self, user, token):
    if not (user and token):
        return False
    try:
        ts_b36, _ = token.split("-")
    except ValueError:
        return False
    try:
        ts = base36_to_int(ts_b36)
    except ValueError:
        return False
    for secret in [self.secret, *self.secret_fallbacks]:
        if constant_time_compare(self._make_token_with_timestamp(user, ts, secret), token):
            break
    else:
        return False
    if (self._num_seconds(self._now()) - ts) > settings.PASSWORD_RESET_TIMEOUT:
        return False
    return True
```

Overriding an attribute (`timeout = ...`) or `_make_token_with_timestamp` changes nothing — the
timeout line is hardcoded to the global setting. **You must override `check_token` itself**,
duplicating the method verbatim except the last comparison, which reads your own setting instead.
`make_token` needs no override (it doesn't consult the timeout).

**`key_salt` must differ from the base class's.** `_make_token_with_timestamp` folds
`self.key_salt` into the HMAC. If the invite generator kept the inherited salt, an actual
password-reset token (minted by `default_token_generator`, same salt) would produce the *same*
digest and would validate under the invite generator's `check_token` too — at which point it is
checked against the 30-day timeout instead of 3, silently widening the reset link. Give the new
class its own `key_salt` (e.g. the fully-qualified class name, Django's own convention) so a token
from one generator never verifies under the other regardless of timeout.

```python
from datetime import datetime
from django.conf import settings
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.utils.crypto import constant_time_compare
from django.utils.http import base36_to_int


class InviteTokenGenerator(PasswordResetTokenGenerator):
    """Own key_salt (so a reset token never verifies here) and own timeout
    (so an invite token gets the longer window without touching
    PASSWORD_RESET_TIMEOUT, which also governs the reset link).
    check_token is Django's PasswordResetTokenGenerator.check_token verbatim
    except the final comparison uses INVITE_LINK_TIMEOUT_DAYS instead of
    settings.PASSWORD_RESET_TIMEOUT — check_token reads that setting directly,
    so overriding _make_token_with_timestamp or an attribute would not work.
    """

    key_salt = "django_core_micha.invitations.tokens.InviteTokenGenerator"

    def check_token(self, user, token):
        if not (user and token):
            return False
        try:
            ts_b36, _ = token.split("-")
        except ValueError:
            return False
        try:
            ts = base36_to_int(ts_b36)
        except ValueError:
            return False
        for secret in [self.secret, *self.secret_fallbacks]:
            if constant_time_compare(self._make_token_with_timestamp(user, ts, secret), token):
                break
        else:
            return False
        timeout_days = getattr(settings, "INVITE_LINK_TIMEOUT_DAYS", 30)
        if (self._num_seconds(self._now()) - ts) > timeout_days * 86400:
            return False
        return True


invite_token_generator = InviteTokenGenerator()
```

### 2. `mixins.py::_build_frontend_url`

Mint with the invite generator only for the invite branch; the reset branch is untouched:

```python
def _build_frontend_url(self, request, user, *, is_new_user: bool) -> str:
    if is_new_user:
        token = invite_token_generator.make_token(user)
    else:
        token = default_token_generator.make_token(user)
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    ...
```

Add `from .tokens import invite_token_generator` to the imports.

### 3. `views.py::PasswordResetConfirmView`

Both `get` and `.post` currently call `default_token_generator.check_token(user, token)` once each
(lines ~151 and ~173). Replace both call sites with a small helper on the view so the "either
generator" rule lives in one place:

```python
def _token_is_valid(self, user, token):
    return default_token_generator.check_token(
        user, token
    ) or invite_token_generator.check_token(user, token)
```

Add `from django_core_micha.invitations.tokens import invite_token_generator`. Nothing else in the
view (password validation, `EmailAddress` marking, the `registration_completed` signal) changes.

### 4. Setting — `settings_base.py`

Next to `RECOVERY_REQUEST_TTL_MINUTES` (line 372):

```python
INVITE_LINK_TIMEOUT_DAYS = env("INVITE_LINK_TIMEOUT_DAYS", default=30)
```

Document it alongside the other auth settings the way `RECOVERY_REQUEST_TTL_MINUTES` is documented
(inline comment or the same doc block — match whatever convention is already there).

### 5. `emails/email_texts.py`

Add one sentence to `INVITE_BODY` (all three languages) stating the configured lifetime, in the
phrasing style `PENDING_REGISTRATION_BODY` already uses ("The link is valid for 24 hours:"). Do
**not** hardcode "30" — render it from the setting. Add a small helper next to
`get_project_name`/`get_preferred_language`:

```python
def get_invite_link_ttl_days() -> int:
    return getattr(settings, "INVITE_LINK_TIMEOUT_DAYS", 30)
```

`render_invite_email` passes `"ttl_days": get_invite_link_ttl_days()` in `ctx`, and each
`INVITE_BODY[lang]` template gains the sentence with a `{ttl_days}` placeholder, e.g. (en) "To set
your password and sign in for the first time, open the following link. The link is valid for
{ttl_days} days:\n{url}\n\n" — mirror the equivalent insertion in `de`/`fr`.

**Do not touch** `src/django_core_micha/emails/__init__.py` — it has its own copies of
`INVITE_BODY`/`PENDING_REGISTRATION_BODY`/`RESET_BODY` but nothing imports from it
(`invitations/emails.py` imports `email_texts`, not this module); it is pre-existing dead code,
out of scope for this WO.

### Pitfall — writing the tests: throttle scope isn't configured in test settings

`tests/settings.py` has **no `REST_FRAMEWORK` key at all** (see the docstring at the top of
`tests/test_auth_throttles.py` — this repo's test settings deliberately ship without the
`REST_FRAMEWORK` dict that `settings_base.py` defines). `PasswordResetConfirmView.throttle_classes
= [ScopedRateThrottle, AnonRateThrottle]` with `throttle_scope = "password_reset"`.
`ScopedRateThrottle.allow_request` calls `get_rate()` → `THROTTLE_RATES["password_reset"]`, and
DRF's own built-in default rates are only `{"user": None, "anon": None}` — no `"password_reset"`
key — so calling the view directly (RequestFactory + `as_view()`, or an API client) raises
`django.core.exceptions.ImproperlyConfigured: No default throttle rate set for 'password_reset'
scope`, not a 400/401. Wrap the calls that hit `PasswordResetConfirmView` in
`@override_settings(REST_FRAMEWORK={"DEFAULT_THROTTLE_RATES": {"password_reset": "1000/hour"}})`
(a partial `REST_FRAMEWORK` override is fine — DRF's `APISettings` falls back to its own built-in
defaults for every key you don't include). This is test-only setup, not a production change.

### Time control

Django's own generator has `_now(self)` returning `datetime.now()`, with the comment "Used for
mocking in tests" — patch `InviteTokenGenerator._now` / `default_token_generator.__class__._now`
(or `monkeypatch.setattr`) on the generator instance/class, not `django.utils.timezone.now`, to
move the clock for `+29`/`+31`/`+2`/`+4` day assertions.

### Invariants / do-not-touch (restating the Envelope)

- Reset lifetime and `RESET_BODY` stay untouched — the new test module's own regression test (#2 in
  Required tests) is what proves this, using the confirm view, not by reasoning about the code.
- No change to `auth/views.py`, the pending-registration flow, access codes, recovery, throttles
  config values, or permission classes.
- `ui-core-micha` unrelated — no frontend diff in this repo.

## Target repo working directory (absolute)

`C:\Users\biglmi\Documents\webapps\django-core-micha`

## Preamble

> The text above is the COMPLETE spec — the committed WO file's content, not a plan to refine; there
> is no separate plan file. Read the nearest `AGENTS.md`, the relevant `.codex/skills/<role>/SKILL.md`, and the
> app `MEMORY.md` ONLY for conventions. Stay in scope; do not touch auth/permissions/deps/schema/CI
> beyond what the spec names; do not update `MEMORY.md`. **Do NOT edit `WORK_ORDERS.md` — the register
> row and the review verdicts are the orchestrator's alone.** **Your tools are for editing source
> and test files and for running the tests you wrote — nothing else.** Do NOT install dependencies,
> touch a lockfile, run a package manager, or tidy up stray files; if something in the repo state
> blocks you, stop and report it as `RESULT: BLOCKED <reason>` instead of fixing it. Do NOT
> `git add`/`commit`/`push` — leave every
> change uncommitted in the working tree for the orchestrator's independent review. WRITE the tests
> the `Required tests` section calls for AND **RUN the tests you just wrote** to confirm they execute
> and pass — that is the ONLY test run you do (NOT the app's affected/full suite, NOT any review).
> The orchestrator re-runs the authoritative set + does the independent review after you finish —
> those are the gate; your own run does not count as the gate.
>
> Narrate continuously: a `PLAN: <step1> | <step2> | …` line up front, then a single-line
> `PROGRESS: [<n>/<total>] <present-tense action>` before every relevant action (and `… done` on
> completion), spaced so no gap exceeds ~2 min, stdout unbuffered, plus exactly one final
> `RESULT: DONE|BLOCKED <reason>`.

---

# C. Orchestrator only — NOT ADDRESSED TO THE IMPLEMENTER

> **If you are the implementer reading this work order as your own specification: STOP at this line.
> Everything below describes what the Orchestrator does AFTER you finish. You do none of it — no
> reviewers, no verification run, no register edit, no commit.** You ARE the invocation described
> below; do NOT shell out to `codex exec`.

## Execution directive

Implementer per `.claude/models.local.json` (`implementation`). For `runtime: codex`: `codex exec`
in the background via Bash with BOTH `--skip-git-repo-check` and
`--dangerously-bypass-approvals-and-sandbox`, never through the `*_coder` Agent wrappers. Check
`.claude/codex-status.md` (newest-first) for today's date before dispatching. Fallback to direct
Claude implementation only on Codex quota / rate-limit / non-zero exit — authorship then sits with
the Orchestrator and the independent review carries independence.

## Review routing

Tier 3: `reviewer` (all four lenses: envelope · regression · duplication · tests) + `sec_reviewer`,
concurrent, full context, one batch, before the commit. No `ui_reviewer` (no frontend diff).
The Orchestrator's own pass: confirm the reset path cannot be validated through the invite
generator (salt differs), the invite generator's timeout is actually applied in `check_token`,
and no new information leaks from the confirm view's responses.

## Verification

Authoritative run: the new test module + `pytest tests/ -k "auth or invite or reset or throttle"`.
No full suite (narrow, additive, no schema). No prototype, no E2E path (no `playwright.config.js`
in this repo).

## Register + commit + release

Row `DCM-AUTH-1` in `WORK_ORDERS.md` → `done` with the landing SHA and the review line in the
required shape. Version bump to **2.44.0** (new setting + changed default invite lifetime is a new
capability surface, not a patch), CHANGELOG entry naming the new setting and the 30-day default;
`publish.yml` runs on the push to `main`. Consumption: Kira's pin (`backend/requirements.txt`,
currently `2.43.2`) is bumped in a separate Kira WO (`KIRA-DEP-*`) once 2.44.0 is on PyPI; tier
that bump by the 2.43.2 → 2.44.0 diff, not by assumption.
