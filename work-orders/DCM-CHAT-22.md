# DCM-CHAT-22 — Refresh execution datasets and report bounded output

## Part A — Envelope

Repository: `django-core-micha`. Tier 3.

### Goal and outcome

Allow a provider-hosted analysis to use data authorized and fetched through application tools earlier in the same assistant answer, and return oversized supported execution output in bounded form with an explicit truncation indication. This closes the shared-core prerequisites identified during HRAM-AI-6 review.

### Scope and constraints

- Refresh the execution inputs after relevant app-tool calls during the current turn, so subsequent provider execution in that turn can access the newly fetched data as well as authorized inputs from earlier answers in the same private chat. The app's trusted server-side policy remains the sole authority for datasets. Recheck current chat access, user and dataset authorization before any provider transfer. Never accept input bytes, provider file identifiers or authorization from the model or client. If a safe refresh cannot be performed, fail closed and report the limitation; do not silently analyze incomplete data.
- Keep each analysis isolated from other users, chats and analyses. Do not introduce local execution, additional network tools, direct provider access to app resources, implicit provider fallback or exposure of provider identifiers. Preserve cancellation, cleanup, retention approvals, execution limits, cost reservation and settlement, and ordinary turns without execution.
- For supported CSV, JSON and text outputs, return a valid bounded representation when limits are exceeded, with machine-readable truncation metadata that a consumer can display. Enforce count, per-file and aggregate resource limits before unbounded allocation; reject invalid or unsafe content. Never publish a misleading partial JSON document or broken CSV record as valid structured data. Preserve useful valid output where possible and explicitly report what was omitted.
- Support both existing OpenAI and Anthropic provider execution adapters when their documented protocols permit the secure refresh. Do not assert provider behavior that has not been verified. Record unsupported combinations explicitly, without enabling another provider or local execution.
- Preserve compatible existing consumer contracts or document any additive extension. No consumer app, dependency, CI, infrastructure, catalog model list or billing-policy changes.

### Risks

Data transfer across provider continuations can expose stale or unauthorized information if permissions change. Partial structured output can mislead the user; resource handling must remain bounded. Repeated uploads and continuations can create additional provider charges and temporary resources.

### Required tests to write

1. Using synthetic data and mocked provider transports, exercise a same-turn app query followed by analysis and prove that newly authorized datasets arrive, while data from other chats/users and revoked or unavailable data never does. Cover both supported providers, repeated queries, denied refresh and cancellation.
2. Prove limits and truncation metadata for oversized text, valid CSV and JSON, too many files and aggregate limits; cover malformed or unsafe input, multibyte boundaries and valid structured representations.
3. Verify current execution capability, private scope, retention approval, access revocation, temporary-file cleanup, usage settlement and ordinary non-execution turns remain intact. Do not contact real providers or use secrets.

## B. Implementation map

Target: `C:/Users/Micha Bigler/Documents/webapps/django-core-micha`. The approved consumer is HRAM-AI-6; no HRAM changes are in this order. Read `docs/design/assistant-chat.md`, `work-orders/DCM-CHAT-19.md` and `work-orders/DCM-CHAT-21.md` for the existing contract. `src/django_core_micha/assistant/turns.py` calls `prepare_execution` before the model stream. `assistant/execution.py` owns trusted input validation, bounded outputs and provider execution configuration. `assistant/drivers/openai_api.py` and `anthropic_api.py` own provider tool-call loops, file transfer, continuation and cleanup. `assistant/tests/test_provider_execution.py` and the existing mocked driver and access/accounting tests contain test seams. Investigate the actual provider and installed SDK contracts before changing continuation behavior; return a concrete blocker if secure refresh cannot be established. Preserve server-side capability attestations, source identity privacy and existing billing hooks.

### Implementer preamble

First tool action: run `codex-hook-canary-ws-gov-60` exactly from the workspace root. The expected denial is `WS-GOV-60 canary: workspace hooks are active`; continue. If absent, report unverified coverage once. Parts A and B are the complete specification. Read nearest AGENTS.md, backend engineering skill and app MEMORY.md for conventions. Preserve other work and stay in scope. Edit only scoped source and test files; do not edit this WO, register, governance, MEMORY, dependencies, lockfiles or documentation. Do not install, run package managers, start services, use credentials, contact live providers, deploy, dispatch, self-review or run git. Run only tests you wrote, once. Leave changes uncommitted. Print `PLAN: ...`, report `PROGRESS: [n/total] ...`, and finish exactly once with `RESULT: DONE` or `RESULT: BLOCKED <reason>`.

## C. Orchestrator only

STOP — implementers do not perform review, documentation, release or consumer rollout.

Gate #1: on 2026-11-17 the operator approved this Tier-3 shared-core scope in response to the AI-6 review findings. The user authorized both same-turn data refresh and bounded output with a visible truncation indication. Reuse the existing provider execution contract; route independent and security reviews on the resulting diff, and release before updating HRAM's pin. Do not publish a major version without a separate version-specific approval. Preserve the uncommitted HRAM-AI-6 work and coordinate overlap with BCO-UI-10.
