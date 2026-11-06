# DCM-PRIV-1 — Shared privacy information and purpose-specific consent

## Part A — Envelope

### Outcome and scope

Tier 3. Target: django-core-micha, main. Deliver an opt-in shared privacy module for authenticated and anonymous browser use, plus server-side enforcement helpers. It replaces cosmetic profile-level choices with an app-scoped, versioned contract; it does not become a central cross-app tracking service. Canonical rollout index: work-orders/PRIVACY-ROLLOUT.md.

### Shared product contract

- Operator decisions (2026-11-04): use prior opt-in for optional processing uniformly for CH/EU; retain visit measurement only after an explicit choice; use one shared prototype. No consent banner when no optional purposes exist.
- A privacy notice informs; it is not a blanket processing consent or a condition of access. Keep actual terms acceptance, study consent, AI disclosure/release and browser push/geolocation permissions separate.
- The deployment's actual controller, app/site, origins, languages, document versions and services define the scope. Do not assume bigler-consult is controller for a client-operated app or copy consent across apps/subdomains.
- Unknown, missing, expired, malformed or unavailable choice means optional processing is off. A recorded legacy boolean/cookie never counts as a purpose-specific opt-in.
- An explicit requested operation may use the storage actually necessary for it, with a documented justification and lifetime. Being local, first-party, cookieless, server-side or labelled "functional" does not by itself establish necessity.
- No optional request, SDK initialisation, iframe, beacon, identifier creation/read, prefetch or queued event replay before a valid choice. Revocation blocks new work immediately and clears only storage owned by the withdrawn purpose.
- Do not delete research files, customer drafts, tickets, carts, reaction ownership, account sessions or other essential functional data through a generic cookie-clear operation. Existing server records are governed separately; withdrawal does not falsely promise to undo past transmissions.
- Roll out on existing deployment/trunk conventions. Runtime/edge behavior and uploaded legal texts were not verified by the source inventory; each app needs bounded evidence on its actual routes before claiming complete coverage.

### Scope boundaries

No server/secret/provider-console change, production promotion, framework migration, new analytics vendor or broad dependency refresh is included. Narrow shared-library adoption pins and necessary lock/migration changes are in scope. Other active work must remain intact.

### Configuration, information and evidence

1. Define a validated per-deployment service manifest: stable app/site and purpose IDs, actual controller/contact, public privacy/document references per supported locale/version, service/provider, purpose, storage kind/name/scope, browser lifetime and server retention, request trigger, required/optional classification with justification, data categories/recipients/transfers, and withdrawal/cleanup coverage. A purpose may include only services explained by that purpose; unrelated purposes remain separately selectable.
2. Manifest output is public-safe metadata, never credentials, cookie contents, participant IDs, private endpoints or project data. Document references are validated URLs/content references, not an arbitrary server-side fetch/proxy capability. Keep existing terms acceptance separate.
3. Privacy notice presentation may be recorded as presented/delivered, with version and channel. Never describe this as read, understood or consented. Informational text changes do not require an acceptance checkbox or automatically invalidate all choices.
4. Provide an anonymous-safe read/write choice contract and an authenticated adapter using the same browser/site scope. Bind server evidence and enforcement to an opaque scoped receipt, never to an unauthenticated user-supplied profile/account ID. No account or cross-device synchronisation that activates optional services on another browser.
5. Record minimal evidence: purpose/service revision, explicit grants/refusals/withdrawal, receipt and effective timestamps, relevant locale/text revision and an opaque browser/app scope. Do not collect IP, user agent, fingerprint, page history or content solely for consent proof.
6. Default browser choice validity is 180 days for both grant and refusal; this is a product default, not a claimed statutory period. A materially changed/new optional purpose needs a new choice only for that scope. Keep valid unchanged purposes; do not re-prompt on every patch release or rejection. Evidence retention is explicitly configured and bounded (default 365 days from the event); implement pruning through an existing supported retention mechanism without changing production schedules in this WO.
7. Make decisions idempotent and concurrency-safe. Prevent stale tabs/replayed earlier grants from overriding a later withdrawal. Define ordering/revision conflicts and expiry server-side; never trust a client timestamp to extend validity.
8. The anonymous write surface needs CSRF/origin protections appropriate to its transport, bounded request sizes, purpose/version validation, abuse controls and no account-enumeration response. It is not an authentication or authorisation token.
9. A service-level guard checks a current grant before optional server-side measurement or processing. Clients cannot enable a disabled/unconfigured service. Provide a clear receipt-validation integration point for first-party analytics; no "UI only" enforcement.
10. A failed grant write must not activate optional services. Revocation/refusal blocks the browser immediately; server enforcement and retries reconcile the durable state without silently claiming persistence. Expose retryable state honestly; never queue optional events for later replay after denial.
11. Feature-disabled consumers keep their existing interfaces until explicitly adopted. Add schema/API support safely; retain deprecated profile fields for compatibility only, with no automatic conversion to grants. Remove them under DCM-PRIV-2 after consumers are migrated.

