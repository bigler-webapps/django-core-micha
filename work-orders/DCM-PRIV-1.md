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

This is an implementation specification, not permission to start implementation. No server/secret/provider-console change, production promotion, framework migration, new analytics vendor or broad dependency refresh is included. Narrow shared-library adoption pins and necessary lock/migration changes are in scope after implementation approval. Other active work must remain intact.

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

## Part B — Implementation map (not dispatchable)

PLACEHOLDER — after Gate #1 the Orchestrator supplies absolute working directories, current named files/functions, the API/manifest contract slice, migrations and compatibility invariants, progress contract and mandatory implementer preamble. This Envelope alone is not a coder dispatch.

## Part C — Orchestrator only

STOP — this section is not an instruction to the implementer.

Gate #1 pending. The operator requested specifications and the prototype, not feature implementation. Follow orchestrate-codex before dispatch. Requirements-only authoring needs no independent code review; future Tier-3 source delivery does, including the security boundary and UI changes where applicable. Use the configured roles and the app's existing test harness. In joined apps, put the named cross-layer click path into the existing browser suite; do not introduce a harness or expand screenshots beyond the canonical prototype comparison merely for this rollout.

Reorient and re-read current instructions/pins at handoff. The shared visual reference is ui-core-micha/work-orders/assets/app-prototype.html@ced7a28 (375/1280 px); match composition and interaction using the consuming app's own theme. It covers only privacy choices and notice entry points. The Envelope governs behavior, permissions and data contracts, not prototype omissions.

Dependencies: none of the app privacy WOs may silently activate before this released contract and UCM-PRIV-1 are available. Coordinate the new public API through api-contract-design at mapping time. Initial seams: auth/models.py (legacy booleans), auth/serializers.py, auth/views.py, terms/ (separate terms acceptance), receipts/consent.py (separate AI grant), existing public endpoint/retention conventions. Do not incorporate the active billing or sync-secrets diffs. No prototype is needed for backend-only work.

