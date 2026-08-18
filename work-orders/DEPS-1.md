> **Self-address guard:** if you are the implementer reading this work order as your own
> specification, Part C is not addressed to you — it tells the Orchestrator how to invoke you; you
> ARE that invocation. Do NOT shell out to `codex exec`.

# DEPS-1 — Open the Django pin to 6.1 and configure the MAILERS default

Status: **planned** · Tier: 3 · Target repo: django-core-micha (main) · Datum: 2026-08-18 · Backend only

# A. Envelope — authored by the Expertenchat

## Goal / expected outcome

dcm's `pyproject.toml` currently caps `Django>=6.0.5,<6.1`. That cap is the sole reason every
consuming app's Renovate PR proposing `Django==6.1` fails to resolve (`uv pip install` reports
"No solution found" because dcm's own pin makes `django-core-micha` and `Django==6.1`
unsatisfiable together) — confirmed live in `kerzenziehen` PR #50 and `innoservice` PR #55.

Widen the pin to `Django>=6.0.5,<7.0` and fix the one real behavioural gap that Django 6.1
surfaces, so dcm becomes genuinely — not just nominally — usable under 6.1. Expected outcome: a
published dcm release that consuming apps' Renovate PRs can pin to and resolve cleanly, verified
by dcm's own full test suite passing unchanged under Django 6.1.

**This release does not turn those two PRs green by itself.** Both currently pin
`django-core-micha==2.41.2`, which still carries the `<6.1` cap — Renovate must first pull them up
to `2.43.0` (its next scan, or a manual rebase) before they can resolve. Expect them to stay red
until that happens; that is not a sign the fix failed.

## Background — what was already verified in this session

- Full local test run of dcm's suite (667 tests) under Django 6.1 (Python 3.13, `channels==4.3.2`
  / `channels_redis==4.3.0` — current PyPI latest, unmodified): **667 passed, 0 failures**,
  identical to the 6.0.8 baseline. No new test failures. `channels`/`channels_redis` have not yet
  published a release with an explicit Django 6.1 trove classifier, but `django/channels` upstream
  already merged 6.1 test support and a stable-version bump on `main` (PRs #2225, #2229,
  2026-07-11/08-06) — the missing classifier is a release-cadence lag, not a known incompatibility.
  `django-allauth` (65.19.1) and `djangorestframework` (3.18.0) already declare Django 6.1 support
  outright.
- The same run surfaced exactly one new signal: `RemovedInDjango70Warning: Django 7.0 will not
  have a default mailer. Configure settings.MAILERS to avoid errors when sending email.` — raised
  from `src/django_core_micha/notifications/delivery.py:148` (`message.send(fail_silently=False)`).
  Not a 6.1 failure, but it will become a hard error at Django 7.0 if left unaddressed, and this is
  the natural point to close it since it's already surfaced.
- Operator decision (this session): the estate sends mail via **resend only** now. The fix scope is
  **wiring `settings.MAILERS` with a default mailer, resolved from the same already-existing
  `EMAIL_PROVIDER`/backend resolution dcm already does** — not a rewrite of the provider
  abstraction.
- **No consuming app sets `EMAIL_BACKEND` or `MAILERS` itself.** Verified across all twelve app
  repos' `backend/**/*.py` (zero matches outside dcm). A `MAILERS["default"]` introduced in dcm
  therefore cannot silently override an app-level mail configuration — there is none to override.
  This is the main thing that would have made the settings change risky, and it does not apply.

## Scope

1. Widen the Django dependency constraint in `pyproject.toml`: `Django>=6.0.5,<6.1` →
   `Django>=6.0.5,<7.0`. Operator decision (this session): open through the next major, not a
   narrow `<6.2` window — accepts that a future untested 6.x minor could pass the pin without a
   fresh dcm verification pass; see Risks.
2. Configure `settings.MAILERS` with a `"default"` entry in dcm's settings (`settings_base.py` /
   wherever the email backend is currently resolved), pointing at the same backend dcm's existing
   `resolve_email_backend()` already resolves — do not introduce a second, parallel resolution
   path. Since the estate now sends only via resend, the practical effect is a `MAILERS["default"]`
   that resolves to the resend/Anymail backend, but the wiring must go through the existing
   resolver output, not a hardcoded resend literal, so local/console fallback (already used in
   tests and local dev) keeps working unchanged.
