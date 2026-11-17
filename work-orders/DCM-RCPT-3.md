# DCM-RCPT-3 — Apply managed model policy to the shared receipt reader

## A. Envelope

Tier 3. Direct receipt workload slice of approved BCO-UI-10. Bigler Consult manual upload and Gmail intake both call the shared `read_receipts`. In an enrolled deployment the shared reader must resolve the eligible central standard immediately before provider dispatch, with the actual source credential. If no eligible standard exists, return `provider_unavailable` without calling a provider. Treat the configured receipt provider as the only already-approved recipient. An allowlist for another driver does not authorize sending a receipt there. No fallback on quota or provider errors.

Preserve legacy provider/model settings on unenrolled apps and preserve the fake reader, current consent recheck, extension validation, content processing and cost guard. No new dependencies, schema, deployment or consent wording in this slice. Test the actual reader-to-extraction boundary using synthetic bytes and a fake provider. Verify eligibility failure and consent refusal before dispatch, and the existing legacy caller behavior.

## B. Implementation map

Target: `C:/Users/Micha Bigler/Documents/webapps/django-core-micha`. Baseline `9e24fbc`, DCM 3.30.0. Edit `src/django_core_micha/receipts/reader.py` (`read_receipts`, live extraction boundary), and add `src/django_core_micha/receipts/tests/test_managed_receipts.py`. Reuse `django_core_micha.ai_policy.resolver.resolve_workload` with trusted `DCM_RECEIPTS_AI_POLICY_CATALOGUE` rows (`driver`, `model`, `source_id`, `efforts`). Restrict rows to the configured receipt provider's corresponding `openai_api` or `anthropic_api` driver. Use source/model/effort at dispatch, never credential details in logs. Tests use the same receipt fixtures and manage the source selection using the DCM policy test interfaces. The orchestrator owns public contract documentation and release metadata.

### Implementer preamble

> First tool action from workspace root: run `codex-hook-canary-ws-gov-60`. Expect `WS-GOV-60 canary: workspace hooks are active`. If absent, report unverified coverage once, then follow all existing workspace gates.
>
> Parts A and B are the complete scope. Use the absolute target path. Read only the nearby receipt code and backend engineering conventions. Edit only the named sources and affected tests, and run only your new or changed tests once. No dependency or lockfile changes, git staging or commits, register edits, dispatch, secret access, live calls, deployment or self-review. Start with `PLAN: <steps>`, emit `PROGRESS: [n/total] <action>`, and finish once with `RESULT: DONE` or `RESULT: BLOCKED <reason>`.

## C. Orchestrator only

STOP — implementers do not act on this part.

Run scoped receipt/policy checks, independent Tier 3 review, and release before enabling the Bigler Consult managed receipt workload. The parent WO owns consumer pins and staging delivery.

## Gate record

Gate #1 approved by the operator on 2026-11-17 for BCO-UI-10, including shared core routing of direct workloads. This maps only the already-approved receipt path.

### Verification

- The operator explicitly approved the independent DCM-RCPT-3 OpenAI review on 2026-11-17. Two read-only rounds covered the Envelope with Codex `gpt-6.1-terra` and regression, duplication, tests and security with Codex `gpt-6-luna`.
- The final round found no product or security issue. A test now changes to a second source/model standard and verifies the credential at dispatch. Two observations about legacy and consent tests were retained as intentional regression coverage.
- Candidate release: DCM `3.31.0`. The parent BCO-UI-10 tracks the published version, consumer pins, enrolment and staging acceptance.
