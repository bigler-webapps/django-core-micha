# DCM-DX-20 — Instance-bound protective identity for isolated consumers

## A. Requirements envelope

**Status: planned; implementation Gate #1 is open.** Tier 3: shared-core isolation, authentication and recovery. This document authorizes preparation only. No schema migration, implementation, release, consumer pin change, runner change or live deletion is authorized by this document.

### Goal and outcome

Enable an opted-in, verified disposable test-management consumer such as Bigler Consult to preserve an instance-bound protective identity across independent run reset and close while synthetic run-owned Owners use ordinary application authentication. The identity must remain bound to one verified instance, cannot be claimed as a run-owned account or managed by ordinary application users, and cannot authenticate or recover. The consumer's ordinary last-active-Owner protection and MFA policy remain in force. Unrelated accounts, sessions, mailbox state, receipts and history survive.

### Current evidence and boundary

`TestInstance` records the instance identifier and lifecycle but has no application-user binding. `TestRun.adapter_state` and `TestAccount` belong to a run, not to the instance. BCO's existing last-active-Owner guard prevents removal of the sole active Owner on reset or close; an ordinary Owner may also manage another Owner unless the protective identity is explicitly excluded.

The shared MFA recovery service checks `can_user_authenticate` and then signs in using Django's `ModelBackend`; filtering the user-management queryset or installing a custom backend alone does not prove recovery is denied. Password reset, MFA support and recovery, token/session issuance, authenticated management and stale sessions require explicit coverage. No secret or customer data is needed.

DCM-DX-19 already provides explicit, verified instance disposal in 3.42.0 and preserves stop/resume until disposal. This WO does not change that contract or authorize live disposal. The central acceptance runner's choice of stop versus disposal is a separate governance matter. BCO receipt, mailbox, payment and reimbursement fixture ownership and cleanup belong to the consumer WOs; their foreign history must not be altered by this capability. DCM-DX-16 concerns pgvector rather than this identity boundary.

### Design decision required before implementation approval

Compare and document both options against the same acceptance conditions before Gate #1:

1. Durable app-local identity-to-instance association, with a shared authentication and recovery checkpoint that can reliably recognize and reject the bound identity.
2. Explicit shared-core instance-to-identity binding, potentially requiring a schema migration, with equivalent shared authentication and recovery enforcement.

Assess uniqueness and ownership proof; collisions and forged or stale bindings; instance startup, restart and stop/resume; concurrency and interrupted creation; instance disposal; compatibility with existing consumers and installations; testability and operational cost; risk of leaking identity or changing production authorization; and whether both ordinary authentication and the direct recovery service actually consult the same enforceable decision. Prefer the least invasive option that demonstrably meets every condition. If neither is sufficient, document the exact missing contract. A migration is an option for decision, not an approved requirement. Return the chosen design and any schema or sensitive scope to the operator for explicit Gate #1 approval before implementation.

### Acceptance and tests to write

- In a verified isolated instance, bind one protective identity uniquely and idempotently; reject foreign, duplicate, stale, forged and cross-instance claims, including simultaneous creation and interrupted startup. Fail closed when instance or binding proof is missing.
- Exercise ordinary Owner login with normal MFA behavior; reject protective-identity password login, password reset and confirmation, MFA support and recovery, token issuance and consumption, and session creation or reuse, including the direct `ModelBackend` recovery route. Do not expose protected identifiers or grant management access through list, detail, update, role, deletion or indirect actions.
- Reset and close a run containing a genuine Owner and sessions: remove only its resources, retain the protected instance identity and normal last-owner rule, and preserve unrelated run and non-run sentinels. Verify failed or interrupted cleanup is recoverable and cannot partially damage foreign resources. BCO-specific receipt and mailbox graphs remain consumer evidence.
- Stop and resume the same verified instance without losing or duplicating its binding; prove an independent instance cannot claim it. Establish that verified DCM-DX-19 disposal removes the dedicated identity and data only through separately authorized live evidence; no live destructive action is implied here.
- Exercise production and non-isolated configurations, existing consumers, protected-file and permission inventories, ordinary authentication, recovery and MFA regression paths; no test endpoint, binding or weakened guard is available outside its authorized boundary. Test schema upgrade and compatibility only if a migration is subsequently approved.

## B. Implementation map

- `src/django_core_micha/test_management/models.py`, `protocol.py`, `services.py` and `activation.py` define instance, run, account and isolated activation contracts.
- `src/django_core_micha/auth/access.py`, `services.py`, `views.py` and `adapters.py` contain authentication and recovery paths.
- `docs/design/test-management.md` and `work-orders/DCM-DX-19.md` define stop/resume and disposal boundaries.
- `tests/test_test_management_*.py` and the auth/recovery tests contain shared security and lifecycle coverage.
- `bigler-consult/backend/consulting/test_management.py`, `backend/users/models.py` and `backend/users/views.py` expose the consumer integration and last-Owner constraint.

## Preamble

> You are a future dispatched implementer, not the Orchestrator. Do not delegate, review, modify the register, run codex, commit or push. Run `codex-hook-canary-ws-gov-60` first. Follow the approved final Envelope and assigned paths, preserve other sessions' changes, never read environment files or secrets, and do not install dependencies or operate Docker. Write the approved focused tests and run them at most once. This preparation is not implementation approval.

## C. Orchestrator only

> Implementer: stop here. This section is for the Orchestrator.

Before dispatch, obtain and record the operator's selected design and Gate #1. Update the Envelope only for the approved design and record any explicitly approved schema change. The independent security and general reviews, affected tests, release/consumer compatibility and any separately approved live disposal evidence remain required. Coordinate BCO and workspace runner work separately.

## Gate record

Preparation authorized by the operator on 2026-11-22. Gate #1 for implementation and any migration remains open.
