# DCM-RMB-1 — Shared reimbursement rules (pure data layer)

Pointer: canonical Envelope is `bigler-consult/work-orders/BCO-EXP-3.md` (Part A, sections 1, 5, 6; Gate #1
approved by the operator 2026-11-07). This order is slice 1 of the shared-core work: only the pure,
Django-free rules that both `reimbursements` and `bigler-consult` must use. Tier 3 (shared-core).
Slice 2 (DCM-RMB-2) adds the xlsx/pdf renderers and the secure excerpt helper; it is NOT part of this order.

## Part A — Envelope

Goal: a new package `django_core_micha.reimbursements` holding the money/FX quantization, deterministic
amount distribution, the template account catalog, cash-withdrawal allocation rules and the non-draft
required-field rule, extracted from `reimbursements/backend/claims/models.py` with IDENTICAL behavior.
Non-goals: no Django models, no ORM, no storage, no authorization, no export rendering, no PDF work, no new
dependency, no change to any existing dcm module, no version bump, no release. Host apps keep their ORM,
users, projects, cost centers and claimant profiles.

Risk: the reimbursements app will later adopt this; any behavioral drift (rounding, field names, the exact
account strings) changes its exports. Characterization tests pin the existing behavior.

## Part B — Implementation map (addressed to the implementer)

Working directory: `C:/Users/Micha Bigler/Documents/webapps/django-core-micha`. Source of truth to read
(read-only, do not edit): `C:/Users/Micha Bigler/Documents/webapps/reimbursements/backend/claims/models.py`
(`quantize_money`, `quantize_fx_rate`, constants, `Reimbursement.Account`, `Reimbursement.clean`,
`missing_required_fields_for_non_draft`, `CashWithdrawal`, `WithdrawalAllocation.clean/save`).
Foreign uncommitted edits exist in `src/django_core_micha/scripts/sync_secrets.py` and
`tests/test_sync_secrets.py`: do not touch them.

Create `src/django_core_micha/reimbursements/` (plain package, no `apps.py`, no app label, no Django import):

- `errors.py`: `class AllocationError(Exception)` with attribute `code` (str) and `message`.
- `money.py`:
  - constants `MONEY_DECIMAL_PLACES=2`, `MONEY_MAX_DIGITS=12`, `FX_RATE_DECIMAL_PLACES=8`,
    `FX_RATE_MAX_DIGITS=18`, `MONEY_QUANTIZER=Decimal("0.01")`, `FX_RATE_QUANTIZER=Decimal("0.00000001")`.
  - `quantize_money(value)` and `quantize_fx_rate(value)`: exactly as in claims/models.py (None passes
    through, `Decimal(value)`, `ROUND_HALF_UP`).
  - `convert_amount(original_amount, fx_rate)` = `quantize_money(Decimal(original_amount) * Decimal(fx_rate))`.
  - `distribute_amount(total, weights, tie_break_keys=None)`: largest-remainder distribution of
    `quantize_money(total)` over positive `weights` (percentages or amounts, any positive Decimals): quotas
    `total*w/sum(w)`, floor each to the cent, hand the remaining cents one by one to the largest fractional
    remainders, ties broken by `tie_break_keys[i]` (default: index) ascending; result sums to the total
    EXACTLY; raises `ValueError` for empty input, non-positive weight or non-finite values.
  - `split_conserving(original_total, converted_total, original_shares)`: `original_shares` are positive
    amounts summing exactly to `quantize_money(original_total)` (else `ValueError`); returns a list of
    `(original_share, converted_share)` where the converted shares come from
    `distribute_amount(converted_total, original_shares)`, so BOTH columns sum exactly to their totals.
- `accounts.py`: `TEMPLATE_ACCOUNTS`, a tuple of exactly 17 `(value, label)` pairs, in this order and with
  these exact strings (value == label, copied from `Reimbursement.Account` in claims/models.py lines 77-110):
  `422000 Travel costs`, `422010 Accommodation and boarding costs`,
  `423000 Medical examinations, vaccinations etc.`, `423020 Other personnel costs`,
  `421000 Continuous education`, `500000 Procurement of material`, `500040 Books`,
  `510010 Procurement equipment < CHF 5'000`, `550000 Office material, printed matters`,
  `550010 Photocopies`, `550020 Telephone, Fax, Internet`, `550030 Postage`,
  `550060 Expenses for meetings, guests, seminaries (enclose name list)`, `550070 Donations`,
  `550080 Authorizations, Fees`,
  `568000 Field expenses (Remuneration field workers, consumables bought + used locally)`, `Other expenses`.
  Also `TEMPLATE_ACCOUNT_VALUES` (frozenset) and `is_template_account(value)`. Copy the strings character
  for character from the source file rather than retyping them.