3. Confirm the `RemovedInDjango70Warning` no longer fires once `MAILERS` is configured (re-run the
   suite under Django 6.1 and check for the warning's absence).
4. Version bump: `2.42.1` → **`2.43.0`** (minor — this both extends supported-dependency surface
   and changes real settings behaviour, not a pure bugfix) + `CHANGELOG.md` entry describing both
   the pin widening and the `MAILERS` fix, referencing this WO's finding chain (kerzenziehen
   PR #50 / innoservice PR #55 as the symptom, the local 6.1 test-suite run as the verification
   evidence).
5. Publish to PyPI (existing `publish.yml`, triggers automatically on `main` push touching
   `pyproject.toml`/`src/**` — no separate action needed beyond the commit).

## Explicit non-goals / do not touch

- **Do not touch the `EMAIL_PROVIDER` multi-provider abstraction** (`_email_config.py`'s
  smtp/postmark/unknown-provider fallback paths, or `resolve_email_backend()`'s signature/logic).
  Operator decision (this session): this WO wires `MAILERS` to the existing resolver output only —
  it does not retire or simplify the smtp/postmark code paths, even though the estate currently
  uses resend exclusively. That simplification, if ever wanted, is a separate WO.
- **Do not bump or touch any consuming app's own `Django==` or `django-core-micha==` pin.** This WO
  only unblocks — kerzenziehen/innoservice/others pick up the new dcm version on their own
  Renovate/PR schedule, out of scope here. **This explicitly includes the six apps that carry their
  own `<6.1` cap** (`cockpit`, `hram`, `spesix`, `fitness-monitor`, `hpc-bridge`, `webshop-guenter`
  — see Risks): widening those is per-app work with its own per-app verification, not a sweep to
  fold in here. Track it as a follow-up rather than dropping it — the point of naming them is that
  they will otherwise be assumed handled.
- **Do not silently widen or touch dcm's other dependency pins** (`django-allauth`, `djangorestframework`,
  `django-cors-headers`, `channels`, `channels_redis`, `django-anymail`, `django-environ`,
  `whitenoise`, `cryptography`, etc.) beyond what's already unpinned today. If the Django-6.1 suite
  run (step 3 re-run, or the Orchestrator's own re-verification) surfaces a real failure traceable
  to one of them, stop and report — do not paper over it with a version bump of that dependency
  inside this WO.
- Do not address `RemovedInDjango70Warning` or any other deprecation warning outside the one named
  above — this WO closes the one that surfaced, not a general deprecation sweep.

## Risks

- **`channels`/`channels_redis` have no published release declaring Django 6.1 support.** The local
  suite run is real evidence (667/667 passed, WS/consumer tests included) but is not an upstream
  compatibility guarantee — a behavioural edge case outside dcm's own test coverage could still
  exist. Accepted risk per the verification already done; no additional upstream-tracking action
  is in scope here.
- **`<7.0` is a wide-open upper bound.** Every future Django 6.x minor (6.2, 6.3, ...) will now
  install without triggering a fresh dcm compatibility check — a future minor could break something
  dcm's suite doesn't currently exercise, and nothing will flag it until symptoms show up
  downstream. This was an explicit operator trade-off against `<6.2`'s narrower/safer window.

  **Consuming apps do not inherit that openness, and the reason matters both ways.** Every one of
  the twelve app repos is independently bounded on Django today (verified against remote `develop`,
  not local checkouts): six pin exactly (`jg-ferien`, `survey_app`, `survey_contact_app`,
  `reimbursements` at `==6.0.7`; `kerzenziehen` `==6.0.5`; `innoservice` `==6.0.6`) and six carry
  their own cap (`cockpit`, `spesix`, `hpc-bridge`, `webshop-guenter` at `>=6.0.5,<6.1`;
  `fitness-monitor` `>=6.0.6,<6.1`; `hram` `>=6.0.7,<6.1`). So **no app can silently receive Django
  6.1 as a side effect of a dcm bump** — that failure mode was considered and does not exist here.

  The consequence runs the other direction instead: for those six capped apps, **widening dcm moves
  nothing.** Their own `<6.1` — today a redundant mirror of dcm's cap — becomes the sole binding
  constraint the moment dcm's is gone, and it is the kind of constraint nobody thinks to look at
  because it used to be harmless duplication. Widening them is per-app work, deliberately **not** in
  this WO's scope (see non-goals), but it should be tracked, or those six quietly sit on 6.0.x
  indefinitely while everyone assumes DEPS-1 handled it.

- **`reimbursements` is the one app that takes this release with no PR and no review.** Its dcm pin
  is `django-core-micha>=2.42.1` — unbounded — so its next image build resolves to 2.43.0 and picks
  up the `MAILERS` settings change without a Renovate PR, a pin bump, or anything to review. Its
  Django stays at `==6.0.7`, so no Django movement; the exposure is to the settings change alone.
  Worth a deliberate look at that app after publishing rather than a surprise later.
- **`MAILERS`'s exact expected shape/keys for this Django version aren't yet confirmed against
  Django's own docs/release notes** by this Envelope — the Orchestrator/implementer must verify the
  correct settings shape while implementing, not guess from the deprecation warning text alone.
- **dcm's CI test gate is real but doubly conditional — and both conditions happen to hold here.**
  `publish.yml` runs `pytest -q` ("Install and run tests", `:98`) *before* "Build package" (`:106`)
  and "Publish to PyPI" (`:110`), so a red suite does block the publish. But it only runs when
  (a) the push touches the `paths` filter (`pyproject.toml` or `src/django_core_micha/**`) — a
  commit touching only `tests/`, `CHANGELOG.md` or docs never triggers the workflow at all — and
  (b) `should_publish == 'true'`, i.e. the version in `pyproject.toml` actually increased; every
  step carries that `if:`, the test step included, so a version-less push skips the tests too.
  This WO bumps the version *and* touches `pyproject.toml`, so the gate does fire for it. The
  Orchestrator's own authoritative run before commit stands regardless — `AGENTS.md` makes it the
  gate — but not because CI lacks one.

## Required tests to WRITE

1. Extend `tests/test_email_provider.py` (or add an adjacent test) asserting `settings.MAILERS`
   contains a `"default"` entry that resolves to the same backend `resolve_email_backend()`
   already picks for a given `EMAIL_PROVIDER`/config combination — covering at least the resend
   case (the estate's actual current config) and the local/console fallback case (already exercised
   by existing tests in that file).
2. No new test is needed for the pin widening itself (a constraint/metadata change, not behaviour)
   — covered by re-running the existing full suite under Django 6.1 as the verification step.

# B. Implementation map — filled by the Orchestrator — ADDRESSED TO THE IMPLEMENTER

> **PLACEHOLDER — not yet written.** The Orchestrator fills this section on `git pull` with the
> context package (exact `pyproject.toml` line, the settings file(s) where `EMAIL_BACKEND`/
> `EMAIL_PROVIDER` are currently resolved with `:line` references, the confirmed `MAILERS` settings
> shape for the installed Django 6.1, `CHANGELOG.md`'s current head entry to insert above), the
> absolute target repo working directory, the progress contract, and the preamble. **Do not
> dispatch Codex while this placeholder stands.**

# C. Orchestrator only — NOT ADDRESSED TO THE IMPLEMENTER

> **If you are the implementer reading this work order as your own specification: STOP at this line.**
> Everything below describes what the Orchestrator does AFTER you finish. You do none of it — no
> reviewers, no verification run, no register edit, no commit. You ARE the invocation described
> below; do NOT shell out to `codex exec`.

## Execution directive

Codex-first via `codex exec` in the background, invoked directly through Bash with BOTH
`--skip-git-repo-check` and `--dangerously-bypass-approvals-and-sandbox`, after checking
`.claude/codex-status.md`. cwd = the `django-core-micha` repo root (no backend/frontend split in
this repo).

## Review routing

Tier 3 → independent `reviewer`, full context (touches shared-core settings resolution used by
every consuming app). No `ui_reviewer` (backend only). Consider `sec_reviewer` only if the
implementation ends up touching `EMAIL_PROVIDER`/credential resolution beyond wiring `MAILERS` to
its existing output — flag for the operator if that boundary is crossed; not expected per scope.

## Verification

Authoritative run: dcm's full suite (`pytest`, `tests/` + the four app suites under
`src/django_core_micha/{notifications,onboarding,messaging,activity}/tests`) under Django 6.1 —
this IS the affected set for a shared-core dependency-pin change, not a narrow slice, per the
shared-core-ripple exception in Test scope. Confirm both: (a) 0 failures, matching the 667/667
already observed in this session, and (b) the `RemovedInDjango70Warning` no longer appears in the
output.

## Register + commit

`WORK_ORDERS.md` row to `done` with the publish commit SHA, the named `reviewer` verdict, the
confirmed pytest count, and the new PyPI version (2.43.0). `main` only (this repo has no
`develop`). Add `DEPS-*` to the workstream-prefix table in `WORK_ORDERS.md`'s header (new prefix,
not yet documented there) in the same commit.