### Required tests to write

- Strict manifest and public-response minimisation; invalid/unconfigured purposes and unsafe document references rejected.
- Anonymous choices bound to app/site/browser, CSRF/origin failures, forged receipt/user IDs, expiry, idempotency, replay and cross-app/account isolation.
- Default-off before receipt, backend measurement denied without permission, immediate withdrawal/stale-tab conflict, failed saves and retry semantics.
- Selective re-consent after a substantive purpose revision, informational-only notice change, equal refusal/grant validity and bounded retention/pruning.
- Old profile booleans never grant the new purposes; opting out leaves unrelated terms, receipts-AI grants, study consent, sessions and financial workflows unchanged. Include the affected permission inventory and schema migration checks.

## Part B — Implementation map (addressed to the implementer)

Target working directory (absolute): `C:/Users/Micha Bigler/Documents/webapps/django-core-micha` (git root; never the workspace root). Python package under `src/django_core_micha/`. Work from this package; do not explore broadly from scratch; open only the named files to verify. If you must dig deeper, delegate to a read-only Explore sub-agent.

### Decision: new opt-in app `django_core_micha.privacy`

A new Django app, app label `dcm_privacy`, modelled on the existing opt-in `terms` app (`src/django_core_micha/terms/`: `apps.py` with a system check, `models.py`, `services.py`, `views.py` with `_no_store` + `@csrf_protect` + `@require_http_methods`, `urls.py` that the host app mounts itself, `audit_config.py`, `permissions.py`, `tests/`). It is NOT mounted from `api_urls.py` and is inert unless the host lists it in `INSTALLED_APPS`, defines `DCM_PRIVACY` and mounts `django_core_micha.privacy.urls`. Without `DCM_PRIVACY` every privacy endpoint answers 404 `{"code":"not_configured"}` and the guard answers "no grant".

Do NOT touch: `auth/models.py` (`accepted_privacy_statement`, `accepted_convenience_cookies` stay, deprecated, no conversion), `auth/serializers.py`, `auth/views.py:204`, `terms/` behaviour, `receipts/consent.py`, billing, sync-secrets files (the working tree carries foreign uncommitted edits in `scripts/sync_secrets.py` and `tests/test_sync_secrets.py` — leave them untouched), `api_urls.py`, any migration of another app.

### Files to create / change

