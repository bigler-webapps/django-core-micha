# DCM-DX-11 — Support local password recovery in the test center

## A. Envelope

Tier 3. Scope covers local test identities, password recovery and isolation. Extend the released DCM-DX-6 controller and client so an agent can request recovery as a signed-out user, consume the actual reset email privately and set a new password using the normal app confirmation flow. Innoservice is the acceptance consumer. Ship a documented shared capability usable through the DCM-DX-8 consumer contract.

Today the local branch of `invitations/emails.py::send_invite_or_reset_email` logs the URL and returns. During an isolated run, capture the real rendered reset message at the delivery boundary before that log path. Restrict capture and consumption to the active run and its owned password account. No external delivery, general-purpose mailbox reader, account impersonation or direct password setter. Keep the link, token and passwords inside the trusted client, including on failure. Preserve ordinary application responses, password policy, MFA and non-test delivery. Clear private material at reset/close and reject stale handles after recovery or teardown.

Required tests:
- In the real local Innoservice UI request a reset, complete the real form from the captured message, and sign in with the new password. The old password and reuse of the consumed link must fail. Another synthetic account keeps its original password and rights.
- Verify invalid/expired links, password validation, non-enumerating behavior for an unknown address, refusal for a non-owned account or another run, concurrent requests addressed to different users, and expiry of captured material on cleanup. Exercise existing request throttles and the affected auth tests.
- Prove no delivery outside the isolated instance, no sensitive output in logs or evidence and no change to normal route exposure. Demonstrate the workflow unattended through the installed dcm release in Innoservice. Document the release for downstream app WOs.

## B. Implementation map

### Verified seams (dcm part, mapped 2026-11-19 against `main` 4aae57c)

- Capture point: `invitations/emails.py::send_invite_or_reset_email`, local branch (`ENV_TYPE == "local"`) at the `logger.info` before `return`. Callers: `invitations/mixins.py` `reset_request` (non-enumerating, unknown address returns `Auth.RESET_REQUEST_ACCEPTED`, throttle scope `password_reset`) and the invite action (`is_new_user=True`, out of scope). Capture only when `is_new_user` is false. `send_pending_registration_email` is not touched.
- Management API: `test_management/{urls,views,services,models,serializers}.py`. Views derive from `ManagementView` (bearer auth, loopback only, no browser origin). Run and account lookup by `services._run` / `get_account` (ownership already enforced; closed run refused). Adapter-owned `TestAccount.user_ref` is the app user's primary key as string (Innoservice adapter: `str(user.pk)`); match captures by `account.user_ref == str(user.pk)` for an ACTIVE account of an ACTIVE run, never by email alone.
- Cleanup: `services._finish_run` (reset/close) and `delete_account` are the places that must destroy captured material; `activation.ensure_instance` marks runs RECOVERABLE on restart.
- Controller path: the client only reaches the container through `local_bridge.py::_ROUTES` (explicit allowlist) and its `_redact` (drops response keys matching `password|token|cookie|authorization|secret`). A new route needs an allowlist entry; the response key carrying the link must not match that pattern (use `link`). `http_relay.py` already blocks `/api/test-management*` for browser traffic; keep it unchanged.
- Isolation proof: `activation.validate_configuration()` is the full isolation check; capture must call it (fail closed, no capture on failure) and be a no-op when `django_core_micha.test_management` is not in `INSTALLED_APPS` or the setting `TEST_MANAGEMENT_ENABLED` is not true.

### Design (binding)

