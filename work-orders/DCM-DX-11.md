# DCM-DX-11 — Support local password recovery in the test center

## A. Envelope

Tier 3. Scope covers local test identities, password recovery and isolation. Extend the released DCM-DX-6 controller and client so an agent can request recovery as a signed-out user, consume the actual reset email privately and set a new password using the normal app confirmation flow. Innoservice is the acceptance consumer. Ship a documented shared capability usable through the DCM-DX-8 consumer contract.

Today the local branch of `invitations/emails.py::send_invite_or_reset_email` logs the URL and returns. During an isolated run, capture the real rendered reset message at the delivery boundary before that log path. Restrict capture and consumption to the active run and its owned password account. No external delivery, general-purpose mailbox reader, account impersonation or direct password setter. Keep the link, token and passwords inside the trusted client, including on failure. Preserve ordinary application responses, password policy, MFA and non-test delivery. Clear private material at reset/close and reject stale handles after recovery or teardown.

Required tests:
- In the real local Innoservice UI request a reset, complete the real form from the captured message, and sign in with the new password. The old password and reuse of the consumed link must fail. Another synthetic account keeps its original password and rights.
- Verify invalid/expired links, password validation, non-enumerating behavior for an unknown address, refusal for a non-owned account or another run, concurrent requests addressed to different users, and expiry of captured material on cleanup. Exercise existing request throttles and the affected auth tests.
- Prove no delivery outside the isolated instance, no sensitive output in logs or evidence and no change to normal route exposure. Demonstrate the workflow unattended through the installed dcm release in Innoservice. Document the release for downstream app WOs.

## B. Implementation map

Placeholder — the Orchestrator must finish mapping the current source and test files after Gate #1 and before dispatch.

Absolute target: `C:/Users/Micha Bigler/Documents/webapps/django-core-micha` and `C:/Users/Micha Bigler/Documents/webapps/innoservice`. Context anchors: dcm `docs/design/test-management.md`, `src/django_core_micha/invitations/{emails,mixins}.py`, `src/django_core_micha/test_management/`, and the Innoservice test-management client, isolated settings and browser specs.

### Implementer preamble

Start at the workspace root, run the hook canary and use the absolute targets below. This WO is the complete specification. Preserve concurrent changes. Edit only assigned source and tests. Run newly written tests once (tests-only author runs none). Leave dependencies, git, register, review and authoritative checks to the Orchestrator. Handle credentials inside the test client. Emit PLAN, periodic PROGRESS and one final RESULT.

## C. Orchestrator only

STOP here if you are the implementer. The Orchestrator owns the mapping, release/pin compatibility check, independent Tier 3 security and general reviews, affected auth and permission tests, and the local browser run. Record installed versions, commands, redacted outcomes and cleanup in Completion. Recheck any concurrently changed integration seams before dispatch.

## Gate record

The operator requested preparation and commit of this work order on 2026-11-19. Implementation approval is pending.
