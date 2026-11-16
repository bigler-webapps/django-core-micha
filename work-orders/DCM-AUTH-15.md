# DCM-AUTH-15 — Secure registration extension for application-owned grants

## Part A — Envelope

**Target:** django-core-micha, `main`.

Tier 3 — shared authentication and registration.

### Goal and scope

Allow a consumer application to collect an optional application-specific registration value and bind it securely to a pending email registration, so it can apply an entitlement atomically only when the email is confirmed. The consumer must be able to store and look up an irreversible representation server-side; only an opaque, unguessable reference may cross the existing confirmation link. The raw value must never be placed in the link, signed payload, logs or persistent client storage. The reference must be bound to the pending registration and unusable for another registration, expired confirmation or replay. App-specific quota, credit, entitlement and policy remain owned by the consumer, as does the persistent storage of its binding.

The shared registration flow must expose narrowly scoped extension seams for validated optional input, preparation of the opaque binding after existing anti-abuse and policy checks, and its use inside the existing user-creation transaction. A failed application step must roll back user creation and all grant effects. Disabled or unconfigured extensions keep the existing registration behavior, request contract, generic access-code policy, QR handling, provider checks, security controls and response privacy. No grant or consumption happens merely because registration was requested. Preserve confirmation in another browser, and do not disclose whether an account exists.

No Maana-specific entitlement, frontend, ledger, subscription or generic-code redesign is in scope. Any necessary non-destructive shared schema change is in scope; no destructive migration. Document the extension contract, error behavior and lifecycle responsibilities for its consumer.

### Required tests to write

Cover opt-in request validation, no extension/default parity, anti-abuse ordering, no raw value in URLs, signed payloads and logs, expiration, another-browser confirmation, binding to the correct registration, replay and concurrent confirmation, failed writes and transactional rollback. Verify application extension failures do not consume generic codes or QR quotas, and existing generic single-use behavior and account-enumeration protections remain intact. Include changed-endpoint CSRF, rate-limit, authorization and registration security regressions.

### Risks

Confirmation is a security boundary. An opaque reference alone is insufficient unless bound to the confirmed registration and safely consumed; consumer hooks must not weaken provider, token, throttle or transaction guarantees.

## Part B — Implementation map

Target `C:/Users/Micha Bigler/Documents/webapps/django-core-micha`. `src/django_core_micha/auth/views.py`: `BaseUserViewSet.register_request`, `register_confirm`, and `apply_registration_context`; `auth/serializers.py`: `RegistrationRequestSerializer`; `auth/policy.py`: pending registration signing and expiry. `register_confirm` applies context within `transaction.atomic` after creating the verified user, and returns a privacy-preserving response for an already-existing user. Generic codes are separately handled by `invitations/access_codes.py`; do not alter their semantics. Existing tests live under `tests/`, and auth contract documentation under `docs/`. Implement the smallest reusable, opt-in integration seams supporting Part A, not a Maana-specific implementation. Edit source, relevant tests and contract docs only. Do not touch dependencies or unrelated schema.

### Implementer preamble

Your first tool action is the exact command `codex-hook-canary-ws-gov-60`; the expected denial is `WS-GOV-60 canary: workspace hooks are active`. Continue after it; if absent, report unverified coverage. This is the COMPLETE spec. You are the IMPLEMENTER, not the orchestrator. Work only on the approved scope, source, tests and contract docs. Run only tests you wrote, once. Do not install dependencies, run package managers, edit registers or memory, launch other agents, review, stage, commit or push. Report blockers without expanding scope. Start with `PLAN: ...`, emit `PROGRESS: [n/total] ...` before relevant actions and on completion, and finish `RESULT: DONE` or `RESULT: BLOCKED <reason>`.

## Part C — Orchestrator only

STOP: implementer instructions end here. Do not dispatch, review, update register, commit or push.

Operator explicitly approved this auth dependency on 2026-11-16 in the MAANA-APP-23 chat. Run configured implementation, independent Tier-3 reviews, auth/security regression tests and release sequencing. Existing MAANA-APP-23 prototype and deployment gates remain binding.

## Gate record

Gate #1 approved by the operator 2026-11-16: "Ja, freigeben" to the separately scoped secure registration binding including replay protection and existing-registration parity.
