> **Self-address guard:** if you are the implementer reading this work order as your own
> specification, Part C is not addressed to you — it tells the Orchestrator how to invoke you; you
> ARE that invocation. Do NOT shell out to `codex exec`.

# DEPS-1 — Open the Django pin to 6.1 and migrate email onto MAILERS (resend-only)

Status: **planned** · Tier: 3 · Target repos: django-core-micha (main) **+ spesix (develop)** · Datum: 2026-08-18 · Backend only

# A. Envelope — authored by the Expertenchat

## Goal / expected outcome

dcm's `pyproject.toml` caps `Django>=6.0.5,<6.1`. That cap is the sole reason every consuming app's
Renovate PR proposing `Django==6.1` fails to resolve (`uv pip install`: "No solution found") —
confirmed live in `kerzenziehen` PR #50 and `innoservice` PR #55.

Widen the pin to `Django>=6.0.5,<7.0` **and complete the migration off Django's deprecated
top-level email settings onto `MAILERS`**, reducing dcm's mail providers to the two the estate
actually uses: **console** (local/dev/test/missing-credentials) and **resend** (everything else).
`smtp` and `postmark` are removed.

**This release does not turn those two PRs green by itself.** Both currently pin
`django-core-micha==2.41.2`, which still carries the `<6.1` cap — Renovate must first pull them up
to `2.43.0` (its next scan, or a manual rebase) before they can resolve. Expect them to stay red
until that happens; that is not a sign the fix failed.

## Why this WO grew — the original scope was not implementable

An earlier version of this Envelope scoped the email half as "wire `settings.MAILERS` alongside the
existing settings, don't touch the provider abstraction." **That is impossible on Django 6.1**, and
the attempt is what surfaced it. Django 6.1's `_check_email_settings_conflicts`
(`django/conf/__init__.py:63-71`) raises `ImproperlyConfigured` at settings load when `MAILERS` and
**any** deprecated email setting are both explicitly set:

> `Deprecated email settings are not allowed when MAILERS is defined: <names>.`

Since dcm's `settings_base.py` sets `EMAIL_BACKEND` unconditionally, adding `MAILERS` beside it
means **any app doing `from django_core_micha.settings.settings_base import *` refuses to boot** —
a hard crash, strictly worse than the `RemovedInDjango70Warning` this WO set out to close. Doing it
properly means removing the deprecated settings, which is what the rest of this Envelope specifies.

## Background — measured this session, not inferred

**1. Django 6.1's deprecated-setting list is exact** (read from
`django/conf/__init__.py:42-54` in a real 6.1 install): `EMAIL_BACKEND`, `EMAIL_FILE_PATH`,
`EMAIL_HOST`, `EMAIL_HOST_PASSWORD`, `EMAIL_HOST_USER`, `EMAIL_PORT`, `EMAIL_SSL_CERTFILE`,
`EMAIL_SSL_KEYFILE`, `EMAIL_TIMEOUT`, `EMAIL_USE_SSL`, `EMAIL_USE_TLS`.

**dcm currently sets six of them**: `EMAIL_BACKEND`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`,
`EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` (`settings_base.py:236-259`). All six must go.

`DEFAULT_FROM_EMAIL` is **not** on the deprecated list and stays — but its current default is
`EMAIL_HOST_USER`, which will no longer exist as a setting (see Scope 3).

**2. The read/write asymmetry — measured, and it is the awkward one:**

| Operation with `MAILERS` defined | Behaviour under Django 6.1 |
|---|---|
| read `settings.EMAIL_BACKEND` | `AttributeError: The EMAIL_BACKEND setting is not available when MAILERS is defined.` |
| write `settings.EMAIL_BACKEND = ...` | succeeds, emits only a `RemovedInDjango70Warning` |
| effect of that write on `get_connection()` | **none** — `MAILERS` still wins |

A write therefore *looks* like it worked and silently does nothing. That is the mechanism behind
finding 4.

**3. Estate provider census — every app is already resend-only.** All 13 repos declaring
`EMAIL_PROVIDER` in `project.yaml` declare `resend`, and each has `RESEND_API_KEY` in its
`secrets.yaml`: bigler-consult, cockpit, fitness-monitor, hpc-bridge, hram, innoservice, jg-ferien,
kerzenziehen, reimbursements, spesix, survey_app, survey_contact_app, webshop-guenter. **Not one
declares `smtp` or `postmark`, and no `POSTMARK_SERVER_TOKEN` is declared anywhere.** hram's
`docker-compose.local.yml` sets `EMAIL_PROVIDER=console` for local work — the path being kept.
Removing smtp and postmark therefore removes code with **zero live consumers**, which is what makes
this a cleanup rather than a behaviour change.

**4. `spesix` has a real, measured break** — `backend/claims/test_submission_workflow.py:114,130`,
both tests set `settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"` to
capture mail. Once dcm defines `MAILERS`, that write is inert (see finding 2), and the two tests
fail in *different* ways:

