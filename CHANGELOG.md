# Changelog

## [3.15.0] — 2026-10-18

### Security

DCM-AUTH-11 (closes S246): the generic `POST /api/users/` on `BaseUserViewSet` creates an account only for superusers and users admins. A new overridable method
`BaseUserViewSet.can_create_user(request)` decides (default: `can_view_users_admin(user, request=request)`, request-aware, so the admin auth policy and its session
factor apply); a consumer may widen or narrow it. The check runs before the payload is read: every other authenticated user gets 403 and no row is written, for a valid,
empty or malformed body alike. A permitted create without username and e-mail (also a non-object JSON body) answers 400 with field errors and writes nothing, instead of the
former unhandled 500 on the unique username. Consumer guards that exist today (innoservice's `perform_create`, bigler-consult's `get_permissions`) keep working; a
consumer whose create actors are not users admins in dcm's sense (for example holders of a capability below `INVITE_MIN_ROLE_LEVEL`) must override `can_create_user`.

### Removed

DCM-AUTH-11 (closes the remainder of S251): `auth.permissions.RequireStrongSecurity` and `auth.security.require_security_level` are removed. Both did nothing unless
`SECURITY_ENFORCE_STRONG_AUTH` was set and had no call site in dcm or in any of the 19 consuming apps (measured 2026-10-18). `SECURITY_ENFORCE_STRONG_AUTH` stays (it
still decides the default factor count in `auth/policy.py`); the admin auth policy (`admin_required_auth_factor_count`) is unaffected and effective.

## [3.14.0] — 2026-10-17

### Security

Three authorization and authentication adjustments from the 2026-10-17 audit, shipped together (DCM-AUTH-8, -9, -10).

**DCM-AUTH-8 (closes S249): auth-policy write threshold without a role hierarchy.** The computed default of `AUTH_POLICY_WRITE_MIN_ROLE_LEVEL` now lies above the
level a user without an explicit role has (the level of `DEFAULT_ROLE_CODE`). When the role definitions offer no level above it (no `ROLE_DEFINITIONS`, a single level,
all levels equal) the default is `max(ROLE_LEVEL_3, default level + 1)`; a real hierarchy keeps its highest level; an unknown default role code is treated as level 0
for a genuine hierarchy and as the highest defined level for a flat setup (a misconfiguration never makes a flat setup look like a hierarchy); an explicit setting
still wins in both directions. **Consumer effect:** in an app without a role hierarchy only superusers (and an explicit setting) hold full auth-admin rights, hence
invite-admin, users-admin and the auth-policy write; the stopgap `AUTH_POLICY_WRITE_MIN_ROLE_LEVEL = 3` in Gustav, Cinevia and Photogallery becomes redundant.

**DCM-AUTH-9 (adjusts DCM-AUTH-5, S266, S276): the admin tier may act on equal rank.** `can_act_on_user(actor, target)`: a superuser acts on anyone, nobody else on a superuser;
otherwise the actor acts when its level is greater, or equal and the actor holds full auth-admin rights (the admin tier, base `has_full_auth_admin_rights`, after
DCM-AUTH-8). Invite admins below the tier (teachers, team, supervisors) keep the strict rule from 3.9.0. Invite links stay limited to accounts that never logged in.
**Consumer effect:** admin to admin is allowed again; teacher to teacher stays refused; nobody touches a higher rank.

**DCM-AUTH-10 (adjusts DCM-AUTH-7, S267, S19): the admin MFA gate is opt-in per app.** `ADMIN_MFA_REQUIRED` now defaults to `False` (`settings_base` and the middleware's
fallback for a missing setting). With `ADMIN_MFA_REQUIRED = True` the full 3.11.0/3.13.0 gate applies unchanged (enrolled TOTP/WebAuthn, an MFA-authenticated session,
superusers and staff, prefixed and custom admin sites, `IS_LOCAL` bypass). The allauth-routed admin login (`auth/admin_login.py`) stays on for every app. **Consumer
effect:** an app that wants admin MFA must set `ADMIN_MFA_REQUIRED = True`; without it neither the session requirement nor the older S19 enrolment requirement applies.

## [3.13.0] — 2026-10-17

### Added

DCM-BILL-6: the account view `me/` carries token origins, the team pool and the seat payer, additively (no existing key changes). `tokens.grants[].origin`
is `plan` (a subscription or team allowance, purpose `plan:<key>`), `package` (a purchased package), `trial` (purpose `trial`) or `credit` (every other
grant); `source` is unchanged. `tokens.pool` (the key is always present) is `null` or `{plan_key, plan_name, amount, remaining, expires_at}` for the payer
of an ACTIVE team package and for a seat holder of one (the payer's own package wins when a person is both): the sum of the package account's grant and
purchase entries that have not expired, `remaining` may be 0, `expires_at` is the package end; it is never part of `tokens.grants` or `tokens.balance`,
and after the package ended it is `null`. `seat.payer_name` (seat holders only) is the package owner's full name, else the e-mail, else the username,
never the database id. Contract: `docs/design/billing.md`.

### Security

DCM-AUTH-7 follow-up (post-hoc independent review of the admin gate): the admin MFA gate also covers an admin mounted at another URL prefix (for example
`path("backend/admin/", admin.site.urls)`) or a custom `AdminSite`: besides the default `/admin/` prefix every URL that resolves to an application
namespace `admin` is gated; an unexpected resolver error fails closed, an absent URL conf means nothing admin is reachable. Place
`AdminMfaRequiredMiddleware` after any middleware that selects `request.urlconf`. This closes a bypass that existed before 3.11.0 as well.

## [3.12.0] — 2026-10-17

### Security

DCM-CHAT-14 (closes S434 of the 2026-10-17 audit): aborted assistant turns are charged, not released. In the paid API drivers
(`anthropic_api`, `openai_api`) a cost reservation is released only when the provider call did not start or the provider definitively rejected it
as a client error (HTTP 400-499 except 408, 409, 429). Every other abort after the call started (a `stop` frame, task cancellation, generator
close, timeout, connection loss, 5xx, 408/409/429) is charged at the reserved estimate to the daily cap (`settle_reservation`) and, when
`django_core_micha.billing` is installed, written once as an `AICostRecord` with purpose `chat_aborted` (shielded from the cancellation in
flight; a failing write never replaces the original error). A turn that stops early every time can no longer dodge the daily cap. The
reservation now uses the real output ceiling: new `max_tokens` keyword of `AnthropicDriver.stream` (default `DEFAULT_MAX_OUTPUT_TOKENS` = 8192, the
value that was already sent); the OpenAI request stays uncapped and reserves the same ceiling. New helpers in `assistant/drivers/base.py`:
`estimate_tokens`, `release_unstarted`, `charge_aborted`. Normal completion and the caps themselves are unchanged. **Behaviour change for
consumers:** users are charged (conservatively, the reserved amount) for turns they stop or that fail after the provider call started.

## [3.11.0] — 2026-10-17

### Security

DCM-AUTH-7 (closes S267 of the 2026-10-17 audit): the Django admin needs an MFA login. `AdminMfaRequiredMiddleware` now admits superusers AND
staff users to `/admin/` only when the user has an enrolled authenticator (TOTP or WebAuthn, recovery codes alone do not count) AND the current
session recorded an MFA authentication (`auth.admin_mfa_middleware.session_used_mfa`, read from allauth's authentication records; only `totp`
and `webauthn` count, unknown types fail closed). The gate follows the script prefix (`request.path_info`). The default `admin.site` login is
routed through allauth (`auth.admin_login.secure_default_admin_site`, wired in `CoreAuthConfig.ready()` when `django.contrib.admin` and
`allauth.account` are installed; `secure_admin_site(site)` for custom admin sites): an anonymous visit to the admin login is redirected to
`LOGIN_URL`, Django's own password form and its unthrottled `authenticate()` call no longer serve the admin. **Behaviour change for consumers:**
a session without an MFA login is refused at `/admin/` and the person must log in again through the normal allauth login with the second factor
(staff users included); tests that enter the admin with `force_login` must add an MFA record to the session or set `ADMIN_MFA_REQUIRED=False`.
`IS_LOCAL` and `ADMIN_MFA_REQUIRED=False` still bypass the gate.

## [3.10.0] — 2026-10-17

### Security

DCM-AUTH-6 (closes S264 of the 2026-10-17 audit): social login links to an existing local account automatically only for provider-verified
addresses. The `pre_social_login` receiver `force_auto_connect_on_email_match` and `InvitationOnlySocialAdapter.is_open_for_signup` now use one
helper, `auth.social.provider_vouched_email`, which returns the first address the identity provider itself marked verified; a login connects
only if that address matches a local user whose address is verified too. A login with an address the provider did not verify (allauth's
Microsoft provider never does, and no tenant is pinned) neither links to an existing account nor opens signup for it: allauth ends in its normal
"signup closed" refusal, no second account is created. Google keeps auto-linking, already linked accounts log in unchanged. The skip log names
the provider and a reason code, not the e-mail address. **Behaviour change for consumers:** users who relied on Microsoft auto-linking at their
first social login must log in with their password (or an already linked provider) first.

## [3.9.0] — 2026-10-17

### Security

DCM-AUTH-5 (closes S266 and S276 of the 2026-10-17 audit): rank check for invite links, user edits and deletes. New helper
`auth.permissions.can_act_on_user(actor, target)`: a superuser may act on anyone, nobody else on a superuser, otherwise the actor's role level
must be strictly greater than the target's (equal is refused). `BaseUserViewSet.update`/`partial_update`/`destroy` (for a target that is not the
requester) and `InviteActionsMixin.invite_link` apply it to users/invite admins: they are refused with 403 (`Auth.RANK_INSUFFICIENT` on the invite
link) and nothing changes. Invite links are only issued for accounts that never logged in (`last_login` empty), for everyone including
superusers; for any other account the answer is 409 `Auth.USE_PASSWORD_RESET` (the owner uses the self-service reset). **Behaviour change for
consumers:** invite admins lose the ability to edit, deactivate, delete or get an invite link for accounts of equal or higher rank; apps whose
invite admins are the top role can no longer manage peers of that level (only superusers can). Actors who are not users admins and reach other
rows through a consumer's widened `get_queryset()` (jg-ferien org admins, DCM-AUTH-3) are not affected: their power stays governed by the
consumer's own object permissions. Self-edit rules, role changes (`RolePolicy`) and queryset scoping are unchanged.

## [3.8.0] — 2026-10-17

### Added