- `cash.py` (pure functions on Decimals and plain values):
  - `remaining_balance(withdrawal_amount, allocated_amounts)` = quantized withdrawal minus the quantized
    sum of the allocated amounts (may be negative; callers decide).
  - `check_allocation(withdrawal_amount, already_allocated, new_original_amount)`: raises
    `AllocationError("non_positive")` when the new amount is not > 0 and `AllocationError("over_allocated")`
    when it exceeds the remaining balance (an allocation equal to the remaining balance is allowed).
  - `fx_mismatch(withdrawal_fx, reimbursement_fx, manual_override)` -> True only when not overriding and
    the two rates differ after `quantize_fx_rate` (mirrors `WithdrawalAllocation.clean`).
  - `allocation_fx(withdrawal_fx, reimbursement_fx, manual_override)` -> the effective FX: the withdrawal's
    quantized rate unless `manual_override`, then the reimbursement's quantized rate (mirrors
    `WithdrawalAllocation.save`).
  - `allocation_converted(original_amount, effective_fx)` -> `convert_amount(...)`.
- `validation.py`:
  - `NON_DRAFT_REQUIRED_FIELDS` = tuple of the keys used in `missing_required_fields_for_non_draft`
    (`cost_center`, `claimant_profile`, `expense_date`, `merchant_name`, `account`, `original_amount`,
    `original_currency`, `fx_rate`, `converted_amount`, `converted_currency`).
  - `missing_required_fields(values)`: `values` is a Mapping; a field is missing when absent, None or a
    blank/whitespace string; returns the missing names in `NON_DRAFT_REQUIRED_FIELDS` order.
  - `fx_override_reason_missing(payment_method, workflow_status, manual_override, reason)` -> True when the
    status is not `"draft"`, the method is `"cash"`, `manual_override` is true and `reason` is blank
    (mirrors `Reimbursement.clean`).
- `__init__.py`: re-export the public names above; module docstring states the package is pure data and
  that host apps own ORM, storage and authorization.
- Docs: `docs/design/reimbursements.md` (short): purpose, the public API names above, the rule that hosts
  keep their models, and "behavior is characterized against reimbursements claims/models.py".

Tests: `tests/test_reimbursements_core.py` (plain pytest, no DB): HALF_UP cases (`2.675 -> 2.68`,
`2.665 -> 2.67`, FX to 8 places), `convert_amount` examples, `distribute_amount` for 100 over weights
`[33.33, 33.33, 33.34]` and a case with exact cent ties (tie-break keys decide, same input twice gives the
same output, sum exact), `split_conserving` with a USD-like split where rounding would otherwise lose a cent,
the 17 account strings exactly (compare against a literal list in the test) and `is_template_account`,
all `cash` rules (balance, equality allowed, over-allocation, non-positive, FX mismatch with and without
override, effective FX), `missing_required_fields` for None/blank/whitespace/complete and order, and
`fx_override_reason_missing` for draft, non-cash, override with and without reason. Assertions use concrete
expected values.

Invariants: no import of `django`, no float arithmetic (Decimal only), do not change any existing file
except adding the new package, the test file and the design doc. Do not bump the version.

### Preamble

> The text above is the COMPLETE spec — the committed WO file's content, not a plan to refine; there
> is no separate plan file. Read the nearest `AGENTS.md` and `MEMORY.md` ONLY for conventions. Stay in
> scope; do not touch auth/permissions/deps/schema/CI unless the spec says so. **Do NOT edit
> `WORK_ORDERS.md`.** **Your tools are for editing source and test files and for running the tests you
> wrote — nothing else.** Do NOT install dependencies, touch a lockfile, run a package manager, run any git
> command, or tidy up stray files; if something blocks you, stop and report `RESULT: BLOCKED <reason>`.
> Leave every change uncommitted. WRITE the tests and RUN only the tests you wrote once; that is not the gate.
> Use explicit UTF-8 for file I/O. Narrate with `PLAN: ...` then `PROGRESS: [n/total] ...` lines (no silent
> gap over ~2 min) and exactly one final `RESULT: DONE|BLOCKED <reason>`.

## Part C — Orchestrator only

STOP — if you are the implementer, everything below is not for you.

- Gate #1: operator approval in chat 2026-11-07 for BCO-EXP-3 including shared-core slices.
- Execution: codex/gpt-6-luna, `codex exec` with this file on stdin, cwd = django-core-micha. Review: the
  four lenses plus security-light (pure code, no I/O) on the assembled diff; commit only own paths; dcm
  `main`. No version bump or release in this order (release is a separate operator-gated step after DCM-RMB-2).