- `test_approve_submission_sends_pdf_when_email_configured` asserts `len(mail.outbox) == 1` →
  **fails loudly.** Visible, fine.
- `test_approve_without_reimbursement_email_stays_approved` asserts `len(mail.outbox) == 0` →
  **passes for the wrong reason.** The outbox is empty because locmem was never active, not because
  no mail was sent. It becomes a vacuous test that would no longer catch the regression it exists
  for. This is the one that matters, and it is why spesix is fixed in this WO rather than tracked.

**5. `reimbursements` does not break.** `backend/tests/test_settings_assertions.py:39-43` sets
`EMAIL_HOST`/`EMAIL_PORT`/`EMAIL_USE_TLS`/`EMAIL_USER`/`EMAIL_PASSWORD` as *environment variables*
for a subprocess settings-load assertion, not as Django settings. After this change nothing reads
those env keys; the test still passes, the keys are merely dead scaffolding. Cosmetic — noted so it
is not mistaken for a break, explicitly **not** in scope to clean.

**6. No app reads the removed settings in Python.** Verified per-repo across all twelve app repos'
`backend/**/*.py`; the only hits are the two named above. **Method note for whoever re-checks:** a
single Grep rooted at the workspace directory returns "No matches found" even for strings that
demonstrably exist in the nested repos — it does not descend into them. Search per repo, or the
answer will be a false negative.

