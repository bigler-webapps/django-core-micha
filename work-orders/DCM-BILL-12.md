# DCM-BILL-12 — Opt-in token debit precedence

## Part A — Envelope

**Target:** django-core-micha, `main`. **Tier:** 3 — shared billing core.

### Goal and scope

Provide an explicitly opt-in shared ledger capability enabling Maana to spend its existing trial grants before time-limited code grants. The existing expiration-first order remains the default for all existing consumers. Within the selected precedence, preserve the existing expiry, creation-time and identity ordering. Credits belonging to other accounts and team pools retain their existing isolation and behavior. Preserve expiry, locking, insufficient-balance atomicity and debit retry semantics. Document the public contract and adoption requirements.

This order changes the shared ledger only. It does not implement Maana benefit codes, alter signup, change subscriptions, or introduce a duplicate ledger. It must not silently change existing consumers' debit order.

### Required tests to write

Cover opt-in trial-first ordering with trial grants and expiring code grants, partial and multi-credit debits, expiry and insufficient balance, retries and reference conflicts, and isolation of accounts and team pools. Demonstrate that the default remains expiration-first, including other credit kinds. Test invalid configuration or input, and concurrent debits where supported by the existing test infrastructure.

### Risks

Ledger ordering affects shared consumers, expiration and financial accounting. An opt-in must be consistently applied to every Maana debit path and must not broaden the scope of a debit or alter retry behavior.

## Part B — Implementation map

Target: `C:/Users/Micha Bigler/Documents/webapps/django-core-micha`. Work from these seams: `src/django_core_micha/billing/services.py`, `debit_tokens`, which locks the token account and credits, checks reference idempotency, then orders by `expires_at` ascending with nulls last, `created_at`, `pk`. `TokenEntry` in `billing/models.py` carries `kind`, `purpose`, `remaining` and `expires_at`. Existing billing tests are in `src/django_core_micha/billing/tests/`; update the relevant ledger tests and `docs/design/billing.md` for the adopted public contract. Maana calls this through `maana/backend/maana_billing/services.py`; no Maana files are in this order. Do not change schema, dependencies, other behavior, or registrations. Work from these named files; report a necessary scope change instead of expanding it.

### Implementer preamble

Your first tool action is the hook canary: run `codex-hook-canary-ws-gov-60` exactly. The expected denial is `WS-GOV-60 canary: workspace hooks are active`; continue. If absent, report unverified coverage and continue.

This file is the COMPLETE specification. Read nearest AGENTS.md, relevant engineering skill and MEMORY.md only for conventions. Your tools are for editing source and test files and running only the tests you wrote, once. Do not install dependencies, touch lockfiles, run package managers, tidy files, edit MEMORY.md or the work-order register, launch further agents, self-review, or run other tests. Do not git add, commit or push. Leave changes uncommitted for independent review. If blocked, report it. Narrate `PLAN: ...`, then `PROGRESS: [n/total] ...` before actions and on completion, with no gap over two minutes; finish with exactly `RESULT: DONE` or `RESULT: BLOCKED <reason>`.

## Part C — Orchestrator only

STOP: implementer instructions end here. Do not perform review, register updates, commit or push.

Operator approved drafting and implementation of the separately scoped shared-ledger dependency on 2026-11-16 in the MAANA-APP-23 orchestration chat. Execute configured implementation, independent Tier-3 reviews and affected tests before landing. Maana integration and dependency pin are separate work under MAANA-APP-23; preserve its deployment gates.

## Gate record

Gate #1 approved by the operator 2026-11-16: "Ich gebe die Ausarbeitung und Umsetzung des separaten Shared-Core-Work-Orders frei. Bitte fahre danach mit MAANA-APP-23 fort."