DCM-RCPT-2: receipt reading sees the request context, and the fallback currency is per app. An extension can define `validate_context` and
`prompt_for`: the app-validated context (never the raw one) adds text to the prompt of that reading only, a refused context ends the reading
with `invalid_request` before the count, the file preparation and any provider call, and an extension with `prompt_for` but no validation is
refused. New setting `DCM_RECEIPTS_FALLBACK_CURRENCY` (default `CHF`, `none` leaves an unreadable currency empty, system check
`dcm_receipts.E003`). Apps that set nothing behave as in 3.6.0 to 3.7.0. Contract: `docs/design/receipts.md`.

## [3.7.0] — 2026-10-17

### Added

DCM-BILL-5: operator billing data and a richer `me/`. Three operator endpoints guarded by the Users-tab permission (`can_view_users_admin`):
`GET operator/overview/` (monthly aggregates: accounts by access source, new and ended subscriptions, team packages and seats, token packages,
tokens granted/used/expired, AI cost by model and per used token), `GET operator/accounts/` (searchable, paginated, constant-query account list)
and `POST|DELETE operator/accounts/<user_id>/free-access/` (reason mandatory, audited, revoking deletes nothing). `me/` adds `subscription`,
`tokens.grants`, `team` (payer: seat holders) and `seat` (holder). Existing keys unchanged. Contract: `docs/design/billing.md`.

## [3.6.1] — 2026-10-17

### Fixed

DCM-BILL-4: a withdrawal is completed from Stripe's refund event. If the server crashed after Stripe accepted the withdrawal refund but
before the local commit, the purchase stayed active although the person was refunded. Ending the access, expiring the tokens, marking the
purchase withdrawn and the confirmation e-mail are now one routine under the purchase row lock, shared by the POST path, the webhook
(`refund.created`, `refund.updated`, `charge.refunded`) and `billing_reconcile`. Only refunds carrying the withdrawal key
`dcm-withdrawal:<app>:<purchase id>` count; manual refunds and disputes are unchanged. The Stripe webhook endpoint must subscribe
`refund.created` and `refund.updated`. Contract: `docs/design/billing.md`.

## [3.6.0] — 2026-10-16

### Added

DCM-RCPT-1: shared receipt recognition, the opt-in app `django_core_micha.receipts` (label `dcm_receipts`, extra `receipts` = pillow-heif,
together with the `ai` and `extraction` extras). One call reads a file with one or several receipts (core schema with app extension,
Swiss-focused multilingual prompt, normalisers, positions on the image, HEIC/PDF/upload preparation, account suggestion restricted to the
app's list, perceptual duplicate hash). It is refused without the user's standing, revocable and audited consent (`ReceiptConsent`,
migration `dcm_receipts` 0001, `import_consents` for consumer migrations). The spend cap of the extraction module applies to every
reading; an optional per-user daily count, the central default model `gpt-6-luna`, REST `consent/` and `read/` with stable error codes
and a deterministic fake reader for local and test environments complete it. Nothing but the consent is stored.
Contract: `docs/design/receipts.md`.

## [3.5.0] — 2026-10-16

### Added

DCM-CHAT-13 (assistant): usage and cost per reply for every driver (`claude_cli`, `openai_api`, `anthropic_api`) with normalised
usage (input, output, cache read, cache write); shared price table `django_core_micha.ai_prices` (`AI_MODEL_PRICES_USD_PER_MILLION`,
long-prompt tiers, incomplete rows fall back to the legacy rates); `AssistantTurn.cost_usd_micros` / `context_tokens`; optional
`DCM_ASSISTANT_USAGE_HOOK`; automatic summary of long chats above `context_limit_tokens` (`AssistantSummary`, the summary is treated as
untrusted data in the prompt, its cost is recorded, `summarised` notice, shown in history); model levels and picker data with
per-user filtering and cost estimates. Cache tokens are now priced in the billing cost record (closes the limit of 3.4.0).
Migrations `dcm_assistant` 0004, 0005.

DCM-BILL-3 (billing): EU/EEA withdrawal. `Purchase` record per first payment (subscription, team package, token package),
`eligibility()` (EU/EEA country, no business tax ID, 14 days), proportional `refund_amount()` (larger of time and token share),
`withdraw()` as one idempotent operation (access ends, remaining tokens zeroed, Stripe refund with idempotency key, confirmation e-mail
after commit), endpoints `GET withdrawal/`, `GET withdrawal/<id>/preview/`, `POST withdrawal/<id>/`, `reverse_debit()` for token
credit-back, `charge.refunded` recorded as a no-op, a withdrawn purchase is never reopened by later events. Setting
`DCM_BILLING_WITHDRAWAL_ENABLED`. Migration `dcm_billing` 0002. Contract: `docs/design/billing.md`.

## [3.4.0] — 2026-10-16

### Added

DCM-BILL-1: shared subscriptions and AI tokens with Stripe Managed Payments, as the opt-in app `django_core_micha.billing`
(label `dcm_billing`, extra `django-core-micha[billing]` = stripe + PyYAML; an app that does not install it gets no tables and no
routes). Contract: `docs/design/billing.md`.
- Plans, packages and token rates are declared in the app's `billing.yaml` (validated; system check and `billing_check`);
  `billing_sync_plans` creates Stripe Products and immutable, versioned Prices idempotently (a changed price opens `v2`, a changed
  tax code opens a new Product, existing subscriptions keep theirs). Every Stripe object carries `metadata.app`.
- Checkout (`POST checkout/`), customer portal (`POST portal/`), signed and idempotent webhook (`POST webhook/`: events of another
  app are ignored; subscription lifecycle, renewals, failed payments, team packages and token packages; entitlements are fixed at
  checkout), `billing_reconcile` repairs missed events.
- Access: `access_state(user)` is `active` through a subscription, a seat in a team package or an operator free access
  (`billing_grant_free_access` / `billing_revoke_free_access`); otherwise `read_only` with the date access ended. Per operator
  decision nothing is deleted automatically and there is no deletion date or notice.
- Token account per person or team: grants (per term, expiring), purchases, atomic debits that never go below zero and are idempotent per
  reference, expiry (`billing_expire_tokens`), `tokens_for(rate, units)`.
- AI costs: every chat turn, completion and extraction call records provider, model, tokens and cost in USD micros per app
  (`record_ai_call`); `GET cost-report/?month=YYYY-MM` (bearer token, SHA-256 digests in settings) returns only per-app sums.
- Endpoints for the UI: `GET me/` (access, token balance, plans) and `GET|POST|DELETE team/seats/`.
Known limits: the cache-token share of a call is stored but not yet priced (cache rates arrive with DCM-CHAT-13); a monthly
subscription's allowance follows the current `billing.yaml` at each renewal; whether the Stripe account is enabled for Managed Payments
is not verifiable from code.


## [3.3.0] — 2026-10-15

### Added

DCM-CHAT-12: shared chats in a scope. New optional hook `DCM_ASSISTANT_SHARED_SCOPE(scope) -> bool` (an empty scope is never
shared; a missing, failing or broken hook means not shared). In a shared scope every user who passes the scope permission can list
(`chats/?scope=`), read, send, rename, change model and effort, archive and restore the chats; only the creator can delete (others
get 403 `forbidden`). Owner-only stays the default and is unchanged. Each turn records its sender (`AssistantTurn.sender`, migration
`0003_turn_sender`); credentials, cost guard and the per-user turn limit use the sender, and turn payloads of shared scopes carry
`sender_id`. Running-turn frames reach every open socket of the chat through a Channels group; every delivery re-checks the
receiving user (session, scope permission, shared scope) at most once per `access_recheck_seconds` and closes with 4403 on a failure
(or on a failing re-check). The live access check of a running turn now uses the same visibility rule, and a second `send` during a running turn
gets `turn_active`. A `stop` frame only cancels the turn started on the same connection.

DCM-CHAT-11: chat core for a structured consumer. The `send` frame accepts an optional `context` object (bounded by the new limit
`max_client_context_bytes`, default 65536; an optional `DCM_ASSISTANT_CLIENT_CONTEXT(chat, user, context)` hook validates and may
normalise it; a rejected context ends the send with `invalid_frame` before any turn row or provider call). The context reaches
`DCM_ASSISTANT_TURN_CONTEXT` as `client_context=` when that hook declares it (hooks written for 3.1 are unchanged) and is never
stored. `DCM_ASSISTANT_STRUCTURED_TURNS = True` stops `delta` frames for an app's turns. `AfterTurnResult(repair="feedback")`
asks for a repair round inside the same turn (resume for the Claude Code driver, next message for the API drivers), bounded by the new limit
`max_repair_rounds` (default 2) and by `max_prompt_bytes`; after the last round the hook's own text and frames are the fallback.
New Python API `django_core_micha.assistant.completion.complete()` / `acomplete()`: one prompt to one catalogue entry, no chat, no
persistence and no tools, with the calling user's credentials, the cost guard, the provider timeout and the size bounds; the Claude
Code throwaway session directory is removed afterwards.


## [3.2.0] — 2026-10-15

### Added

DCM-CHAT-9: a scope-aware tool set for the chat core. New optional hook `DCM_ASSISTANT_TOOLS_FOR_CHAT(chat)` returning an
iterable of `ToolSpec` or `None`. Not configured: the global `DCM_ASSISTANT_TOOL_REGISTRY` applies to every chat, as before.
Configured: a returned list is exactly that chat's tool set (an empty list means no tools) and is validated like the global
registry; `None` means the global registry for an unscoped chat and no tools for a scoped chat. A hook that raises, returns an
invalid list or returns a scalar fails the turn with `provider_error` before any provider call. All drivers already refuse a tool
outside the chat's offered list (now tested per driver). Handlers receive the chat, so a handler can bind to `chat.scope`; a
different system prompt per scope comes from the existing `DCM_ASSISTANT_SYSTEM_PROMPT_BUILDER(chat)`.
For consumers' tests: `turns.run_turn` now resolves tools through `hooks.tools_for_chat(chat)`; a test that patched
`turns.tool_registry` patches `turns.tools_for_chat` instead (`lambda chat: [...]`).


## [3.1.0] — 2026-10-15

### Added

DCM-CHAT-10: optional hooks and behaviours of the chat core for a research consumer (maana). All are off or neutral when not
configured; apps that use none of them (cockpit) behave as before. Migration `0002_session_upkeep` adds `session_tokens` and
`session_prompt_version` to the chat.
- `DCM_ASSISTANT_TURN_CONTEXT(chat, user, message, turn)`: context text sent with that turn only (CLI: `context` key of the prompt;
  API drivers: prefix of the user message), never stored in the turn's `user_text` or the history. A raising hook fails the turn
  with `provider_error` before any provider call.
- `DCM_ASSISTANT_AFTER_TURN(chat, user, turn, text)`: may return a replacement text and app frames
  (`AfterTurnResult(text, frames)`), delivered as `{"type":"app","name":...,"payload":...}` before the final `turn` frame. A raising
  hook keeps the raw text and the turn completed.
- `DCM_ASSISTANT_AVAILABILITY(user, scope)` returns a reason code or None: `GET status/?scope=` reports
  `{"status":"unavailable","reason":...}` (a scope the user may not access answers 404), and a `send` in an unavailable scope is
  answered with the error `unavailable` (with `reason`) before any turn or provider call.
- Live access check: while a turn runs the scope permission, the optional `DCM_ASSISTANT_TURN_VALIDITY(chat, user, turn)` hook and
  the session are re-checked every `access_recheck_seconds` (default 1; also once right before the provider call). A failure cancels
  the turn and closes the socket with 4403.
- `GET chats/?scope=` returns only that scope's chats (forbidden scope: 404; cursors are bound to the scope).
- Claude Code session upkeep: a resumed session whose summed input tokens exceed the limit `session_rebuild_tokens` (default
  200000) or whose stored `DCM_ASSISTANT_PROMPT_VERSION` differs from the configured one is not resumed (history is rebuilt);
  a rebuilt history that exceeds `max_prompt_bytes` drops the oldest turns and sends `{"type":"notice","code":"history_trimmed","left_out":n}`.


## [3.0.2] — 2026-10-15

### Fixed

DCM-CHAT-7: a provider timeout before the Claude Code CLI's `init` event now also discards a resumed session. The turn runner
enforces its own provider timeout, which replaced the driver's signal; the CLI driver now yields an internal `init` marker
event (consumed by the turn runner, never sent to clients) and the runner clears the session reference when a resumed turn
times out without having seen `init`. A timeout after `init` keeps the session.

### Added

DCM-CHAT-8: failed provider calls in the OpenAI and Anthropic chat drivers are logged on the server with one warning entry
(logger `django_core_micha.assistant.drivers.base`): driver, model, HTTP status, the provider's error type, code, parameter
and message. API-key-like tokens (`sk-...`) are redacted and each field is capped at 1000 characters; request bodies, prompts
and tool content are never logged. Client-facing error codes and the 429 mapping are unchanged.


## [3.0.1] — 2026-10-15

### Fixed

DCM-CHAT-4: the chat core accepts synchronous tool handlers. A synchronous handler runs in a worker thread that permits ORM
access (before, it ran on the event loop and a database read failed with `SynchronousOnlyOperation`); an async handler is
awaited as before. A running synchronous handler is not interrupted when a turn is stopped, which is why tools stay read-only.

DCM-CHAT-5: a chat survives an interrupted Claude Code turn. Events of type `system` before the CLI's `init` event (for example
`ui_invalidate` after a resumed session) are ignored; any other event before `init` and a wrong `init` still fail closed. A resumed
session that fails or is stopped before `init` is discarded, so the next turn starts a fresh provider session and rebuilds the
history from the stored turns.