**7. dcm's own suite passes under Django 6.1 unchanged** — 667 passed, 0 failures (Python 3.13,
`channels==4.3.2`/`channels_redis==4.3.0` unmodified), identical to the 6.0.8 baseline.
`channels`/`channels_redis` have not published a release with a Django 6.1 trove classifier, but
`django/channels` upstream already merged 6.1 test support and a stable-version bump on `main`
(PRs #2225, #2229) — release-cadence lag, not a known incompatibility. `django-allauth` (65.19.1)
and `djangorestframework` (3.18.0) declare 6.1 support outright.

**8. `MAILERS` is a viable test-capture mechanism** (verified both ways on a real 6.1 install):
`override_settings(MAILERS={"default": {"BACKEND": "...locmem.EmailBackend"}})` and a direct
`settings.MAILERS = {...}` assignment both route mail into `mail.outbox` correctly. This is the
replacement spesix's tests need.

## Scope

1. **`pyproject.toml`** — widen `Django>=6.0.5,<6.1` → `Django>=6.0.5,<7.0`. Operator decision:
   open through the next major rather than a narrow `<6.2`; see Risks.

2. **`_email_config.py` — reduce `resolve_email_backend()` to two targets: console and resend.**
   Remove the `smtp` and `postmark` branches entirely, along with the now-unused `host`, `password`
   and `postmark_token` parameters. The empty-provider auto path becomes: console when
   `IS_LOCAL`/`DEBUG`, otherwise resend (falling back to console with the existing warning when
   `RESEND_API_KEY` is absent). Keep the never-raises contract and the existing `_warn()` behaviour
   — an app must still boot on a misconfiguration. Keep `build_mailers()` as the single place the
   `MAILERS` dict shape is constructed.

3. **`settings_base.py` — remove all six deprecated settings.** `MAILERS` becomes the only mail
   backend configuration. `ANYMAIL` stays as-is (it is dcm's own setting, read directly by the
   anymail backend, not a deprecated Django name). `EMAIL_PROVIDER` stays (dcm's own selector, not
   a Django setting) — update its comment, which currently documents the removed providers.
   `DEFAULT_FROM_EMAIL` must keep working without `EMAIL_HOST_USER`: read the from-address into an
   **underscore-prefixed module-local** (e.g. `_email_from_user = env("EMAIL_USER", default="")`)
   and use that as the default. The underscore serves twice — Python's `import *` skips underscore
   names, and Django only treats UPPERCASE module-level names as settings, so it satisfies the
   conflict check by construction rather than by convention.

4. **`spesix` companion fix** (`backend/claims/test_submission_workflow.py:114,130`) — replace both
   `settings.EMAIL_BACKEND = "...locmem..."` writes with the `MAILERS` equivalent per finding 8.
   **`test_approve_without_reimbursement_email_stays_approved` must be shown to be non-vacuous
   afterwards** — its `len(mail.outbox) == 0` has to fail if mail *were* sent, which today it would
   not. Prove that (temporarily make it send, watch it fail, revert), don't assume it.

5. **Version bump `2.42.1` → `2.43.0`** + `CHANGELOG.md` entry covering both halves. Operator
   decision on minor-not-major: the removed surface has **zero live consumers** (finding 3), no app
   reads the removed settings (finding 6), and the one real dependant is fixed in this WO
   (finding 4) — and Renovate routes majors to the Dependency Dashboard for per-app manual
   approval, which would leave `kerzenziehen` PR #50 / `innoservice` PR #55 blocked and defeat this
   WO's stated purpose. Semver measures breakage for actual consumers; here there are none.

6. **Publish to PyPI** — existing `publish.yml`, automatic on the `main` push.

## Explicit non-goals / do not touch

- **Do not keep `smtp` or `postmark` "just in case."** Their removal *is* this WO (operator
  decision, asked and answered this session) — a cleanup that ships more than it removes has
  failed. Do not leave dead branches, unused parameters, or commented-out provider code behind.
- **Do not touch any consuming app's `Django==` or `django-core-micha==` pin**, including the six
  apps that carry their own `<6.1` cap (`cockpit`, `hram`, `spesix`, `fitness-monitor`,
  `hpc-bridge`, `webshop-guenter` — see Risks). Widening those is per-app work with its own
  verification. The **only** app change in scope is spesix's two test lines (Scope 4) — not its
  pins, not anything else in that repo.
- **Do not touch dcm's other dependency pins** (`django-allauth`, `djangorestframework`,
  `django-cors-headers`, `channels`, `channels_redis`, `django-anymail`, `django-environ`,
  `whitenoise`, `cryptography`). If the 6.1 suite run surfaces a failure traceable to one of them,
  **stop and report** — do not paper over it with a bump inside this WO.
- **Do not change the console-fallback-on-missing-credentials behaviour** (see Risks — it is a
  known, deliberately out-of-scope gap, not an oversight to fix while passing through).
- **Do not clean `reimbursements`' now-dead email env keys** (finding 5) — out of scope.
- Do not address any deprecation warning other than the email one.

## Risks

- **resend becomes a single point of failure with no configured alternative.** Today a provider
  switch is an `EMAIL_PROVIDER` env change; after this it is a code change plus a dcm release plus
  an app pin bump. Accepted per the operator's resend-only decision, and the estate is already
  resend-only in practice (finding 3) — but the *recovery path* genuinely gets longer, and that is
  the real cost of this cleanup.

- **A missing `RESEND_API_KEY` in production means mail silently disappears.** `resolve_email_backend()`
  falls back to console with a warning rather than failing loudly — mail is "sent" to stdout and
  lost, with nothing user-visible to notice it by. This behaviour exists today; what changes is that
  it becomes the *only* fallback, so the blast radius of one missing secret grows. **Explicitly
  surfaced and left out of scope** rather than silently accepted: making non-local boot fail hard on
  a missing key is a defensible follow-up, but it is a behaviour change affecting every app's boot
  path and does not belong in a dependency-migration WO. Track it; do not fold it in.

