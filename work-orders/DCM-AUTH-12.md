# DCM-AUTH-12 — Backend foundations for resumable setup: terms acceptance and checkout return

## Part A — Envelope (everyone)

**Repository:** django-core-micha, `main`. **Tier:** 3 (shared core, authenticated writes, schema and billing).

### Goal and expected outcome

Provide the two missing shared backend capabilities for Maana's first-use wizard:
an explicit, attributable terms-of-use acceptance and an unambiguous return from
Stripe Checkout to the originating flow. Existing consumers retain their current
behaviour unless they opt into these capabilities.

The operator requested these three implementation work orders on 2026-10-21,
following the approved MAANA-APP-18 prototype. They explicitly chose server-side
setup continuation, preparation before final processing confirmation, a 24-hour
lifetime for prepared imports, and explicit acceptance of the terms of use.

### Delivery order and dependencies

1. **This WO first:** publish the backend contract and an installable DCM release.
2. [UCM-AUTH-14](../../ui-core-micha/work-orders/UCM-AUTH-14.md) consumes that contract
   and publishes the shared presentation/client release.
3. [MAANA-APP-19](../../maana/work-orders/MAANA-APP-19.md) adopts both exact releases,
   enables the capabilities and implements the persistent app journey.

This WO has no dependency on either downstream implementation. Its public contract
is the authority for downstream payloads and errors. No circular dependency on
Maana models, routes, project IDs or wizard step names is allowed. Release numbers
are assigned at release time; downstream WOs use the actual published versions.

### Scope

**1. Explicit terms acceptance, opt-in per app.**

- Add a shared persistence/service boundary and authenticated read/accept API for
  the current user's acceptance of an app's configured terms document.
- Record the app identity, authenticated user, server-configured document version
  and URL, and server acceptance timestamp. Use a stable, app-owned version
  identifier; it is not supplied authoritatively by the browser. A submitted
  version must match the version actually offered by the server.
- Acceptance requires an explicit affirmative submission. Missing/false acceptance,
  stale versions and unknown/unconfigured documents fail without writing a record.
  Returning the current document metadata makes stale-version recovery possible.
- Repeated acceptance of the same user/app/version is idempotent, including
  concurrent requests; preserve the original evidence timestamp. A later version
  does not overwrite the historical record. No IP address or user-agent collection
  is required for this feature.
- Existing privacy acceptance and optional convenience-cookie preferences remain
  separate. Never infer terms acceptance from either field, an existing account,
  a Google/Microsoft login, a successful checkout or an administrator's edit.
- Normal callers can read/write only their own acceptance. Enforce session/CSRF
  protections; do not widen generic user-profile editing or user administration.
- Provide a server-side check for consuming apps to gate their setup operations.
  Enabling this capability does not introduce a global login/access gate for all
  users or all apps. Maana decides where its new setup journey requires it.
- Include the necessary DCM-owned schema migration and consumer installation
  instructions. Avoid forcing abstract-profile field migrations across the fleet.

**2. Explicit checkout return context.**

- Extend checkout with an optional app-configured context key. The server resolves
  success/cancellation destinations through an allowlisted configuration or hook;
  arbitrary browser-provided return URLs are not accepted. Omitted context retains
  the existing configured destinations and response compatibility.
- Bind each contextual checkout attempt to its authenticated user, app, plan and
  context, and expose an opaque correlation reference for resumption. Bind the
  Stripe session to this attempt. The return must distinguish a setup checkout
  from a later account checkout, including multiple tabs and repeated visits.
- Provide authenticated, ownership-checked resolution of that reference. Returning
  from Stripe or adding a success query parameter is never proof of payment or
  entitlement. Existing verified billing/webhook state remains authoritative.
- Support success with settlement still pending, cancellation, failure and retry.
  Retrying the same attempt after a network interruption must not create a second
  Stripe session or duplicate grants; a genuinely new attempt is explicit.