DCM-CHAT-6: the chat list archive filter was inverted. The default list now returns the owner's active chats and
`?archived=true` returns the archived ones.


## [3.0.0] — 2026-10-15

### Changed (breaking for consumers)

DCM-DEPS-3: `django-core-micha` now declares only what every consumer needs on app start. Optional features moved
behind extras, the Postgres driver is psycopg 3, and `redis` is declared.

| Extra | dcm features that need it | Packages it brings |
|---|---|---|
| `ai` | `assistant` (OpenAI and Anthropic drivers), `extraction` provider drivers | `openai>=2.54.0`, `anthropic` |
| `extraction` | `extraction` (PDF text, image resize); also image validation in `SafeImageField` and image attachments in `messaging` (without Pillow those reject image uploads) | `pypdf>=6.19.0`, `Pillow>=12.3.0` |
| `push` | web push delivery in `notifications` (delivery is disabled when the package is absent, as before) | `pywebpush` |
| `scripts` | the console scripts `generate-env`, `sync-secrets`, `run-dev`, `provision-app`, `provision-server-access` | `PyYAML` |

Core now also declares `psycopg[binary]` (psycopg 3) instead of `psycopg2-binary`, and `redis` (the cache backend and
`cached_db` sessions in `settings_base` use it). Using an `ai` or `extraction` feature without its extra raises an error
that names `django-core-micha[ai]` or `django-core-micha[extraction]`.

How to migrate an app: in `requirements.txt` pin `django-core-micha[<extras>]==3.0.0` with the extras from the table that
the app uses, or keep pinning the package itself (for example `Pillow==12.3.0`). An app that pins `psycopg2-binary` itself
should move to `psycopg[binary]`; the `DATABASES` OPTIONS that `settings_base` sets are libpq parameters and work unchanged.


## [2.45.1] — 2026-10-15

### Changed

DCM-DEPS-2: dependency floors and workflow pins from the Renovate bundle. Runtime floors: django-anymail >=15.2, Pillow >=12.3.0, pypdf >=6.19.0, openai >=2.54.0, cryptography >=42.0.8. Test floors: pytest >=9.1.1, pytest-django >=4.14.0, pytest-asyncio >=1.4.0. Publish workflow: actions/checkout v7.0.1, actions/setup-python v7.0.0, Python 3.14.8, pypi-publish v1.14.2. Not taken in: the Django >=6.1.2 floor, cryptography >=50.0.2 and openai >=3.26.1, because apps pin Django 6.1/6.1.1, cryptography 50.0.0 (cockpit) and openai 3.24.0 (cockpit, reimbursements, spesix) below those floors.


## [2.45.0] — 2026-10-15

### Added