- `src/django_core_micha/privacy/{__init__,apps,models,manifest,services,guard,views,urls,permissions,audit_config}.py`, `privacy/migrations/{__init__,0001_initial}.py`, `privacy/management/{__init__,commands/__init__,commands/prune_privacy_evidence}.py`, `privacy/tests/{__init__,test_*.py}`.
- `pyproject.toml`: bump `version` `3.21.1` -> `3.22.0` (additive minor); add `"src/django_core_micha/privacy/tests"` to `[tool.pytest.ini_options].testpaths` (`tests/test_testpaths_coverage.py` enforces this).
- `tests/settings.py`: add `"django_core_micha.privacy"` to `INSTALLED_APPS` (next to `django_core_micha.terms`).
- `CHANGELOG.md`: new `## [3.22.0] — 2026-11-06` entry above 3.21.1 (Added: privacy module; behaviour for apps that do not adopt it is unchanged; migration `dcm_privacy 0001`; the new `prune_privacy_evidence` command is not scheduled by this release).
- `docs/design/privacy.md`: short design note (manifest schema, endpoints, guard, retention, what is NOT recorded).
- `tests/test_declared_dependencies.py`: read it first; if it requires new top-level imports in `src` to be declared, satisfy it (use stdlib/Django/DRF only; add no dependency).
- Do not edit `WORK_ORDERS.md`.

### Contract slice (binding; UCM-PRIV-1 builds against exactly this)

**The host setting is `DCM_PRIVACY`** (dict, strict: unknown keys at any level are a check error). Validate in an `AppConfig.ready()` system check (`dcm_privacy.E001..`, `W001` when absent), like `terms/apps.py`, reusing the style of `terms/services.py::validate_document_setting` (ASCII regexes, bounded lengths, a `(parsed, errors)` result). Shape:

```
DCM_PRIVACY = {
  "app": "<slug [a-z0-9_-]{1,80}>", "site": "<slug>",
  "origins": ["https://host", ...],          # exact allowed Origin values for the write API; optional, default = same-host only
  "locales": ["de", "en"],                   # non-empty, [a-z]{2}(-[A-Z]{2})?
  "controller": {"name": str<=200, "contact": str<=200},   # a name and an email address or http(s) URL
  "notice": {"version": "<[A-Za-z0-9._-]{1,64}>",
             "documents": {"<doc_id [a-z0-9_-]{1,40}>": {"<locale>": "<reference>"}}},  # every declared locale present per doc
  "choice_validity_days": 180,               # 1..400
  "evidence_retention_days": 365,            # >= choice_validity_days, <= 1095
  "purposes": [ {                            # 0..20; ZERO purposes = no banner, endpoints still answer notice info
      "id": "<[a-z][a-z0-9_-]{1,40}>", "revision": <int>=1,
      "required": false,                     # true => never gated, never stored as choice; requires "justification"
      "justification": "<str<=500>",         # mandatory iff required
      "label": {"<locale>": str<=80}, "summary": {"<locale>": str<=400},   # plain text, every locale present
      "services": [ {
          "id": "<slug>", "provider": str<=120, "trigger": str<=200,
          "data_categories": [str<=80], "recipients": [str<=120], "transfers": [str<=120],
          "browser_storage": [ {"kind": "cookie|localStorage|sessionStorage|indexedDB|none", "name": str<=80, "scope": str<=120, "lifetime": str<=60} ],
          "server_retention": str<=120 } ],    # 0..10 per purpose
      "withdrawal_clears": [str<=80]         # storage names owned by this purpose only; must be a subset of its services' browser_storage names
  } ]
}
```
A `reference` is either an absolute `http(s)` URL (hostname present, no userinfo, <=500 chars) or a site-relative path starting with a single `/` (not `//`, no scheme, no backslash). It is never fetched or proxied. Purpose IDs and service IDs are unique across the manifest. A storage name may be owned by only one purpose.

**Public manifest** `GET manifest/` (no auth, `Cache-Control: no-store`): returns `{app, site, locales, controller, notice:{version, documents}, choice_validity_days, purposes:[{id, revision, required, justification (if required), label, summary, services:[public fields incl. browser_storage, data_categories, recipients, transfers, server_retention], withdrawal_clears}], necessary_storage:[...], config_version}`. Whitelist-build the response from the validated structure (never dump the setting); no `origins`, no internal keys. `necessary_storage` always includes the system entry for the receipt cookie below. `config_version` = hex sha256 of the canonical JSON of the public payload.