- Preserve existing subscription/token-package rules, webhook verification and
  grant idempotency. Include only the persistence necessary for this correlation,
  not a generic workflow engine.

**3. Public contract and release.**

Document settings/hooks, request/response/error shapes, idempotency, ownership,
required migrations and omitted-option compatibility in the repo's API contract
and consumer documentation. Publish a minor release for the new public APIs and
document adoption. No consumer pin changes belong to this WO.

### Non-goals / do not touch

- Maana's draft state, uploads, projects, processing, prices and trial policy.
- A general onboarding progress database, legal-text editor, legal content changes,
  forced fleet-wide reacceptance, consent withdrawal administration or operator UI.
- Auth provider configuration, signup/login semantics, MFA, account linking and
  automatic login after email confirmation.
- Infrastructure, CI changes, secrets, unrelated dependency upgrades and consumer
  releases. Existing foreign sync-secrets edits are outside this WO.

### Acceptance and required tests to write

- Valid self-acceptance persists the correct app/version/URL and server timestamp;
  concurrent/repeated submissions yield one logical record and stable evidence.
- Anonymous, foreign-user/app, missing/false, stale-version and forged-timestamp
  requests cannot create or alter acceptance. A profile PATCH cannot forge it.
- Privacy-only and social-login-only users remain unaccepted; a new document
  version requires an explicit new acceptance without rewriting history.
- Unconfigured consumers retain existing auth/profile and checkout behaviour.
- Checkout context maps only to configured destinations. Unknown keys, hostile
  URLs and foreign attempt references fail; setup/account attempts remain distinct.
- Replayed checkout creation uses the same session; cancelled/failed attempts can
  be retried intentionally. Forged success returns cannot unlock access; delayed
  and repeated webhooks preserve current idempotent billing behaviour.
- Migration from the previous release preserves existing users, profile fields and
  billing records; add coverage for new constraints and ownership boundaries.

### Risks and limits

The new document record is technical evidence of a submitted acceptance, not a
replacement for the app's published terms. Apps own document versions and URLs.
Stripe settlement can arrive after the browser returns; the contract must expose
that state instead of claiming immediate access. Local Maana currently pins DCM
3.13.0 while DCM is 3.15.0; its downstream adoption must inspect intervening changes.

## Part B — Implementation map (reserved for the Orchestrator)

**Not dispatch-ready.** The Orchestrator fills the complete implementation map,
absolute working directory, API contract slice, file/line anchors, progress
contract and implementer preamble after Gate #1. No implementer is dispatched
from this envelope.

Inspection starting points (not an implementation prescription):

- `src/django_core_micha/auth/models.py`, `auth/serializers.py`, `auth/views.py`:
  current privacy fields, self-edit boundary and explicit login after confirmation.
- `src/django_core_micha/billing/views.py`: `checkout` currently returns a URL.
- `src/django_core_micha/billing/stripe_service.py`: `checkout_session_params`
  currently reads fixed success/cancel settings.
- Existing billing persistence, webhook tests and migration conventions.
- `../webapp-management/SHARED_CAPABILITIES.md` for the shared ownership boundary.

## Part C — Orchestrator only

STOP — this part is not an instruction to an application implementer.

Status: planned; Gate #1 approved by the operator on 2026-10-21 in the Maana
Expertenchat (session local_cbfc9d51-5217-42a4-99ba-27a315c854ce), for all three WOs
of the chain, delivery order unchanged. Implementation and the minor release of this
WO are authorized; consumer pins and production remain outside it.

After approval, follow `orchestrate-codex` and the current configured roles. Define
the API contract before mapping/dispatch; finish required independent Tier-3 review,
including security, on the corrected diff. Run the affected auth, billing and
migration tests. No frontend review is needed unless the approved scope changes.
Record the published version, contract reference and completed evidence in this
register row so UCM-AUTH-14 and MAANA-APP-19 can consume an actual release.
Commit only this WO's files on `main`; no consumer or production promotion is part
of this WO. Production app activation remains a later app promotion gate.
