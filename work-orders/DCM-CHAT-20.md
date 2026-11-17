# DCM-CHAT-20 — Enforce centrally managed AI model policy

## A. Envelope

Tier 3. Shared-core slice of Bigler Consult BCO-UI-10 (`02ab0ef`), approved on 2026-11-17. DCM owns durable, revisioned consumption of app/environment model policy and enforcement at the provider boundary. The Bigler Consult central API, UCM and consumer rollout are tracked in the parent order.

- A source identifies a concrete deployment account or subscription and has a stable ID independent of its provider driver. An eligible explicit choice stays valid. A chat whose saved selection was revoked uses the allowed standard on its next request if eligible. A managed workload that cannot use the standard reports unavailable unless it has an eligible explicit choice. A forged or stale explicit request is rejected by the server. A provider or quota failure never selects a different source implicitly.
- Enrolled apps keep their last valid applied policy across restarts and network outages. A reachable enrolled app refreshes promptly enough for the combined central-save-to-UCM window of 30 seconds. It never rolls back or acknowledges unapplied revisions. An enrolled app with no valid policy returns unavailable for managed calls. A running provider request retains its original selection. Queued requests resolve eligibility and credentials at provider-call time.
- Carry source identity through the effective catalogue, chat selections and provider calls. Offer the same resolver to direct workloads alongside assistant chat and one-shot completion. Preserve app callbacks, local restrictions, readiness, budget and data-processing checks. Existing unconfigured apps keep their current paths. Operator policy does not authorize a capability, credential, prompt disclosure or billing fallback.
- No changes outside DCM. No new dependencies, CI, secret provisioning, live provider calls or deployment. An additive migration may persist source selections and policy state; retain existing rows and support pre-existing chats.

Required tests: app/environment identity mismatch, invalid and delayed revisions, atomic apply and acknowledgement, process restart and outage, missing policy, refresh timing with a fake transport, source identity for duplicate model names, local eligibility, default/explicit selection, revocation in existing chats, queued versus running requests, provider errors and forged selections. Exercise normal DCM chat and completion paths plus a direct-workload resolver fixture. Preserve existing access, spend-limit and provider capability tests.

## B. Implementation map

Working directory: `C:/Users/Micha Bigler/Documents/webapps/django-core-micha`. Baseline `331c59b` includes DCM-CHAT-19 and release 3.29.0. Read `docs/design/assistant-chat.md` and the relevant local backend conventions.

### Contract shared with BCO-UI-10

- The central base URL is trusted deployment configuration. The app-side transport uses the scoped server credential with `GET /api/ai-policy/enrollment/` and `POST /api/ai-policy/enrollment/applied/`. An enrolled app never sends a caller-controlled target identity. The central service derives the target from the credential, returning the explicit expected app/environment in the response. The scope is app plus environment. Keep reporting/provider credentials separate from the enrolment credential. Use fixed HTTPS endpoints, bounded response/time, safe diagnostic codes and no cross-host redirects. Transport tests use injectable responses.
- Policy JSON carries `app_id`, `environment`, positive integer `revision`, `allowed` mapping source IDs to nonempty lists of model IDs, and `standard` containing `source_id` and `model_id`. The pair must occur in `allowed`. Treat source IDs as opaque. Validate before durable application. Acknowledge with `{revision}` only after successful durable application; retry acknowledgements idempotently. An older or conflicting same-revision payload cannot overwrite an applied revision. Avoid hanging provider requests during central outages; keep refresh bounded and app configuration opt-in. Allow local-only state tests without any secrets.
- Catalogue identity must resolve model and source unambiguously, with the safe source label and applied revision available to UCM. Existing catalogue entry IDs remain compatible for unconfigured apps. Source credentials are looked up from trusted local configuration by source ID. A missing or ineligible local source never falls back to that provider's legacy credential.

### File and function seams

- Add focused opt-in policy modules under `src/django_core_micha/ai_policy/` for revision validation, app/environment storage, update/ack and a reusable direct-workload resolver. The opt-in module must be usable by apps that do not install the assistant. Reuse Django's transaction/locking and deploy configuration conventions. Keep the last valid state durable. `tests/settings.py` is the test registration seam. Add an initial migration only for this new policy app if needed.
- `src/django_core_micha/assistant/catalogue.py`: `entry_id`, `selection_of`, `entry_usable`, `default_entry`, `validate`, and startup validation. `assistant/views.py`: the catalogue response, chat creation and update, and the legacy local model administration path. `assistant/models.py`: persisted selection and source-safe session continuity; add only the additive migration needed against the landed graph. `assistant/credentials.py` and `assistant/availability.py`: coverage of several accounts, CLI versus API, and missing credentials. Existing driver IDs remain driver dispatch identifiers; they do not stand in for source IDs on managed apps.
- `assistant/turns.py`: `_create_turn` and `run_turn`, including session resume and continuation, authorization rechecks and hosted-analysis boundaries. `assistant/completion.py`: one-shot provider dispatch, including pay-per-use and CLI. Resolve before sending any content to a provider; keep the resolved source for the lifetime of an in-flight request. Preserve DCM-CHAT-19's hosted-container checks and existing credential/capability/data guards.
- Write scoped tests in `src/django_core_micha/assistant/tests/test_managed_policy.py` and `tests/test_ai_policy.py`. Existing assistant `test_limit_models.py`, `test_completion.py`, `test_access_recheck.py` and `test_provider_execution.py` are regression references. Do not test with actual provider calls or read any credential values. Report the resulting API shape for the Orchestrator to finish `docs/design/assistant-chat.md`.

### Implementer preamble

> First tool action: run `codex-hook-canary-ws-gov-60` from the workspace root. Expect the denial `WS-GOV-60 canary: workspace hooks are active`. If absent, report unverified coverage once and proceed under the workspace gates.
>
> Parts A and B are the complete specification for this slice. Use the absolute target directory, preserve all other work, read the relevant engineering conventions, and edit only scoped source and tests. Write and run only the tests you added or changed once. No dependency installs, package managers, lockfiles, git staging/commits/push, register or documentation edits, self-review, other agent dispatch, live provider calls, deployment or credential access. Report `RESULT: BLOCKED` only for a concrete unresolved contract or runtime blocker. The Orchestrator owns review, authoritative tests, versioning and publication.
>
> Begin with `PLAN: <steps>` and report `PROGRESS: [n/total] <action>` before relevant actions. Finish exactly once with `RESULT: DONE` or `RESULT: BLOCKED <reason>`.

## C. Orchestrator only

STOP — implementer, do not act on Part C.

Independently review the mapped Tier 3 slice including security and the checked inputs to provider routing. Verify affected assistant, extraction budget and policy tests. Release the additive DCM contract before consumer pins. The parent WO owns the end-to-end 30-second guarantee, the central and UCM behavior and staging rollout evidence.

The independent OpenAI reviews used codex/gpt-6.1-terra (envelope) and codex/gpt-6-luna (regression, duplication, tests, security). The review found legacy startup/identity regressions, local eligibility bypass in routing, incorrect standard selection, missing provider-boundary recheck, and several readiness and test gaps. Corrections retain last-valid policy on disconnection as required by the approved Envelope. The central and first live consumer still need to verify the end-to-end delivery window and PostgreSQL concurrency.

## Gate record

Gate #1 approved by the operator in BCO-UI-10 chat on 2026-11-17. This work order maps the DCM portion of the approved BCO-UI-10 Envelope. No independent scope extension.