DCM-CHAT-1: opt-in module `django_core_micha.assistant` (app label `dcm_assistant`, not part of `CORE_APPS`): persistent per-user AI chats with a durable turn lifecycle, an owner-only WebSocket stream and REST surface, three drivers (Claude Code CLI on a subscription token, OpenAI API, Anthropic API), a per-chat model and effort choice from an app-defined catalogue, and read-only tools the app registers (each handler receives the driver's trust class and returns sources). API drivers go through the extraction cost guard for every provider call. Credential resolution is one seam and every turn stores its billing source. The binding transport contract is `docs/design/assistant-chat.md`. Apps that do not install the module get no tables and no routes.


## [2.44.6] — 2026-10-14

### Fixed

DCM-NOTIF-29: notification email rendering falls back to `user.profile.language` when no contact
profile is available, while preserving contact-profile precedence and the German default for
missing or unsupported languages.
Push and feed rendering retain their existing contact-profile-only behavior.

## [2.44.5] — 2026-10-12

### Added

RMB-AI-7: the default AI cost rate table gains `gpt-6-luna` at 0.01 cents per 1k input tokens and
0.05 cents per 1k output tokens (OpenAI list price USD 0.10 / 0.50 per 1M). Before, that model was
accounted at the pessimistic unknown-model fallback (1.5 / 6.0). No other entry, rounding rule or
override semantics change; an operator-supplied `AI_MODEL_COST_TABLE_CENTS_PER_1K` still replaces
the whole table.

## [2.44.4] — 2026-10-12

### Fixed

RMB-AI-6 (second review pass on the 2.44.3 lock-free admission): admission now reads outstanding
reservations BEFORE settled spend, so a call settling between the two reads can be double-counted
(conservative) but never missed; the reservation window is recomputed after the add and spans a
concurrent request's reservation in the next 5-minute bucket; and the input-token estimate is more
pessimistic (about 2 characters per token instead of 4) so dense scripts such as CJK text are not
badly under-reserved. No API or setting changes.

## [2.44.3] — 2026-10-12

### Fixed

RMB-AI-6: `extract_document` now reserves budget BEFORE any provider call. The daily per-user cap
was only enforced after the provider answered, so every call made while over the cap still spent
money and its result was then discarded. Admission now adds a conservative, expiring reservation
(atomic cache `add`/`incr`, rolled back on denial, 30-minute bounded lifetime, keyed to the admission
UTC day) against settled spend plus outstanding reservations and raises `AICostLimitExceeded` before
the provider runs; concurrent callers can be denied conservatively but never over-admitted. Settlement
charges reported usage to the admission day without raising; billed truncation still counts;
definitively unbilled failures (pre-provider validation, provider 4xx other than 408/409/429) release
the reservation; ambiguous failures hold it until expiry. A cache failure during admission raises the
new `AICostAccountingUnavailable` instead of admitting unmetered work. `DocumentExtractionError` gains
the additive `provider_request_started` attribute. The settled counter key, cap
(`AI_DAILY_COST_LIMIT_CENTS`, default 500), `AI_COST_LIMIT_ENABLED`, the rate table, `charge_user*`
and `current_user_spend_cents` are unchanged; disabled/local behaviour and `user_id=None` are
unchanged. Optional new setting `AI_COST_RESERVATION_TTL_SECONDS` (default 1800).

## [2.44.2] — 2026-09-29

### Fixed

DCM-AUTH-3: `BaseUserViewSet`'s generic self-edit and self-delete restrictions now apply only
when the resolved object is the requesting user's own row. Non-admin writes to other users remain
available to consumers whose `get_queryset()` deliberately exposes those rows, as before 2.44.1.

## [2.44.1] — 2026-09-29

### Fixed

DCM-AUTH-2: `BaseUserViewSet`'s generic `update`/`partial_update`/`destroy` routes let a non-admin
bypass the `current()` action's field allowlist on their own account, because `get_queryset()`
already scopes a non-admin to their own row -- `PATCH`/`PUT /api/users/<own-id>/` could write
`is_active` and other unsafe fields, and `DELETE /api/users/<own-id>/` could delete the account
outright. The generic `update()` now enforces the same `current_patch_allowed_fields` allowlist for
a non-admin target (a full `PUT` is treated as partial for a non-admin, so it does not need to
supply every field); `destroy()` now refuses a non-admin's self-delete (403). Both checks resolve
the object first, so a non-admin targeting an id that isn't theirs still gets the pre-existing 404.
Admin behaviour and `current()` are unchanged. No frontend change; an estate-wide survey found no
consumer relying on generic self-delete, and jg-ferien's one generic self-PATCH use
(`ParticipantDetails.jsx`, `first_name`/`last_name`) stays within the allowlist.

## [2.44.0] — 2026-09-15

### Added

DCM-AUTH-1: an admin-issued invite link (`users/invite/`, `users/<pk>/invite-link/`) now stays
valid for 30 days instead of 3, via a new `InviteTokenGenerator` with its own key salt and
lifetime — the password-reset link keeps Django's default 3-day `PASSWORD_RESET_TIMEOUT`
unchanged, and `PasswordResetConfirmView` accepts a token valid under either generator. New
setting `INVITE_LINK_TIMEOUT_DAYS` (default 30, ENV-overridable). The invite email texts (en/de/fr)
now state the configured lifetime. No frontend change; consuming apps get the new lifetime by
bumping their `django-core-micha` pin.

## [2.43.2] — 2026-08-27

### Fixed

DCM-NOTIF-10: a `Notification` whose problem had already been resolved was never reused by a later
`notify()` for the same (type, target) — `dedup_key` carried a permanent `UniqueConstraint`, so a
monitor that went down, recovered, and went down again alerted exactly once, ever. `Notification`
gains a nullable `resolved_at`; the uniqueness is now partial (at most one OPEN row per `dedup_key`),
so an emit after resolution starts a fresh episode. New `django_core_micha.notifications.api.resolve()`
and `.has_open()` primitives; `get_or_create_by_dedup` and the dedup lookups now scope to the open
row. Migration `0009` backfills `resolved_at` for existing fully-resolved rows. No change for callers
that never resolve a notification (e.g. the todo channel), which keep their previous one-row-per-key
behaviour unchanged.

## [2.43.1] — 2026-08-27

### Added

`run-dev --refresh-deps` requests a uv dependency refresh explicitly and implies `--build`.
In `--no-log-stream` mode `run-dev` now waits for the backend to answer and closes with exactly
one parseable line — `READY <url>` or `TIMEOUT after <n>s` (60 s default) — leaving the containers
running either way. Agent sessions previously had nothing to wait on: `docker compose up -d`
returns at container *creation* and no consuming app declares a `backend` healthcheck, so the
detached mode returned into an app that was not yet serving, while every other mode blocks
forever by design. The readiness probe resolves its port through `docker compose port`, never by
reading `.env`.

### Changed

DCM-DX-5: `--build` no longer sets `UV_FLAGS=--refresh`. That flag defeats the
`--mount=type=cache,target=/root/.cache/uv` cache in the consuming apps' Dockerfiles, so a local
`--build` re-resolved dependencies from scratch on every invocation while CI, building with the
default `ARG UV_FLAGS=""`, hit the cache in full — the largest single contributor to the
local/staging build-time gap. The deprecated `--refresh` flag keeps its existing meaning as an
alias for `--build` and was deliberately not repurposed. `ensure_frontend_node_modules` now also
runs `pnpm install` when `pnpm-lock.yaml`/`package.json` is newer than `node_modules/.modules.yaml`,
not only when `node_modules` is absent — a changed lockfile previously left the host-side frontend
build running against stale dependencies with no error, which the container then served via
`FRONTEND_BUILD_DIR`. `--watch`'s help text now states that it runs a full `vite build` per change
rather than HMR (`--vite` is HMR), and the `--watch` + `--no-log-stream` message describes what
that combination does instead of reading as a usage error. Script output is unbuffered and the
frontend watch loop emits a heartbeat, so a long build no longer looks like a stalled process.

### Fixed

`docker rm -f traefik` ran unconditionally — and twice — from whichever app repo `run-dev` was
started in, removing a traefik container belonging to another Compose project, in practice
`webapp-management`'s. Removal is now scoped to the invocation's own project, whose name is
derived from `project.yaml` using `generate_env`'s own `{project_name}_{env_name}` formula. This
was caught in review: the first implementation reconstructed the project name from the directory
basename, which misclassified the invocation's own container as foreign in almost every case,
and reading the name back from `.env` is not an option.

## [2.43.0] — 2026-08-18

### Changed

DEPS-1 moves the supported Django range from `>=6.0.5,<6.1` to `>=6.1,<7.0`, unblocking
the Django 6.1 upgrades in kerzenziehen PR #50 and innoservice PR #55 once those apps
adopt django-core-micha 2.43.0. Email configuration now uses Django's `MAILERS`
setting exclusively and supports only the estate's live console and Resend paths;
the unused SMTP and Postmark branches were removed. `MAILERS` is a Django 6.1+-only
concept — on Django 6.0.x it is inert and Django silently falls back to its own SMTP
default, so the lower bound was raised to `>=6.1` (not left at `>=6.0.5`) specifically
to prevent that silent breakage in any consuming app still on Django 6.0.x: those apps
now simply cannot resolve 2.43.0 at all, rather than resolving it and losing mail
routing. This eliminates the `RemovedInDjango70Warning` and avoids Django 6.1's
settings-conflict boot failure, as verified against Django 6.1's deprecated-settings
inventory.

## [2.41.1] — 2026-08-06

### Fixed

SEC-3: a bare `sync-secrets` run resolved its GitHub-Environment target list from a
hand-maintained `config.bare_server_targets` override, which silently drifted from the
estate's real environments (root cause of OPS-2: the `monitoring` environment held 0
secrets against 32-38 elsewhere, `DOMAIN_KUMA` was never populated, `status.bigler-consult.ch`
404'd). Bare-mode target resolution now has an explicit precedence — `config.bare_server_targets`
if set, else `project.yaml infra.servers` keys in declaration order, else the built-in
`staging`/`production` default — and every bare run now verifies the resolved targets against the
repo's actual GitHub Environments (`gh api repos/{repo}/environments`), naming the difference in
both directions (an environment with no target, or a target with no environment) instead of
quietly covering a subset. A mismatch is **fatal** only when the target list was *derived* from
`infra.servers` — a derived registry can itself be incomplete, which is the exact bug class OPS-2
was. A mismatch on an explicit override or the built-in default is a **loud warning, not a
failure**: those reflect an existing consumer's current, already-working configuration (e.g. hram
has a real third GitHub Environment, `hram-webapp`, its default doesn't know about), and this fix
must not turn that kind of pre-existing, out-of-scope drift into a surprise hard failure for a repo
SEC-3 never touched. Unable to fetch the environment list at all (gh missing/unauthenticated/API
error) is always a warning — a transient tooling gap must not block the routine either way.

## [2.41.0] — 2026-08-06

### Added

NOTIF-26: notification types now declare *reach* (`active`/`passive` on `NotificationType`,
not a channel list) instead of enumerating `default_channels`/`eligible_channels` — an app
says whether a notification must reach the user (active: email/push, the user's own choice
of which) or may wait until they look (passive: chip), with all three states (active-only,
passive-only, both) expressible. `feed_visible` is now derived from `passive` instead of an
independently-settable flag. Adds `NotificationCategorySubscription` (a separate model from
`NotificationCategoryChannelPreference` — a different consent) and `notify_subscribers()` for
events with no natural recipient owner (a nightly job, a CLI-started run), resolved from
explicit opt-in only — never derived from `is_channel_enabled()`'s default-True passive tier.
Gated by a required `content_is_shareable=True` acknowledgement, since one `content` payload
is durable and visible to every subscriber. The `preferences/` endpoint now also reports the
app's registered event types (with reach + a computed `has_active_channel` flag) and its
subscribable categories (translated label + subscription state); a new `preferences/subscriptions/`
endpoint toggles a subscription. `NotificationType` gained an optional `label_key`, resolved
through the text registry, for a human-readable settings-surface label per type.

### Changed

`default_channels`/`eligible_channels` are removed from `NotificationType` — `eligible_channels`
is now a derived property computed from reach. Existing registrations must migrate to `active`/
`passive` (see `messaging/notifications.py`'s migration for the pattern). No change to `notify()`'s
signature.

### Fixed

Capped `Django<6.1` — Django 6.1 removed `django.utils.cache.cc_delim_re`, which the installed
`djangorestframework` (3.16.1) still imports, breaking every DRF-touching module at import time.
Unrelated to this release's own changes; discovered when this version's CI publish run was the
first to actually resolve Django 6.1 fresh. Operator-approved dependency-pin fix.

## [2.40.6] — 2026-08-05

### Fixed

`ActivityBucket`'s index name `activity_bucket_scope_range_idx` (31 characters) exceeded Django's
30-character `Index.name` limit (kept for Oracle compatibility), raising system check error E034 and
blocking `manage.py migrate` outright for every consumer before the table could even be created.
Renamed to `activity_bucket_scope_range_ix` (30 characters) directly in the `activity` app's
`0001_initial` migration rather than adding a `RenameIndex` migration, since no consumer's persistent
database had successfully applied it yet — the check always failed before the table was created.
Discovered via jg-ferien's staging deploy failing at the system-check stage.

## [2.40.5] — 2026-08-04

### Fixed

The new `activity` app (ACT-1, shipped 2.40.4) was never added to `CORE_APPS` in
`settings_base.py`, so no consuming app could actually load it — `ActivityBucket` raised
`RuntimeError: ... isn't in an application in INSTALLED_APPS` the moment anything imported it.
Discovered integrating jg-ferien (ACT-3). Follows the exact same unconditional-`CORE_APPS` pattern
as `messaging`/`notifications`/`onboarding`; no consumer-side settings change needed.

## [2.40.4] — 2026-08-04

### Added

New `activity` app: a shared, cross-app user-activity domain scoped generically to any consumer
model via `content_type`/`object_id` (mirroring `messaging.MessagingScope`'s pattern), replacing the
need for every app to build its own presence-tracking model. A ping endpoint upserts hourly
presence buckets with a capped, concurrency-safe accumulation rule (ported from jg-ferien's
reference implementation); a query endpoint aggregates server-side to a requested granularity
(hour/4hour/day/month, fixed to the requested range) rather than shipping raw rows; a
`cleanup_activity_buckets` management command enforces retention. Reading is gated by a new
per-app-registered `ActivityPolicy` hook (mirroring `MessagingPolicy`) — fails closed: no policy
registered for an app means every read is denied, never implicitly allowed (ACT-1).

## [2.40.2] — 2026-08-03

### Fixed

`serialize_attachment` never returned `filename`, even though `MessageAttachment` already stores the
real, sanitized upload name (distinct from `blob_key`, the obfuscated storage path) — every client
fell back to displaying the raw attachment id (MSG-12).

## [2.40.1] — 2026-08-03

### Fixed

Attachment uploads were rejected unconditionally: `client_request_id` from raw multipart body data
arrived as a `str`, compared with `!=` directly against the header's `uuid.UUID` — always `True` in
Python. `_idempotency_request_id` now coerces before comparing, unconditionally (not just when an
`Idempotency-Key` header is present), rejecting a malformed value with a clear 400 rather than either
a silent pass-through or an uncaught 500 (MSG-10).

`read_status` and `batch_read_status` both reported `all_read: True` for a message with zero
recipients (an empty-set vacuous truth) — a message nobody can receive no longer claims to be "read by
everyone" (MSG-10).

`vote_poll` never retracted a multi-select vote: only the single-choice branch cleared prior votes
before creating new ones, so a smaller `option_ids` set on a later call silently kept the omitted
option's vote. `option_ids` is now the caller's complete, authoritative set for both single- and
multi-select polls (MSG-11).

## [2.40.0] — 2026-08-03

### Added

`read_status` now includes `read_count` and `recipient_count` alongside `all_read`, inside the same
`read_receipt_detail`-gated block as `recipient_detail` — team-only, same as the existing gate, no new
permission logic. A new batch endpoint, `POST /messaging/messages/read-status/batch/`
(`{"message_ids": [...]}`, capped at 100), returns the same per-message aggregate for many messages in a
bounded number of queries (one per distinct conversation among the requested ids, not one per message) —
enforces the same per-message view permission as the single endpoint; a message the caller cannot view
is silently absent from the response, never a denied placeholder.

## [2.39.5] — 2026-08-03

### Testing

Added regression coverage for the payload-encoding failure path introduced in 2.39.4 (a payload that
cannot be JSON-encoded now has a dedicated test proving it logs once at error level and reaches no
recipient, rather than being encoded per-user) — closed a gap found in 2.39.4's independent review. No
behavioural change.

## [2.39.4] — 2026-08-03

### Added

Messages and compact conversation last-message projections now include a `sender` object with `id` and
`display_name`. Poll REST projections now include their stable `message_id`.

### Fixed

WebSocket fan-out now normalizes every payload through the same JSON renderer as REST before it reaches
the Redis msgpack serializer. Messaging frames containing datetimes therefore deliver in the same
ISO-8601 shape as REST responses, and encoding/delivery errors now log their frame type and payload
keys at error level while leaving durable writes isolated.

### Breaking changes

`read_status` no longer returns the permanently-zero `delivered_count`, and the unused
`mark_delivered` service has been removed.

## [2.39.3] — 2026-08-02

### Fixed

#### Direct conversations exposed no counterpart identity

`serialize_conversation` now includes `other_user_id` for direct conversations — a bare id (never a
resolved name, matching the existing `sender_id` convention on messages) identifying the OTHER
participant relative to whichever participant is asking. Sourced from the already-denormalized
`user_low`/`user_high` FKs on `Conversation`, so no extra query. Deliberately NOT added to
`serialize_conversation_core` (the realtime `conversation_upsert` fan-out payload), since that value is
per-viewer and would leak the wrong direction across a shared broadcast frame.

## [2.39.2] — 2026-08-02

### Fixed

#### NOTIF-24 — batch provider-todo overlay materialization

Todo derivation now resolves overrides and existing notification/recipient overlays in set queries,
uses conflict-tolerant bulk inserts backed by the existing unique constraints, and bulk-refreshes only
stale content. The 13-todo reference fixture falls from 39 queries on both cold and warm derivations to
8 cold / 3 warm queries. The unread badge now counts the same live todo projection without creating
notification overlays merely to produce its integer.

## [2.39.1] — 2026-08-01

### Fixed

#### MSG-2g — deduplicate multi-participant conversation lists correctly

`GET /messaging/conversations/` now applies the requesting participant's active and archive-state
conditions through the same relation join. Multi-participant conversations therefore appear once,
rather than once per matching participant; `include_archived=true` continues to include the
requesting user's archived participation row.

## [2.39.0] — 2026-08-01

### Fixed

#### MSG-2f — group/broadcast conversation lifecycle: membership reconciliation + idempotency

`create_conversation()` previously always did a blind `Conversation.objects.create(...)` for `group`/
`broadcast`/`managed`, even though the schema already had a `(app,scope,kind,external_key)` unique
constraint for `managed`/`broadcast` — a second call for the same key raised `IntegrityError`. Widened
that constraint to cover `group` too and made keyed creation idempotent: an authorized actor
re-opening an existing keyed conversation now joins it (added as a participant, `participant_users`
processed, membership reconciled) rather than either crashing or being silently locked out. Membership
reconciliation (`reconcile_membership`, calling the app-supplied `provision_membership` hook) now runs
for `group`/`broadcast` too, not only `managed`/`object_thread` — both at creation and on every
authorized re-open (`trigger="reconcile"`), so a group/broadcast conversation's membership stays live
instead of frozen at its creation-time snapshot. The realtime `conversation_upsert` frame still
publishes only for a genuinely new conversation, not on every re-open. A migration widens the existing
partial unique constraint (defensive data-preservation step included; no live consumer had used this
code path before this fix, confirmed via `create_conversation`'s single call site). Design doc updated
to document idempotent join-on-reuse and to confirm `MessagingScope.Kind.OBJECT` already generically
supports a per-entity object scope with no dcm change needed — an app's own group-like entity gets a
stable scope via `get_or_create(kind=OBJECT, content_type=..., object_id=...)`, same mechanism any
object scope already uses.

## [2.38.0] — 2026-07-31

### Added

#### MSG-2d — readable thread reply state, managed-conversation identity

Messages now carry viewer-independent `reply_count`/`last_reply_at` (a soft-deleted reply still counts, matching its tombstone rendering), so a freshly-mounted client can tell a root has replies without expanding every thread. The requesting user's own `thread_last_read_at` is added REST-only, on top of the viewer-independent projection, never on a realtime frame. `serialize_conversation` now exposes `external_key`, letting a client distinguish managed/broadcast conversations that otherwise share the same `kind`. Reply counts are annotated/aggregated and thread receipts bulk-fetched per page — no N+1 across a message list.

### Fixed

#### MSG-2e — declared messaging runtime dependencies

Declared `Pillow>=10`, required by the attachment image decode, EXIF-strip, safe re-encode, and thumbnail pipeline, and `cryptography>=42`, which backs messaging encryption at rest. Both were already imported by the messaging package; `cryptography` had previously been supplied only transitively through `django-allauth`.

## [2.37.0] — 2026-07-31

### Added

#### MSG-2c — poll read contract, conversation preview, realtime frame completion

Polls now expose a viewer-independent encrypted core projection in messages and mutation responses, with REST-only `voted_option_ids`; conversations include a bounded decrypted last-message preview. Added commit-safe messaging realtime fan-out for conversation, reaction, poll, receipt, archive, and membership updates while keeping frame payloads viewer-independent.

## [2.36.1] — 2026-07-31

### Fixed

**MSG-2b — scoped first-contact direct messages**

- A scoped direct conversation can now be opened with a target who has no existing `ConversationParticipant` row in the resolved app. Tenant selection remains entirely server-side (`scope.app`, or the single active app for an omitted scope), while the registered `MessagingPolicy.can_open_direct()` hook remains the sole authorization decision for who may be addressed inside that tenant and still runs before any conversation or participant row is created. Self-DMs remain rejected by the core service.

## [2.36.0] — 2026-07-31

### Added

**MSG-2 — shared messaging platform (`django_core_micha.messaging`)**

- New additive-only subpackage implementing the design in `docs/design/messaging-platform.md` (MSG-1): a consumer-agnostic, multi-tenant messaging domain generalized from jg-ferien's local implementation. No app-specific code lives in dcm — every app-specific decision (who may DM whom, who may post, recipient resolution, moderation rights, managed-membership provisioning) goes through an app-registered `MessagingPolicy` protocol.
- **Domain model:** `MessagingApp` (tenant registry), `MessagingScope` (container/object/global anchor), `Conversation` (direct/group/broadcast/managed/object_thread), `ConversationParticipant`, `Message`, `MessageReaction`, `MessageAttachment`, `MessageThreadReceipt`, `Poll`/`PollOption`/`PollVote`, `MessagingAuditEvent` — the four v1-extension seams (object-thread kind, delivered watermark, retention fields, participant channel/archive state) are schema now, per the design.
- **Encryption at rest, fail-closed by construction:** `MESSAGING_KEYRINGS[app_key]` provisions a distinct ordered Fernet ring per app (`MultiFernet`, rotation-capable); registration validates every ring (non-empty, valid keys, no key shared across apps) and refuses to serve an unregistered or invalidly-configured app rather than falling back to plaintext. Message text, poll content, and attachment blobs/thumbnails/filenames are all encrypted under the owning app's ring; the field deliberately does not auto-decrypt on ORM read — decryption only happens after an authenticated policy check, never on hydration.
- **Tenant resolution is server-side, never client-supplied:** a request never carries an `app_key`. Given a scope, the tenant is `scope.app`; given no scope (a global DM), the tenant resolves to the single active `MessagingApp` registration, failing closed (400/409) if zero or more than one exist — the schema stays genuinely multi-tenant while the ambiguous case is a server misconfiguration, not a client-supplied trust input.
- **REST + realtime contract:** the full `/api/messaging/` surface (conversations, direct/group/broadcast/managed/object-thread creation, messages, replies/threads, reactions, polls, read/delivered watermarks, archive/preferences, scope config, unread counts, attachment upload/download) with opaque signed cursor pagination and `Idempotency-Key`/`client_request_id`-based retry safety; realtime frames on the existing Layer-1 `messaging` WebSocket envelope (`push_to_users`) — no new WebSocket consumer, no client-to-server WS path anywhere, matching the design's explicit non-goal.
- **Privacy invariants enforced at the service layer, not just documented:** DMs never expose per-recipient read-receipt detail, even to a caller holding `read_receipt_detail`; the audited break-glass content-read path (`MessagingAuditEvent`, denial included) is unconditionally denied on DIRECT conversations regardless of moderation rights, since that capability governs read-status visibility, not content decryption. Soft-deleted messages clear `body`/`title`/`link_target` (not just `deleted_at`), and the `message_deleted` realtime frame carries only `message_id`/`deleted_at`/`deleted_by` — never content.
- **Attachments:** magic-byte allowlist (PDF, OOXML, ODF, PNG/JPEG/GIF/WebP) via `filetype`-based content-aware detection — including the OOXML/ODF-vs-bare-ZIP distinction, which `filetype` handles by inspecting internal archive members, not just the outer ZIP signature; images are decoded, EXIF-stripped, safely re-encoded, and thumbnailed before encryption; a `MessagingScanHook` seam is defined (no scanner installed in v1); downloads are always authenticated, viewer-gated, `nosniff`, and download-only (no inline rendering, no generic `/media/` exposure).
- **`notify()` gains a new keyword-only `expires_at=` parameter** (the underlying `Notification.expires_at` field and `prune_notifications` janitor query already existed; this exposes the capability through the public API for the first time — closes the NOTIF-21 gap). Messaging's own notification type (`{app_key}.messaging.new_message`, per-app opt-in registration) uses a 30-day TTL and jg's proven recipe verbatim: `email`+`push` only, no `chip`, `feed_visible=False`, sender/muted excluded via live recipient resolution, delivery failure isolated from the durable message (an exception in the notify path is caught and logged, never rolls back the already-committed message). Sensitive preview content (title/body excerpt) travels only through `transient=`, never persisted into `Notification.content`.
- Fully additive: no existing table altered, no existing `notify()` call site's behavior changed (new parameter defaults to `None`, byte-identical existing behavior).

## [2.35.0] — 2026-07-30

### Added

**NOTIF-19 — transient dispatch params + feed-visibility policy**

- `notify()` accepts a new keyword-only `transient=` mapping. Its values reach the email and push dispatchers for rendering but are **never** written to `Notification.content` and never enter the `dedup_key`. This makes "deliver it, but do not persist it" expressible for the first time: a consuming app whose source field is encrypted at rest (jg-ferien's `Message.body`/`title` are Fernet `EncryptedTextField`s with an audited privileged-read path) can now render an excerpt into an email or push without leaving a cleartext copy in an unencrypted table.
- The values travel through the `ctx` parameter that `Dispatcher.deliver` has always declared but that `dispatch()` never populated — no dispatcher signature changed; the seam was already there.
- **`ChipDispatcher` and `PopupDispatcher` deliberately ignore `ctx`.** They broadcast `notification.content` over the WebSocket, so keeping them clean is the point of the feature; a transient value must never reach the wire. Covered by a negative test.
- `_render_content` merges `transient` over `content["params"]` (transient wins on a key collision) into a new dict, leaving the persisted content untouched.
- `_render_content` now logs a warning when a `.format()` call fails and it falls back to the raw source key. The fallback behaviour itself is unchanged — it previously happened silently, which with transient params would ship a literal `{excerpt}` as an email body with no signal.
- `NotificationType` gains `feed_visible: bool = True`, plus an `iter_feed_hidden_type_keys()` accessor. `CanonicalInboxView` and `CanonicalUnreadCountView` now additionally exclude registered types that set it `False`, alongside the existing `category="todo"` exclusion. This lets a type be delivery-only (email/push) without appearing in the canonical bell feed — previously omitting the `chip` channel suppressed only the realtime push, while the REST feed still listed every recipient row.
- **Exclusion is by explicit registration, never by absence.** A `Notification` whose `notification_type` is no longer registered stays visible, so historical rows are unaffected.
- Fully backward compatible: both features default to today's behaviour. A caller passing neither argument produces a byte-identical persisted row, the same dispatch calls (`ctx=None`), and identical feed contents. Verified additionally that `exclude(field__in=<empty set>)` emits no `WHERE` clause at all, so the 13 consumer repos that register no hidden type see no query change.

## [2.34.0] — 2026-07-30

### Added

**NOTIF-12 — popup channel delivery**

- `PopupDispatcher.deliver` now actually delivers instead of logging a "pending" stub: it sends the same `push_to_users(notification_envelope({...}))` shape as `ChipDispatcher`, with a new `"channel": "popup"` field inside the payload so a client can tell the two apart (the envelope itself stays the NOTIF-13 domain-level `"notification"` discriminator — no second envelope value was introduced).
- `ChipDispatcher` gains the same `"channel": "chip"` field for symmetry. Both dispatchers also now include `"recipient_id"` (the `NotificationRecipient` pk) — `feed/mark/` resolves ids against that model, not `Notification`, and only the recipient pk lets a WS-pushed notification be marked seen/dismissed correctly before the next REST feed refresh.
- Backward compatible: a payload with no `channel` field (an app pinned to an older dcm) keeps behaving exactly as before (feed entry + unread increment) on the ucm side.
- **The popup channel ships with zero producers.** No notification type in dcm, jg-ferien, cockpit, hram, spesix, or survey_app declares `eligible_channels: ["popup"]`, so `resolve_channels()` will not route anything to it until an app opts a type in — this release only wires the dispatcher and is inactive until then. Proven end-to-end by a test-local notification type only (`test_dispatch.py`).
- `prefs.py`, `router.py`, and `resolve_channels` are unchanged — popup was already a valid channel there.

## [2.33.0] — 2026-07-30

### Added

**NOTIF-13 — realtime envelope discriminator**

- Every WS payload `push_to_users` sends for this app now additionally carries `"envelope": "notification"`, via a new `delivery.notification_envelope()` authoring helper — additive only, all existing fields (`type`, `notification_id`, `status`, `content`, etc.) are unchanged.
- Lets a consumer's Layer-1 realtime primitive (ucm 2.13.0's `subscribe(envelope, handler)`) route this domain's messages without misreading a second stream (e.g. messaging) as a notification. A payload with no `envelope` field (older dcm) is still treated as a notification by that primitive's default, so this is fully backward-compatible in both directions.
- `push_to_users`'s own signature is unchanged; only the two existing producers (`views.py`'s status-change broadcast, `dispatch.py`'s `ChipDispatcher`) now wrap their payload through the helper before sending.

## [2.32.0] — 2026-07-29

### Added

**`TodoOverride.created_by` audit field**

- Added `created_by` (nullable FK, `SET_NULL`, `related_name="+"`) to `TodoOverride`, migration `0006_todooverride_created_by` — additive, no default touching existing rows, no unique-constraint change.
- Restores audit attribution (which user set an override) that jg-ferien's NOTIF-10 cutover onto canonical `TodoOverride` would otherwise have silently dropped versus the legacy `EventTaskOverride.created_by` it replaced. Never exposed via any API response.

## [2.31.0] — 2026-07-28

### Added

**Cloudflare Turnstile bot-check for fully-open self-signup (SEC-2)**

- New `TURNSTILE_SITE_KEY`/`TURNSTILE_SECRET_KEY` settings (`env(default="")`) and `auth/turnstile.py` (stdlib `urllib` Cloudflare siteverify call, fail-closed on any missing/invalid token, network error, or hostname mismatch against `ALLOWED_HOSTS`).
- Enforced only inside `register_request` when `TURNSTILE_SECRET_KEY` is set AND the signup mode is `self_signup_open` or `self_signup_email_domain` — every other mode, and every consumer that has not configured the secret, is completely unaffected (pure no-op on this bump).
- `build_public_auth_config()` exposes `turnstile_site_key` only when the secret is configured — this is the single signal the frontend (ucm) uses to decide whether to render the widget at all.
- No new dependency (stdlib only); no `AuthPolicy` schema change.

## [2.30.0] — 2026-07-28

### Changed

**`sync-secrets` now has an explicit local/GitHub destination matrix and retires `.env.local`**

- No flags now writes the full, regenerable local `.env` first when `project.yaml` defines a `local` environment, then syncs every `config.bare_server_targets` GitHub environment. This is a behaviour change for existing automation: a bare invocation in a project with a local environment now also overwrites `.env`.
- `--local` writes only that full composed `.env`; `--github` and its alias `--remote` sync all configured GitHub targets without touching `.env`; `--staging`, `--production`, and legacy `--server --secret-target` retain their single-GitHub-target behaviour.
- Local generation now delegates to `generate-env --env local`, so `.env` includes platform and project composition such as ports, `PROJECT_NAME`, `TRAEFIK_ROUTER_RULE`, `app_env`, and resolved secrets. `.env.local` is no longer created.
- When a project has no `local` environment, bare mode reports a skip and continues its GitHub sync; explicit `--local` remains a fatal configuration error.

## [2.29.0] — 2026-07-27

### Added

**NOTIF-8 / NOTIF-8b — shared derived-todo channel**

- Added the generic derived-todo channel: the engine mechanics (due-expression evaluation, windowing, severity, materialization) lifted domain-free into `notifications/todo/`, a code-first provider registry (`register_todo_provider` / `TodoSeed` / `TodoTypeConfig`), and a `TodoOverride` model (per-scope enable + lead-days).
- Todos are canonical `Notification`s in `mode="derived"`: dismiss/done map onto `NotificationRecipient`, the digest sent-log onto `NotificationDelivery` — no parallel status tables.
- Self-healing on read (NOTIF-8b): the `feed/` surface derives currently-emitted todos live (excludes stored `category="todo"` rows), so a todo whose provider stops emitting disappears immediately; the digest scan additionally reconciles/deletes stale todo overlays.
- Added a generic window-scan/digest management command for derived-todo reminders.
- Dormant until a consumer app registers a todo provider — no behavior change for apps that do not.

## [2.28.0] — 2026-07-27

### Added

**NOTIF-5 — canonical notification read API and status synchronization**

- Added the canonical `feed/`, `feed/unread-count/`, and `feed/mark/` endpoints over `NotificationRecipient`, including flattened message content, per-user status filtering, and self-scoped seen/dismissed/done marking.
- Added the stable `notification.status` WebSocket payload contract so status changes synchronize across notification surfaces.
- Retained the legacy swappable-model `inbox/*` endpoints unchanged during the expand-contract transition.

## [2.27.0] — 2026-07-18

### Added

**NOTIF-1 through NOTIF-4 — canonical notifications platform core**

- Added canonical `Notification`, per-user `NotificationRecipient`, and per-channel `NotificationDelivery` records with deduplication, expiry, and category × channel preferences.
- Added code-first notification type policies, preference-aware routing, and the `notify()` authoring API.
- Formalized chip, email, web-push, todo, and popup dispatchers behind a registry with a bounded synchronous retry hook for dispatchers that report a transient failure; a channel failure never aborts sibling channels. No dispatcher currently shipped reports a transient failure (the existing transports swallow per-recipient errors internally), so retry is forward-looking scaffolding for now, not an active behavior on any channel.
- Added `prune_notifications` for expired and retention-aged canonical notifications (90 days by default), including cascading recipient and delivery cleanup.

### Changed

- Replaced the nullable `NotificationDelivery` uniqueness rule with partial constraints so immediate deliveries (`digest_threshold=None`) are race-safe while distinct digest thresholds coexist.

## [2.26.0] — 2026-07-16

### Fixed

- Bound each `/api/healthz` dependency check with a shared deadline, returning a fast 503 with per-check timeout attribution when a dependency stalls. During a sustained DB outage, wedged non-daemon healthz worker threads accumulate at roughly one per Kuma poll interval and are joined at interpreter exit; the request timeout does not bound them, so graceful shutdown relies on the process manager's SIGKILL grace period as a backstop.
- Added app-wide Redis socket/connect timeouts (2s), a Postgres connection timeout (5s), and Postgres TCP keepalives (30s idle, 10s interval, three probes). The keepalives detect a black-holed Postgres peer in roughly 60 seconds, bounding how long a single wedged healthz worker persists; a hard Redis outage now makes cached-db session cache writes fail fast while reads degrade to the database.
- Retried GitHub secret pushes with exponential backoff and now exit non-zero after reporting failed key names.

## [2.25.0] — 2026-07-15

### Added

**`pwa_install` universal onboarding step (registration only — UI lands in
`ui-core-micha`)**

Registers `pwa_install` in `UNIVERSAL_STEP_KEYS` alongside `cookie_consent`,
`complete_name`, and `browser_push`, so it is created with `enabled=True` by
default and independently toggleable per event via the existing
`OnboardingStepConfigView` admin PATCH endpoint — full parity with the other
universal steps. This is a backend registration change only; the actual step
UI (install prompt, `beforeinstallprompt` capture) is implemented in
`ui-core-micha`, gated there behind an explicit per-app opt-in prop so it
does not start appearing in apps that haven't verified their PWA manifest/
icons are actually installable.

## [2.24.0] — 2026-07-13

### Added

**`sync-secrets`: configurable bare-mode server targets**

Bare `sync-secrets` (no arguments) previously always iterated the hardcoded pair
`(staging, production)`. That fits app repos with one server per environment, but
not infra repos like `webapp-management` that manage several named servers — there
the phantom `production` target failed to resolve. `secrets.yaml` may now set
`config.bare_server_targets` to a non-empty list of server target names; bare mode
iterates those in order. Absent the key, behaviour is unchanged (`staging` then
`production`), so app repos are unaffected. Malformed values abort before any sync.

### Fixed

**Missing hard dependencies caused every published-package install (and the
publish CI's own test gate) to crash on import**

`settings_base.py` does a top-level `from corsheaders.defaults import
default_headers` and registers `corsheaders`, `rest_framework`, `channels`,
`allauth` (+ `.mfa`/`.account`/`.socialaccount`/providers), `whitenoise`
(`MIDDLEWARE` + `STORAGES`), and — transitively, via allauth's Google social
provider — `PyJWT` — none of which were declared in `pyproject.toml`'s
`dependencies`. Every consuming app happened to already install these itself,
masking the gap, until the publish workflow's own test gate (added in the
previous commit) installed `django-core-micha` in isolation and failed with
`ModuleNotFoundError: No module named 'allauth'`.

The first fix pass only added `django-environ`, `django-cors-headers`,
`djangorestframework`, `channels`, `channels_redis`, and `django-allauth[mfa]`.
Independent review correctly flagged that the existing test suite never
actually imports `settings_base.py` (`tests/settings.py` hand-rolls a minimal
settings module instead — see `test_channel_layer.py`), so passing tests were
not evidence that `corsheaders`/`whitenoise` were fixed. Added
`tests/test_settings_base_dependencies.py`, a regression test that imports
`settings_base` for real in an isolated subprocess and explicitly resolves
every `MIDDLEWARE`/`STORAGES` backend string (since `django.setup()` alone
does not import those lazily-referenced paths). Running it against the
reviewed fix caught `whitenoise` immediately, and after adding that, a second
gap: `ModuleNotFoundError: No module named 'jwt'` (needed by
`allauth.socialaccount.providers.google`, registered in `CORE_APPS`
unconditionally).

A second independent review pass then found a third, still-latent gap:
`requests` (also required by the Google OAuth2 provider) was only present by
accident, via `django-anymail`'s own unrelated transitive dependency on it —
the exact same masking pattern this whole fix exists to eliminate. Rather than
hand-declaring `requests`/`PyJWT` separately, switched to
`django-allauth[mfa,socialaccount]` — allauth's own `socialaccount` extra
already declares `oauthlib`, `requests>=2.0.0`, and `pyjwt[crypto]<3,>=2.0`
together, which is the exact transitive set `settings_base.py` needs and is
self-documenting instead of hand-rediscovered.

Verified three times over, each via a real disprove-then-reprove cycle in a
freshly created virtualenv (uninstall the package, confirm
`test_settings_base_dependencies.py` fails with the exact expected
`ModuleNotFoundError`, reinstall, confirm the full suite passes again) — for
`whitenoise`, `jwt`, and `requests` in turn. Final verification: a completely
fresh virtualenv, installed purely via `pip install -e ".[test]"` against the
final `pyproject.toml`, full suite green (295 passed). A full AST-based scan
of every top-level third-party import across all of `src/django_core_micha`
(not just `settings_base.py`) found nothing else missing; the one remaining
unscanned import (`asgiref`, used by `notifications/delivery.py`) is safe —
it's an unconditional hard dependency of Django itself, already covered by
`Django>=6.0.5`.

## [2.22.0] — 2026-06-15

### Added

**`/api/healthz`: three new readiness checks — migrations, config, version**

The health endpoint now covers the deploy-error classes that `db` + `cache`
cannot see:

| Check | What it catches | 503 on failure |
|---|---|---|
| `migrations` | Unapplied migrations — schema stale after deploy | yes |
| `config` | Missing critical config keys (e.g. `RESEND_API_KEY` when `EMAIL_PROVIDER=resend`) | yes |
| `version` | `APP_GIT_SHA` env var — stale-image detection for CI | no (info only) |

Implementation notes:

- **migrations**: uses `MigrationExecutor(connection).migration_plan(leaf_nodes)` —
  read-only, no `call_command('migrate')`, one DB query.
- **config**: checks key *presence* only; values are never serialised.
  Which keys are required is derived from live settings (`EMAIL_PROVIDER`,
  `AUTH_METHODS`), so apps without Resend or social login never produce a
  false-503.  Response shape: `{"ok": bool, "missing": ["KEY_NAME", …]}`.
- **version**: `{"version": "<sha>" | null}` at the top level — Kuma/CI can
  assert "running == pushed" without the endpoint knowing the expected SHA.

Response is backward-compatible: `status`, `checks.db`, and `checks.cache`
shapes are unchanged; `migrations` and `config` are new entries in `checks`;
`version` is a new top-level field.

A brief `degraded` window during the migrate-then-up deploy cycle is expected
and acceptable.

## [2.20.0] — 2026-06-11

### Changed

**`sync-secrets` bare invocation syncs both environments**

`sync-secrets` called with no arguments now runs a full GitHub-secrets sync for
`staging` and then `production` in sequence (identical code paths to the explicit
`--server --secret-target <target>` invocations). If the staging pass fails, the
production pass is skipped and the CLI exits non-zero.

Two new shorthand flags are also available:

| Flag | Equivalent to |
|---|---|
| `--staging` | `--server --secret-target staging` |
| `--production` | `--server --secret-target production` |

Explicit invocations (`--server --secret-target <target>`, `--local`) are
unchanged. Note: bare `sync-secrets` now mutates both GitHub environments on
every call — use the explicit flags when you only want to touch one.

## [2.19.0] — 2026-06-10

### Added

**Redis-backed sessions (`cached_db` on Redis db 2)**

`settings_base` now configures `CACHES["default"]` using Django's built-in
`RedisCache` backend, pointing at `redis://<REDIS_HOST>:6379/2` (db 2 — db 0
is used by the Channels pubsub layer and Celery broker in consumer apps; db 1
is commonly used by Celery result backends). `SESSION_ENGINE` is set to
`django.contrib.sessions.backends.cached_db` so session reads are served from
Redis while writes still hit Postgres. A Redis flush therefore costs only cache
misses, never logouts. No new infrastructure is required: all consumer apps
already declare a hard dependency on Redis via the Channels layer.

### Migration

- **No code change required** in consumer apps — the settings are applied
  automatically via `settings_base`.
- The Redis instance of each consumer app will start serving session reads on
  **db 2**. Apps that pin `maxmemory` on their Redis instance should account for
  the additional session key space (typically small, proportional to active
  concurrent sessions).
- The test settings override `CACHES` to `LocMemCache` so the CI test suite
  remains green without a Redis instance.

## [2.18.2] — 2026-06-10

### Fixed

**Query explosion in `BaseUserSerializer` for admin users**

`_admin_policy_satisfied` re-fetched the auth policy from the DB on every call —
~10x per `/api/users/current/` serialization (8 ui_permissions helpers +
`can_manage_support_agents` + `security_state`). Now memoized per request
(`request._dcm_admin_policy_cache`, invalidated by `set_security_level`).
`get_user_security_state` now reads all authenticator types in one query
instead of three `.exists()` calls. Permission semantics unchanged.

## [2.18.0] — 2026-06-09

### Added

**Pluggable Mail-Transport — `EMAIL_PROVIDER`**

`settings_base` now selects the email backend via the `EMAIL_PROVIDER` env var:

| `EMAIL_PROVIDER` | Backend | Required env vars |
|---|---|---|
| *(empty)* | `console` (IS_LOCAL/DEBUG) · `smtp` (otherwise) | same as before |
| `console` | Django console backend | — |
| `smtp` | Django SMTP backend | `EMAIL_HOST`, `EMAIL_PASSWORD` |
| `resend` | anymail Resend backend | `RESEND_API_KEY` |
| `postmark` | anymail Postmark backend | `POSTMARK_SERVER_TOKEN` |

**No crash on missing credentials.** When `EMAIL_PROVIDER` is set but the required credential is absent, a `warnings.warn` + `logger.warning` is emitted and the backend falls back to console — the app always boots.

**`EMAIL_PORT` / `EMAIL_USE_TLS`** now carry explicit defaults (`587` / `True`) at the call site; API-transport providers no longer crash when these vars are absent.

**`DEFAULT_FROM_EMAIL`** is now configurable via `env("DEFAULT_FROM_EMAIL", default=EMAIL_HOST_USER)`.

### Migration

- No change required for existing apps (no `EMAIL_PROVIDER` set → identical behavior).
- API transports (Resend/Postmark): set `DEFAULT_FROM_EMAIL` explicitly to your verified sender address (e.g. `DEFAULT_FROM_EMAIL=noreply@km-h.ch`) — `EMAIL_HOST_USER` will be empty for these providers and would produce an invalid From address.
- `django-anymail>=10` is now a required dependency; it is included automatically when bumping `django-core-micha`.

## [2.17.4] — 2026-06-02

### Fixed

**Auditlog — `AuditEvent.objects.create()` failure breaks outer DB transaction**

When `metadata` contained a non-JSON-serializable value (e.g. a raw `UUID` from a `context_resolver` that forgot `str()`), the `TypeError` from psycopg2's JSON adapter propagated through Django's `mark_for_rollback_on_error` context manager inside `_save_table`, setting `connection.needs_rollback = True`. The `except Exception` block in `_create_audit_event` then caught the error and logged it, but the outer transaction was already poisoned — every subsequent query in the same test (or request) failed with `TransactionManagementError`.

Fix: wrap `AuditEvent.objects.create()` in `transaction.atomic()`. Inside an existing transaction this creates a savepoint; if the create fails, only the savepoint is rolled back and the outer transaction remains intact.

### Migration required

- App bump `django-core-micha` → `==2.17.4` in `backend/requirements.txt`. No data migration.
- App `context_resolver` lambdas that return FK IDs should wrap with `str(...) if ... is not None else None` to avoid silent audit-write failures.

## [2.17.3] — 2026-05-31

### Fixed

**WebSocket channel layer — periodic `redis.exceptions.TimeoutError` crashing consumers**

`CHANNEL_LAYERS` used `channels_redis.core.RedisChannelLayer`, whose BRPOP-based receive loop raises `redis.exceptions.TimeoutError` ("Timeout reading from redis:6379") on idle WS connections with current redis-py / Python 3.14. Consumers crashed in a ~5s `WSDISCONNECT` loop, flooding logs and breaking live updates.

Switched to `channels_redis.pubsub.RedisPubSubChannelLayer`, which uses a persistent SUBSCRIBE instead of polling. All consumers across apps use only group semantics (`group_add`/`group_discard`/`group_send`), which the pub/sub layer fully supports — no consumer changes required.

### Migration required

- App bump `django-core-micha` → `==2.17.3` in `backend/requirements.txt`, then redeploy. No data migration.

Behavioural notes for WS-using apps (none of the current apps are affected — all use group-only consumers via standard ASGI dispatch):

- **Fire-and-forget:** no per-channel message capacity/backpressure. Apps relying on `channel_layer.receive()` on individual channels would need review.
- **No `group_expiry` TTL:** group membership is in-process and cleaned up on `disconnect()` (`group_discard`). A hard process crash leaves stale membership until restart — the old layer's 24h TTL had no equivalent here. Immaterial for short-lived connections.
- **Strip legacy `CONFIG` keys** from any app-level `CHANNEL_LAYERS` override before bumping: `RedisPubSubChannelLayer` rejects `expiry` / `group_expiry` / `capacity` / `channel_capacity` with a `TypeError` at consumer startup.
- **`group_add` requires standard ASGI dispatch:** the pub/sub layer registers the channel via `new_channel()` during dispatch; tests that poke the channel layer directly (outside `WebsocketCommunicator`) must call `new_channel()` before `group_add()`.

## [2.17.2] — 2026-05-31

### Fixed

**S212 follow-up — `ACCOUNT_RATE_LIMITS` 500 on every login (`ratelimit configured per user but used anonymously`)**

The S212 rate-limit config used the `/user` rate key for actions that allauth evaluates in an anonymous context. allauth consumes the `login_failed` limit inside `pre_authenticate` (before any user is known); a `/user` component there raises `ImproperlyConfigured`, surfacing as **HTTP 500 on every login attempt** in non-local environments. `password_reset` and `confirm_email` were affected the same way (both reachable while logged out).

Changed the anonymous-context limits to key on `/ip` and `/key` (the submitted identifier) instead of `/user`:

- `login_failed`: `5/5m/ip,10/h/user` → `5/5m/ip,10/h/key`
- `confirm_email`: `3/h/user` → `3/h/key`

Also fixed a latent typo: the reset-password limit was keyed `password_reset`, which is **not** an allauth action name — allauth merges this dict over its defaults and silently ignores unknown keys, so the entry had never taken effect (allauth's own `20/m/ip,5/m/key` applied instead). Renamed to the canonical `reset_password` and made it anonymous-safe: `5/h/ip,3/h/key`.

`reauthenticate` and `manage_email` keep `/user` (only reachable when authenticated). The dict is now exposed as `ACCOUNT_RATE_LIMITS_DEFAULTS`, and the regression test guards both the anonymous-context invariant and that every action name matches an allauth canonical key.

### Migration required

- App bump `django-core-micha` → `==2.17.2` in `backend/requirements.txt`, then redeploy. No data migration.

## [2.16.0] — 2026-05-28

### Added

**S211 / S212 / S213 — Audit-Log-Erweiterung: AuthN-Events, Brute-Force-Mitigation, DRF-AuthZ-Logging**

#### S211 — AuthN-Events persistent loggen

New signal receivers in `django_core_micha.auth.signals` create `AuditEvent` entries for every authentication lifecycle event. All events store a k-anonymised IP (`/24` for IPv4, `/48` for IPv6), a coarse UA family, and a session-key digest (sha256[:16]) — no full IP, no full UA string, no session secret.

New `event_type` strings (searchable in `AuditEvent.event_type`):

- `users.user.logged_in` — successful login
- `users.user.logged_out` — explicit logout
- `users.user.login_failed` — failed login attempt; metadata contains `credential_hash` (sha256[:8] of lowercased input), never plaintext
- `users.user.password_changed` — password updated while logged in
- `users.user.password_set` — password set for the first time (social-only → local)
- `users.user.password_reset` — password reset via email link
- `users.user.email.confirmed` — email address confirmed; metadata contains `email_domain`
- `users.user.email.added` — additional email address added; metadata contains `email_domain`
- `users.user.email.removed` — email address removed; metadata contains `email_domain`
- `users.user.mfa.authenticator_added` — MFA method enrolled; metadata contains `authenticator_type` (e.g. `totp`, `webauthn`, `recovery_codes`) — no secret/seed
- `users.user.mfa.authenticator_removed` — MFA method removed
- `users.user.mfa.authenticator_reset` — MFA method reset (e.g. recovery codes regenerated)
- `users.user.social.added` — social account linked; metadata contains `provider` + `uid`
- `users.user.social.removed` — social account unlinked
- `users.user.social.updated` — social token refreshed

MFA signal connections are deferred to `AppConfig.ready()` so the signals module is importable even when `allauth.mfa` is not in `INSTALLED_APPS`.

New helpers in `django_core_micha.auth._audit_helpers`: `_client_ip`, `_ua_family`, `_session_key_digest`, `_credential_hash`.

#### S212 — Failed-Login-Tracking / Brute-Force-Mitigation

Added `ACCOUNT_RATE_LIMITS` to `settings_base.py` using allauth's built-in Redis-backed rate limiter. Disabled in `IS_LOCAL` environments to avoid dev/test friction.

Configured limits:

| Key | Limit |
|---|---|
| `login_failed` | `5/5m/ip, 10/h/user` |
| `login` | `30/m/ip` |
| `signup` | `10/h/ip` |
| `password_reset` | `5/h/ip, 3/h/user` |
| `reauthenticate` | `10/m/user` |
| `confirm_email` | `3/h/user` |
| `manage_email` | `10/h/user` |

#### S213 — DRF AuthZ-Denial-Logging

Extended the existing `custom_exception_handler` in `django_core_micha.auth.exception_handler` to persist `AuditEvent` entries for access-control failures:

- `drf.not_authenticated` (HTTP 401)
- `drf.permission_denied` (HTTP 403)
- `drf.throttled` (HTTP 429) — metadata includes `retry_after` in seconds

All three include `view` (class name), `action` (ViewSet action or `None`), `method`, `path`. Actor is set to the authenticated user where available, `None` for anonymous. Audit write failures are logged but never abort the response.

### Migration required

None — all changes are signal receivers and settings. No new models.

## [2.15.1] — 2026-05-28

### Fixed

**S198 / auditlog — `models.E034` index-name-too-long blocked all consumer apps from migrating after dcm 2.15.0 bump**

The `AuditEvent` index `auditlog_event_type_created_idx` (31 characters) exceeded Django's default 30-character limit for index names. Django's system-check failed with `models.E034` before migrations could run, breaking deploys in every app that bumped to dcm 2.15.0. Renamed to `auditlog_evtype_created_idx` (27 chars); since 2.15.0 had not yet successfully deployed anywhere in production (the index never landed in any database), the initial migration is edited in place rather than chaining a rename-migration.

### Migration required

- App bump `django-core-micha==2.15.0` → `django-core-micha==2.15.1` in `backend/requirements.txt`.
- No data migration; the renamed index lands cleanly on first migrate.

## [2.15.0] — 2026-05-28

### Added

**S198 — Platform AuditLog (`django_core_micha.auditlog`)**

New app providing a reusable business-audit-event pattern for all platform apps.

- `AuditEvent` model with actor FK, `event_type`, `metadata` JSON (model, object_id, action, changes, before, after, request_id), `created_at`
- `register(model, redact_fields, context_resolver)` API — apps declare tracked models in `<top_app>/audit_config.py`, loaded automatically via `AuditlogConfig.ready()`
- `AuditlogActorMiddleware` — sets actor + request_id ContextVars per request (X-Request-ID header, falls back to generated UUID)
- Field-diff via pre/post-save signals; raw state captured, PII redaction applied before persistence (so PII-only changes are still recorded as events)
- `prune_audit_events` management command — `--days` override, `--dry-run`; default from `AUDITLOG_RETENTION_DAYS` setting (default 730 days)
- `AUDITLOG_RETENTION_DAYS` added to `settings_base.py` (env-tuneable per app)
- `AuditlogActorMiddleware` wired into `settings_base.py` MIDDLEWARE after `AuthenticationMiddleware`

## [2.14.0] — 2026-05-22

### Added

**S112 — WebSocket Permission Framework (`django_core_micha.auth.ws_permissions`)**

- `BaseSecureConsumer` — base Django Channels consumer with built-in permission checks
- `IsAuthenticated`, `IsObjectOwner`, `AllowAnonymous` permission classes
- `WSPermissionInventory` — startup check that all consumers declare permissions
- `generate-env` script: `ENV_TYPE` now defaults to `production` (fail-safe)
