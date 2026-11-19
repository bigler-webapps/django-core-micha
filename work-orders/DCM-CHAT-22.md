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

### Checkpoint, 2026-11-17

The source and tests are uncommitted; the order remains in progress. The configured implementation ran on `codex/gpt-6-luna` at medium/max after Haiku was skipped because its writing hooks were not verified. The first run stopped without source edits; the retry produced the current diff. The provider/access/accounting tests passed (55); the full assistant suite passed (230, one skipped, with an existing Windows socket-thread warning). No real provider was contacted.

The runtime's automatic review initially rejected the launch because it did not find explicit approval to send this private source diff and work order to the OpenAI-hosted reviewers. On 2026-11-17 the operator explicitly approved that transfer; independent review round 1 was then started. Do not release or backfill the review. HRAM must still adapt its provenance snapshot to same-turn refresh: its existing per-category aggregate changes content when a query is added, while the DCM candidate fails closed when a previously uploaded snapshot changes. HRAM also needs the new DCM truncation metadata and selected-model availability checked against the active chat. The candidate's provider, security and accounting behavior remains subject to independent review.

## Completion

Landed with dcm 3.35.0. Review: round 1 and closing round 2 on codex/gpt-6.1-terra (envelope, sec_review: no security finding) and claude/haiku (regression, duplication, tests; the codex-launched Haiku lenses returned no result, so they were re-run once as read-only subagents on the same r2 diff). Fixed after the closing round (no third pass): the Anthropic repeat-stream regression (uploads were no longer re-attached while no container id exists; blocker), OpenAI truncation metadata now per execution record, plus tests for it, mid-row and quoted-newline CSV truncation and session-wide output accumulation. Assistant suite 253 passed in a clean venv with the CI extras.

Carried, not fixed: oversized JSON is reported as `data: null` with truncation metadata (documented in the changelog; consumers must show the metadata); output CSV parsing is now strict; truncated CSV tails beyond the bounded read cannot be validated; `bounded_text` iterates per character; duplicated parse/upload loops and extension tables across the drivers and `execution.py`; no test makes `authorize` fail during a same-turn refresh at turn level, settlement is stubbed in the refresh tests, no unsafe-name/media-type output tests, UTF-16 JSON output is now rejected; the SHARED_CAPABILITIES row for the AI chat core does not yet mention the bounded output contract; refresh_execution in turns.py has no try/except around the mid-turn prepare call. HRAM-AI-6 still has to adapt its provenance snapshot, the truncation metadata and selected-model availability before it bumps its pin.
