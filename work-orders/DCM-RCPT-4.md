# DCM-RCPT-4 — Receipt reader follows the policy route across disclosed providers

## A. Envelope

Requested by the bigler-consult Expertenchat for [BCO-LEGAL-6](../../bigler-consult/work-orders/BCO-LEGAL-6.md) (operator decision 2026-11-27: "Policy wählt Anbieter"). Consumers of the shared receipt reader: reimbursements, spesix, bigler-consult.

### Goal and outcome

Today an enrolled deployment resolves the receipt workload only among rows of the one provider named by `DCM_RECEIPTS_PROVIDER` (DCM-RCPT-3), so the owner's policy cannot move receipts to another provider even when the app's disclosure covers it. When done:
- an app can declare the set of providers its receipt disclosure covers (a new list setting); without it, the set is the single `DCM_RECEIPTS_PROVIDER`, so every existing consumer behaves exactly as today;
- in an enrolled deployment the reader resolves the extraction workload among the catalogue rows of every declared provider, and the policy route (source, family, effort) decides which provider receives the document;
- a resolved route whose provider is not in the declared set, or no eligible route, is refused with `provider_unavailable` before any provider receives the document; no fallback to another provider on quota or provider errors;
- unenrolled deployments, the fake reader, consent checks (including the recheck before dispatch), extension validation, limits and the cost guard are unchanged;
- `docs/design/receipts.md` and `docs/design/assistant-chat.md` describe the new setting and its default.

Release: minor version (additive setting, unchanged default). Bigler Consult bumps its pin in its own work order after the release.

### Scope and exclusions

In scope: `receipts/reader.py`, its tests, the two design docs, release metadata.

Excluded: changes to `ai_policy` resolution or catalogue semantics; statement extraction or any other workload; consent or disclosure wording; consumer pins and consumer settings (each consumer's own work order); legacy unenrolled provider and model settings beyond keeping them working.

### Risks

- A widened eligibility set sends private receipts to a provider an app has not disclosed. Control: the declared set defaults to today's single provider, and the provider of the resolved route is checked against it before dispatch.
- Three consumers share the reader; a changed default would silently reroute their receipts.

### Existing capabilities

`ai_policy.resolver.resolve_workload` (eligibility callback, workload `extraction`), `DRIVER_PROVIDERS`, the DCM-RCPT-3 managed path in `read_receipts`, and the test fixtures in `receipts/tests/test_managed_receipts.py`.

**Tier 3** (change inside shared core; routing of private documents to external AI providers).

### Required tests to write

- With two declared providers and a policy selecting the second, the reader dispatches to that provider's source, model and credential.
- With the declared set left unset, only `DCM_RECEIPTS_PROVIDER` rows are eligible (today's behaviour, both for `openai` and `anthropic`).
- A route resolving to an undeclared provider, an unknown provider name in the setting, and no eligible route each refuse with `provider_unavailable` before any provider call.
- Consent refusal still precedes dispatch with two declared providers.
- Regression: existing receipt tests, `test_managed_receipts.py` and the unenrolled legacy test stay green.

## B. Implementation map

- Reader: `src/django_core_micha/receipts/reader.py:20` (`_managed_workload`, driver filter from the provider setting), `:71-79` (provider, effort and enrolled branch in `read_receipts`).
- Resolver, read only: `src/django_core_micha/ai_policy/resolver.py:206` (`resolve_workload`, `eligible` callback), `DRIVER_PROVIDERS`.
- Tests: `src/django_core_micha/receipts/tests/test_managed_receipts.py` (fixtures `managed`, settings `DCM_RECEIPTS_AI_POLICY_CATALOGUE`, `DCM_RECEIPTS_PROVIDER`).
- Docs: `docs/design/receipts.md:16` (settings list), `docs/design/assistant-chat.md:11` (provider restriction sentence).
- Consumer view for context only: `bigler-consult/backend/expenses/receipt_route_guard.py` wraps the reader with an app-side disclosure check; not edited here.
- Environment: tests through `tests.py --repo django-core-micha` with the receipts selection.

## Preamble

> You are the dispatched implementer. Run `codex-hook-canary-ws-gov-60` as the first tool action.
> Continue on the expected denial. Otherwise report unverified coverage once and continue under the existing gates.
> Read this Envelope, DCM-RCPT-3, the nearest governance and the relevant role skill.
> Edit only the files named in Part B and run only the new tests you wrote. Preserve parallel work.
> Do not access hosts or credentials, dispatch jobs or agents, run reviews, edit the register or write to git.
> Report progress and end with `RESULT: DONE` or `RESULT: BLOCKED` with the test evidence.

## C. Orchestrator only

> Implementer: stop here. The Orchestrator owns review, evidence and landing.

- Tier 3 shared core: independent review including security; scoped receipts and ai_policy tests; minor release per the dcm publish flow.
- After release, report the published version to the bigler-consult Expertenchat for the pin bump.
- Report back to: `local_9ef3b5e5-763d-4d48-a151-31b8553002b2`.

## Gate record

Gate #1 approved by the operator 2026-11-27 (click "Übergeben: Session starten" in session local_9ef3b5e5-763d-4d48-a151-31b8553002b2), following the decision "Policy wählt Anbieter".
