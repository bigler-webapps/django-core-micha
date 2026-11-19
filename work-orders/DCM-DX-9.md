# DCM-DX-9 — ASGI server compatibility for isolated test consumers

## Part A — Envelope

**Tier:** 3 (shared-core isolated infrastructure). **Outcome:** A verified isolated consumer starts with the ASGI server it already supplies, including consumers with uvicorn but without daphne. Preserve the existing daphne behaviour when it is available. If neither executable is present, fail closed. The server is started only after the existing isolation proof succeeds; no isolation, controller, authentication, database or network checks are weakened. This fixes the shared starter dependency revealed while completing MAANA-APP-44.

**Scope:** Isolated test-management entrypoint and focused tests; publish a backwards-compatible patch and update the approved Maana consumer pin as needed. Do not introduce a new runtime dependency or modify ordinary application startup.

**Tests:** Prove daphne preference, uvicorn fallback with equivalent address, failure when neither is present and that failed isolation cannot start either server. Run affected shared isolation and starter tests and the real isolated Maana integration journey.

## Part B — Implementation map

Target `C:/Users/Micha Bigler/Documents/webapps/django-core-micha`. See `src/django_core_micha/test_management/isolated_entrypoint.py`, `tests/test_test_management_isolation.py`, shared launcher tests, `pyproject.toml` and `work-orders/DCM-DX-8.md`. The existing launcher reports an upstream proof failure when the backend exits immediately after a successful proof because daphne is absent. Maana already supplies uvicorn. Preserve all gates and use only fixed server arguments.

Implementer preamble: This work order is the complete scope. Do not stage, commit, push or review your own changes; report tests and blockers to the orchestrator.

## Completion

Landed as `7922449` (published as 3.33.1). Real Maana run on 2026-11-19 with the installed 3.33.1 pin: the isolated backend now starts under uvicorn after the isolation proof, becomes ready, accepts controller account creation and normal login, and run close stops the instance (`Application shutdown complete`). A first attempt failed only because Maana's untracked isolated settings set `EMAIL_BACKEND` next to `MAILERS` (Django 6.1); that line was removed. The journey still stops at the operator benefit-code creation (403); this is the Maana adapter's permission mapping and stays with MAANA-APP-44.

## Part C — Orchestrator only

STOP — this section is not an instruction to the implementer.

Approved by the operator's 2026-11-18 request to fix the failure and complete MAANA-APP-44, together with prior approval to generalize DCM for new consumers and use necessary Maana/core updates. The operator separately allowed this orchestrator to implement the shared-core first implementation. Existing uncommitted DCM-CHAT-22 work is foreign and must be preserved. An independent Tier 3 review remains mandatory before landing.

The existing 3.33.0 entrypoint installs a valid isolation proof but then exits because Maana supplies uvicorn and lacks daphne; the launcher subsequently reports the exited container as a database preflight failure. The corrected source passed 105 test-management isolation, launcher, consumer, security, bridge and synthetic tests. OpenAI-only independent review: envelope and security codex/gpt-6.1-terra; regression, duplication and tests codex/gpt-6-luna. Round one raised two defects (missing-server delay and main-path test coverage); both were fixed. The closing round reported no findings. The real Maana integration depends on publishing this patch first and remains a completion gate after publication.