**Receipt.** Server-generated random 128-bit id (`secrets.token_urlsafe(16)` or uuid4), created only on the FIRST successful choice write (no identifier before a choice). Delivered as a host-only cookie named `dcm_privacy_<site>` (no `Domain` attribute), `HttpOnly`, `SameSite=Lax`, `Secure` mirroring `SESSION_COOKIE_SECURE` (default True when unset), `Max-Age = choice_validity_days*86400`, `Path=/`, value = `django.core.signing` signed value (salt `dcm-privacy-receipt`, payload binds `app`, `site`, receipt id). Cookie is never refreshed by reads; server expiry is authoritative. Reading a forged/foreign-scope/expired/unknown cookie = "no receipt" (state unset), never an error that discloses why. The receipt is not tied to `request.user` in any way: no user FK, no account lookup, no cross-device sync; an authenticated session uses the identical browser-scoped receipt (the "authenticated adapter" is simply that the same endpoints and guard work unchanged for authenticated requests, with CSRF applying equally).

**Models** (`dcm_privacy_*`): `PrivacyReceipt` (receipt_id unique, app, site, created_at, seq int — receipt-level monotonic revision); `PrivacyChoice` (receipt FK, purpose_id, purpose_revision, state `granted|refused`, effective_at, expires_at, UNIQUE(receipt, purpose_id)); `PrivacyDecision` (append-only evidence: receipt FK, purpose_id, purpose_revision, kind `granted|refused|withdrawn`, seq, decided_at server time, locale, notice_version, channel `banner|settings|other`, request_id, UNIQUE(receipt, request_id, purpose_id)). No IP, user agent, fingerprint, page, referrer, user id. Register `PrivacyDecision` with auditlog only if `auditlog.register` fits without storing extra data; otherwise skip and say so in the RESULT line. Timestamps are server time only.