- **`<7.0` is a wide-open upper bound.** Every future Django 6.x minor installs without a fresh dcm
  compatibility check, and nothing flags it until symptoms appear downstream. Explicit operator
  trade-off against `<6.2`.

  **Consuming apps do not inherit that openness.** All twelve are independently bounded today
  (verified against remote `develop`, not local checkouts): six pin exactly (`jg-ferien`,
  `survey_app`, `survey_contact_app`, `reimbursements` at `==6.0.7`; `kerzenziehen` `==6.0.5`;
  `innoservice` `==6.0.6`), six carry their own cap (`cockpit`, `spesix`, `hpc-bridge`,
  `webshop-guenter` at `>=6.0.5,<6.1`; `fitness-monitor` `>=6.0.6,<6.1`; `hram` `>=6.0.7,<6.1`). So
  no app can silently receive Django 6.1 as a side effect of a dcm bump. The consequence runs the
  other way: for those six, **widening dcm moves nothing** — their own `<6.1`, today a redundant
  mirror of dcm's cap, becomes the sole binding constraint, and it is exactly the kind of leftover
  nobody thinks to look at. Track it, or they sit on 6.0.x indefinitely while everyone assumes
  DEPS-1 handled it.

- **`reimbursements` takes this release with no PR and no review.** Its dcm pin is
  `django-core-micha>=2.42.1` — unbounded — so its next image build resolves to 2.43.0 and picks up
  the whole mail migration without a Renovate PR or a pin bump. Its Django stays `==6.0.7`, so no
  Django movement; the exposure is the mail change alone. Given this WO now *removes* settings
  rather than adding one, that app deserves a deliberate look after publishing.

- **`channels`/`channels_redis` carry no published 6.1 classifier.** The 667/667 run is real
  evidence but not an upstream guarantee; an edge case outside dcm's coverage could exist.

- **dcm's CI test gate is real but doubly conditional — both conditions hold here.** `publish.yml`
  runs `pytest -q` (`:98`) *before* "Build package" (`:106`) and "Publish to PyPI" (`:110`), so a red
  suite does block the publish. But it only runs when (a) the push touches the `paths` filter
  (`pyproject.toml` or `src/django_core_micha/**`) — a commit touching only `tests/`, `CHANGELOG.md`
  or docs never triggers the workflow — and (b) `should_publish == 'true'`, i.e. the version
  actually increased; every step carries that `if:`, the test step included. This WO satisfies both,
  so the gate fires. The Orchestrator's own run before commit stands regardless — `AGENTS.md` makes
  it the gate — but not because CI lacks one.

## Required tests to WRITE

1. **`MAILERS` shape, through the real production path.** Assert `build_mailers()`'s output has a
   `"default"` entry whose `"BACKEND"` equals whatever `resolve_email_backend()` returned — for the
   resend case and the console case. Exercise the actual shared helper, not a locally reimplemented
   dict (a re-implementation would not catch a typo in the production line).

2. **A deprecated-settings inventory guard**, modelled on the estate's existing `S112`
   `assert_all_consumers_secure` pattern: assert that loading dcm's settings leaves **none** of
   Django 6.1's eleven `DEPRECATED_EMAIL_SETTINGS` set as a module-level setting. Import the list
   from `django.conf` rather than hardcoding it, so a future Django release that extends it is
   caught automatically. Without this the removal is a convention, and the next person to add
   `EMAIL_TIMEOUT` re-breaks every consuming app's boot.

3. **Removal is observable, not silent:** `EMAIL_PROVIDER=smtp` and `EMAIL_PROVIDER=postmark` now
   take the unknown-provider path — console backend plus a warning — rather than resolving. This
   pins the intended post-removal behaviour so a later reader can tell deletion from regression.

4. **`DEFAULT_FROM_EMAIL` still resolves** from `EMAIL_USER` without `EMAIL_HOST_USER` existing as a
   setting, and the explicit `DEFAULT_FROM_EMAIL` env override still wins.

5. **spesix:** both converted tests pass, and
   `test_approve_without_reimbursement_email_stays_approved` is demonstrated non-vacuous (Scope 4).

# B. Implementation map — filled by the Orchestrator — ADDRESSED TO THE IMPLEMENTER