1. New model `TestMailMessage` (+ migration): run FK, account FK, `handle` UUID, `kind` (`password_reset`), `link` (text, blanked when no longer needed), `status` (`captured`, `claimed`, `discarded`), timestamps. Only the reset message for an owned account is stored; subject/body are not stored.
2. New module `test_management/mail_capture.py` with `capture_password_reset(user, url)`: returns True only when it stored a message. Any exception is swallowed after a fixed non-sensitive log code; the ordinary response must be unchanged. A newer capture for the same account discards the older one.
3. `send_invite_or_reset_email`: before the existing local log line, call the capture for `is_new_user=False`; when captured, log a fixed redacted line without URL; otherwise the existing behaviour is byte-for-byte unchanged. No other delivery path is added.
4. New controller route `POST /api/test-management/runs/<run>/accounts/<account>/recovery-message/` returning `{"handle", "link"}` once; the message becomes `claimed` and its link is blanked. Unknown, already claimed, discarded, wrong-run or non-owned account returns 404 `recovery_message_not_found` without distinguishing causes. Add it to `urls.py`, `views.py`, `services.py`, `serializers.py` as needed and to `local_bridge._ROUTES` (POST only). Never log or return the link elsewhere.
5. Destroy captured material (rows or blanked links, status `discarded`) in `_finish_run` for reset and close, in `delete_account`, and when the account's run is recovered; a stale handle is therefore rejected.
6. Documentation: extend `docs/design/test-management.md` with the capability, the client flow (signed-out request through the real UI/API, claim the link inside the trusted client, complete the app's own form, sign in) and the rule that the link, token and passwords never leave the client. Bump patch/minor version and note the release for downstream WOs only as the Orchestrator directs.

### Tests to write (dcm)

New `tests/test_test_management_mail_capture.py`: capture happens only for an owned active account of an active run; not for unknown address, other run, other account, invite (`is_new_user=True`), disabled setting, failed isolation or app not installed; the ordinary `reset_request` response is identical with and without capture; exceptions in capture do not change it; the URL is absent from logs; claim returns the link once and the second claim, closed run, reset, close and account delete give 404; newer capture discards the older; two runs/accounts requested concurrently receive their own links; `local_bridge.dispatch` allowlists the route (POST only) and rejects other methods; `http_relay` still blocks the route for browser traffic; the unchanged non-test local path still logs and returns. Extend `tests/test_test_management_*` only by adding new files or clearly separate tests; `local_proxy.py`, `tests/test_test_management_consumers.py` and `tests/test_test_management_relay_snapshot.py` are another session's uncommitted work (DCM-PERF-5): do not edit them.

### Innoservice part (not in the first dispatch)

After the dcm release: `frontend/tests/test-management-client.js` gains `requestPasswordReset`, `claimRecoveryLink` and `completePasswordReset` helpers (link kept in memory, never printed) and a browser spec drives the real reset UI. Dispatch it separately once the installed pin is updated and the Innoservice branch state is clarified.

Absolute target: `C:/Users/Micha Bigler/Documents/webapps/django-core-micha` and `C:/Users/Micha Bigler/Documents/webapps/innoservice`. Context anchors: dcm `docs/design/test-management.md`, `src/django_core_micha/invitations/{emails,mixins}.py`, `src/django_core_micha/test_management/`, and the Innoservice test-management client, isolated settings and browser specs.

### Implementer preamble

Start at the workspace root, run the hook canary and use the absolute targets below. This WO is the complete specification. Preserve concurrent changes. Edit only assigned source and tests. Run newly written tests once (tests-only author runs none). Leave dependencies, git, register, review and authoritative checks to the Orchestrator. Handle credentials inside the test client. Emit PLAN, periodic PROGRESS and one final RESULT.

## C. Orchestrator only

STOP here if you are the implementer. The Orchestrator owns the mapping, release/pin compatibility check, independent Tier 3 security and general reviews, affected auth and permission tests, and the local browser run. Record installed versions, commands, redacted outcomes and cleanup in Completion. Recheck any concurrently changed integration seams before dispatch.

## Completion

dcm part landed as 3.34.0 (private capture at the local reset-mail boundary, one-time claim route `recovery-message/`, discard on claim/reset/close/delete/recovery/restart, bridge allowlist, docs, changelog). Innoservice part (client helpers, real-UI spec with the installed 3.34.0, throttle/unknown-address/cross-run checks, log and evidence hygiene) is NOT done and keeps this WO open.

Evidence: affected set 158 passed, 2 skipped on the tree before the final test addition; `test_test_management_mail_capture.py` 17 passed, 2 skipped (the two PostgreSQL row-lock concurrency tests run only in the isolated instance); makemigrations check clean. Real-flow test claims a really rendered and generated link and sets the password through `PasswordResetConfirmView`: new password works, old fails, a second account is unchanged, the consumed link is rejected.

Process notes: the first Codex implementer (gpt-6-luna, effort max) ran away (3.5 MB output, no RESULT) and was stopped; its diff was read in full and kept. A second, unidentified actor ran an implementer (gpt-6.1-terra) and review rounds in the same tree concurrently; its scratch review directories were set aside as invalid and the review re-run by the Orchestrator. Review: round 1 and closing round 2 on envelope, regression, duplication, tests, sec_review; 4 raised, 4 accepted (link taken from the rendered body, INFO log assertion, real confirmation flow test fixed; Innoservice client and acceptance carried), worst accepted: defect. No blocker.

## Gate record

The operator requested preparation and commit of this work order on 2026-11-19.

Gate #1 approved by the operator 2026-11-19 ("Bitte in DCM-DX-11 ausführen"; confirmed for the unchanged Tier 3 Envelope in chat, with DCM-DX-9 to be finished first and the Innoservice part to follow the dcm part).