**Choice API** (all under the host's mount point; every response `Cache-Control: no-store`, JSON, errors `{"code": "<code>", "retryable": bool, ...}`):
- `GET choices/` -> `{receipt_state: "unset"|"present", seq: int, config_version, notice_version, choices: {"<purpose_id>": {"state": "granted|refused|unset", "revision": int, "expires_at": iso|null}}}`. Reads create nothing and set no cookie. Only optional purposes of the CURRENT manifest are listed; a stored choice whose `purpose_revision != manifest revision`, or that is expired, reads as `unset`.
- `POST choices/` (anonymous allowed; `@csrf_protect`; also reject a present `Origin` that is not in `origins`/same-host with 403 `origin_not_allowed`; body <= 4096 bytes else 413; strict JSON object; keys exactly `request_id` (uuid string, required), `expected_seq` (int>=0, required), `notice_version` (must equal current), `locale` (declared), `channel` (enum), `choices` (non-empty object, `{purpose_id: "granted"|"refused"}`, only optional configured purposes; unknown/required purpose => 422 `unknown_purpose`, nothing stored): applies atomically (`transaction.atomic` + `select_for_update` on the receipt row where the backend supports it; must also be correct on sqlite). Creates the receipt + sets the cookie on the first write. **Ordering rule:** a `refused` write (including granted->refused, recorded as kind `withdrawn`) is ALWAYS applied regardless of `expected_seq` (the safe direction); a `granted` write for a purpose is applied only if `expected_seq == receipt.seq`, else the whole request is rejected with 409 `stale` returning the current `GET` state (so a stale tab or replayed older grant can never override a later withdrawal). Each applied request increments `seq` once. **Idempotency:** a repeated `request_id` on the same receipt returns the original outcome without new rows or seq change. Validity: `expires_at = server now + choice_validity_days` for granted and refused alike; never derived from client input. Success -> 200 with the same shape as `GET` (+ cookie). Failure modes: DB error => 503 `unavailable` `retryable: true` with no partial write and NO cookie; throttle => 429 `throttled` `retryable: true` + `Retry-After`; CSRF => 403.
- Abuse control: per-client-address throttle via `django_core_micha.auth.client_ip.resolve_client_ip` and the cache-backed counter pattern in `billing/declarations.py::_throttle` (copy the pattern; do not import from billing), with a generous bound (e.g. 60 writes/hour/address) and a smaller bound on receipt CREATION (e.g. 10/hour/address). No account-enumeration surface exists; responses must not differ based on whether a user is logged in.
- Revocation is expressed as writing `refused`; there is no DELETE.

**Guard** (`privacy/guard.py`, exported in `privacy/__init__`-free style: import from `django_core_micha.privacy.guard`): `receipt_for_request(request) -> PrivacyReceipt|None`; `has_grant(request, purpose_id) -> bool` (True only for a configured OPTIONAL purpose with a current-revision, unexpired `granted` choice on a receipt valid for this app+site; unknown/unconfigured/absent-setting => False; calling it for a `required` purpose raises `ImproperlyConfigured` — programmer error, not client-reachable); `require_grant(request, purpose_id)` raising `PrivacyGrantRequired`; DRF `permissions.HasPrivacyGrant` as a factory `HasPrivacyGrant("purpose_id")` returning a permission class (instance-not-callable pitfall: see `terms/permissions.py`; the repo's permission inventory tests reject callable-instance mistakes) whose denial code is `privacy_grant_required`. The guard reads the server state on every call (no process cache) so a withdrawal is effective immediately.

**Retention.** Management command `prune_privacy_evidence` following `activity/management/commands/cleanup_activity_buckets.py` (`--older-than-days`, `--dry-run`, default from the manifest `evidence_retention_days` else 365): delete `PrivacyDecision` rows older than the window, then `PrivacyChoice` rows expired for longer than the window, then receipts with no remaining rows older than the window. Never touches terms, receipts-AI consent, users, sessions. Do not add any schedule.

**Legacy and isolation invariants.** Old `Profile.accepted_privacy_statement` / `accepted_convenience_cookies` are never read by the new module and never create a grant; opting out/refusing touches no `TermsAcceptance`, no `receipts` AI grant, no session, no user data. Existing endpoints and serializers answer exactly as before for every host that has not adopted the module.

### Tests to write (in `src/django_core_micha/privacy/tests/`; run only the ones you write)

Mirror the Envelope's list one-to-one: manifest validation (strictness, reference safety, duplicate IDs, required without justification, locale completeness, withdrawal_clears subset) and public-response whitelist (no `origins`, no internals); anonymous write binds to app/site and browser (forged cookie, cookie from another site/app, tampered signature, unknown receipt => unset), CSRF and bad-Origin 403, oversize 413, unknown/required purpose 422 with nothing stored, throttle 429; default-off before any receipt; `has_grant` false for unconfigured/absent setting and for legacy profile booleans set True; ordering rule (stale grant 409 leaves the later withdrawal intact; stale refusal still applies), idempotent replay, equal 180-day validity for grant and refusal (use `freezegun` only if already a test dependency, else patch `timezone.now`), expiry => unset, selective re-consent (bump one purpose `revision` => only that purpose reads unset; an informational `notice.version` change alone invalidates nothing — a POST with the old notice_version is rejected with 409 `stale_notice`-style code, GET still shows choices), failed save (simulated DB error) => 503, no cookie, no rows, guard still false; retention command (dry-run, window, receipts untouched while younger); isolation (refusing leaves `TermsAcceptance`, receipts consent, session untouched); system check cases; a migration check (`makemigrations --check --dry-run` clean for `dcm_privacy`).

### Progress contract

Emit a `PLAN: <step1> | <step2> | …` line first, then a single-line `PROGRESS: [n/total] <present-tense action>` before every relevant action (and `… done`), spaced so no gap exceeds ~2 minutes, stdout unbuffered, and exactly one final `RESULT: DONE` or `RESULT: BLOCKED <reason>`. Use explicit UTF-8 file I/O (`encoding="utf-8"`).

### Preamble (mandatory, in-file)

> The text above is the COMPLETE spec — the committed WO file's content, not a plan to refine; there is no separate plan file. Read the nearest `AGENTS.md`, the relevant `.codex/skills/<role>/SKILL.md`, and the app `MEMORY.md` ONLY for conventions. Stay in scope; do not touch auth/permissions/deps/schema/CI unless the spec says so; do not update `MEMORY.md`. **Do NOT edit `WORK_ORDERS.md` — the register row and the review verdicts are the orchestrator's alone.** **Your tools are for editing source and test files and for running the tests you wrote — nothing else.** Do NOT install dependencies, touch a lockfile, run a package manager, or tidy up stray files; if something in the repo state blocks you, stop and report it as `RESULT: BLOCKED <reason>` instead of fixing it. Do NOT `git add`/`commit`/`push` — leave every change uncommitted in the working tree for the orchestrator's independent review. WRITE the tests the `Required tests` section calls for AND **RUN the tests you just wrote** to confirm they execute and pass — that is the ONLY test run you do (NOT the app's affected/full suite, NOT any review). The orchestrator re-runs the authoritative set + does the independent review after you finish — those are the gate; your own run does not count as the gate.

## Part C — Orchestrator only

STOP — this section is not an instruction to the implementer.

Gate #1 approved by the operator 2026-11-06 (Codex Expertenchat, session 01a1a807-3193-7793-8e43-50d6d7fd7263). Approval covers this Envelope and the shared privacy prototype/copy, accepted after the interactive preview. The canonical visual reference remains ui-core-micha@ced7a28. Dependencies and activation prerequisites still apply; production promotion is not approved. The Orchestrator must complete Part B and the mandatory preamble before dispatch. Follow orchestrate-codex before dispatch. Requirements-only authoring needs no independent code review; future Tier-3 source delivery does, including the security boundary and UI changes where applicable. Use the configured roles and the app's existing test harness. In joined apps, put the named cross-layer click path into the existing browser suite; do not introduce a harness or expand screenshots beyond the canonical prototype comparison merely for this rollout.

Reorient and re-read current instructions/pins at handoff. The shared visual reference is ui-core-micha/work-orders/assets/app-prototype.html@ced7a28 (375/1280 px); match composition and interaction using the consuming app's own theme. It covers only privacy choices and notice entry points. The Envelope governs behavior, permissions and data contracts, not prototype omissions.

Dependencies: none of the app privacy WOs may silently activate before this released contract and UCM-PRIV-1 are available. Coordinate the new public API through api-contract-design at mapping time. Initial seams: auth/models.py (legacy booleans), auth/serializers.py, auth/views.py, terms/ (separate terms acceptance), receipts/consent.py (separate AI grant), existing public endpoint/retention conventions. Do not incorporate the active billing or sync-secrets diffs. No prototype is needed for backend-only work.


Launch evidence (2026-11-06): implementer `codex exec -m gpt-6-luna --approve-for-me` (banner read back: model gpt-6-luna, reasoning xhigh), RESULT: DONE, 42 own tests. Review: codex/gpt-6-luna, lenses envelope, regression, duplication, tests + security (round 1), closing pass envelope, regression, tests, security. Orchestrator-fixed after round 1: choice pruning from effective_at, `config_version` on writes, service-aware guard, sharper tests. Closing pass: bounded body read added. Carried/rejected with reasons: two-tab first-write race (client duty, documented in docs/design/privacy.md and bound into UCM-PRIV-1); `--older-than-days` below choice validity is an operator override; server-side revocation after a failed write is per Envelope item 10; `resolve_client_ip` trust is the estate-wide Cloudflare convention; study-consent/financial isolation not applicable to dcm models.