> **PLACEHOLDER — must be re-derived. The previous Part B is void.**
>
> It mapped the superseded "wire `MAILERS` alongside the existing settings" approach, which Django
> 6.1 rejects outright (see "Why this WO grew"). Its instruction to add `MAILERS` next to
> `EMAIL_BACKEND` produces the boot crash this WO now exists to avoid — do not carry any of it
> forward.
>
> The Orchestrator fills this section fresh with the context package (the `settings_base.py` and
> `_email_config.py` slices with `:line` refs, spesix's two test lines, `CHANGELOG.md`'s insert
> point), both absolute target repo working directories, the progress contract, and the preamble.
> **Do not dispatch Codex while this placeholder stands.**

# C. Orchestrator only — NOT ADDRESSED TO THE IMPLEMENTER

> **If you are the implementer reading this work order as your own specification: STOP at this line.**
> Everything below describes what the Orchestrator does AFTER you finish. You do none of it — no
> reviewers, no verification run, no register edit, no commit. You ARE the invocation described
> below; do NOT shell out to `codex exec`.

## Working-tree state — read before dispatching

An earlier partial implementation of the superseded scope is **uncommitted in the dcm tree**
(`pyproject.toml`, `settings_base.py`, `_email_config.py`, `tests/test_email_provider.py`,
`CHANGELOG.md`). That state is the broken intermediate — it sets `EMAIL_BACKEND` **and** `MAILERS`,
which is exactly the `ImproperlyConfigured` boot crash. It must be reconciled into the new scope, not
shipped and not blindly reverted: `build_mailers()` in `_email_config.py` is worth keeping (it came
from an independent reviewer's finding that the tests were reimplementing the dict shape instead of
exercising the production line, and that reasoning still holds). Diff it deliberately before
dispatching.

## Execution directive

Codex-first via `codex exec` in the background, invoked directly through Bash with BOTH
`--skip-git-repo-check` and `--dangerously-bypass-approvals-and-sandbox`, after checking
`.claude/codex-status.md`. cwd = the `django-core-micha` repo root for the main work; the spesix
change (Scope 4) is a separate, tiny hand-off with cwd = the spesix repo root.

## Review routing

Tier 3 → independent `reviewer`, full context. No `ui_reviewer` (backend only).
**`sec_reviewer` IS warranted here** and should run concurrently: this changes how mail credentials
are resolved and removes two credential-reading paths, and mail carries the estate's account
security traffic (password resets, invitations). The specific question for it: can any configuration
— including a missing `RESEND_API_KEY` — cause security-relevant mail to be silently discarded to
console without an operator-visible signal?

Two repos means two diffs. The spesix change is small enough to go to the same `reviewer` in the
same package rather than spawning a separate pass, but it must be **named explicitly** in that
package — a reviewer handed only the dcm diff cannot see the break the spesix fix addresses.

## Verification

Authoritative run: dcm's full suite (`pytest` — `tests/` plus the four app suites under
`src/django_core_micha/{notifications,onboarding,messaging,activity}/tests`) under Django 6.1. This
IS the affected set for a shared-core settings + dependency change, per the shared-core-ripple
exception in Test scope. Confirm (a) 0 failures against the 667/667 baseline, and (b) the
`RemovedInDjango70Warning` is gone from the output.

Plus, in spesix: that app's mail-touching tests, including the non-vacuity demonstration.

**One check the suite cannot give you:** dcm's own `tests/settings.py` is a standalone minimal
settings module that never imports `settings_base.py`, so a green dcm suite does **not** prove a
consuming app boots. Before commit, load a real consuming app's settings against the migrated dcm
(`python -c "import backend.settings"` with `DJANGO_SETTINGS_MODULE` set, per
`reimbursements/backend/tests/test_settings_assertions.py`'s pattern) and confirm no
`ImproperlyConfigured`. The original defect in this WO was invisible to dcm's suite for exactly this
reason — do not repeat it.

## Register + commit

`WORK_ORDERS.md` row to `done` with the publish SHA, the named `reviewer` **and** `sec_reviewer`
verdicts, the confirmed pytest count, the consuming-app boot check, and the published version
(2.43.0). dcm: `main` only. spesix: `develop` only, as its own commit in its own repo, cross-
referenced in both registers.

Two follow-ups to record as tracked entries, not to fold in: the six apps' redundant `<6.1` caps,
and the silent-console-fallback gap (both in Risks).
