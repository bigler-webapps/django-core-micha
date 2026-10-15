# DCM-DEPS-3 — django-core-micha declares only what every consumer needs

Part of the estate dependency reduction decided 2026-10-15 (operator + Expertenchat). This order
and `ui-core-micha` `UCM-DEP-2` land and release first; every app then has its own order that bumps
to these releases (`<TOKEN>-DEP(S)-<n>` in each app repo, e.g. `SPX-DEP-6`, `JG-DEPS-6`).

# A. Envelope

## Goal & expected outcome

- Ziel: a consumer that does not use an optional dcm feature no longer installs that feature's
  third-party packages, and the estate runs on one Postgres driver.
- Expected outcome: a new **major** release of `django-core-micha` whose `[project].dependencies`
  hold only what is needed on every app start; optional features are behind named extras; the
  CHANGELOG entry carries a consumer migration table (extra -> dcm features that need it -> packages
  it brings) that the app orders read to pick their extras.

## Scope

1. **Feature extras.** Move out of `[project].dependencies` into `[project.optional-dependencies]`:
   - `ai`: `openai`, `anthropic` (used by `extraction/drivers/*` and `assistant/turns.py`, both lazy
     imports today);
   - `extraction`: `pypdf`, `Pillow` (`extraction/input_prep.py`; `messaging/attachments.py` imports
     PIL lazily);
   - `push`: `pywebpush` (`notifications/delivery.py`, already import-guarded);
   - `scripts`: `PyYAML` (only the `[project.scripts]` entry points: `generate-env`, `sync-secrets`,
     `run-dev`, `provision-app`, `provision-server-access`).
   Keep `test` as is. If the audit's file list above turns out incomplete, the rule is: a package
   moves to an extra exactly when no module reached on app start (CORE_APPS app configs and their
   `ready()`, models, `api_urls`, `settings_base`) imports it at module level.
2. **Boot without extras.** An app with none of the extras installed starts and serves every
   CORE_APPS endpoint. Any module-level import of a moved package that IS reached on app start
   becomes lazy. Specifically verify `extraction/input_prep.py:8` (top-level `PIL` import) is not on
   the start path; if it is, make it lazy.
3. **Clear failure when a feature is used without its extra.** Calling an `ai`- or
   `extraction`-gated feature without the extra raises an error whose message names the missing
   extra (`django-core-micha[ai]` etc.). `pywebpush` keeps today's behaviour (push delivery disabled
   when the package is absent) — no new error there.
4. **One Postgres driver.** Declare `psycopg[binary]` (psycopg 3) and remove `psycopg2-binary`. No
   `pool` extra. dcm's own test environment runs on psycopg 3.
5. **Declare `redis`.** `settings_base` configures `django.core.cache.backends.redis` and
   `SESSION_ENGINE=cached_db`, so `redis` is a core runtime dependency; declare it in
   `[project].dependencies`.
6. **Release** as the next major version with the CHANGELOG migration table from the expected
   outcome, including the driver switch and the `redis` declaration.

## Non-goals / do-not-touch

- `cryptography`, `filetype`, `channels`, `channels_redis`, `whitenoise`, `django-cors-headers`,
  `django-environ`, `djangorestframework`, `django-allauth[mfa,socialaccount]` stay core.
- `django-anymail` stays. (The audit claim that `settings.MAILERS` is ignored came from a Django 6.0
  test venv; dcm requires Django >= 6.1. Not this order's subject.)
- No allauth change: the `[headless]` extra and the unconditional `allauth.headless.urls` include
  are untouched. No new version floors/caps for packages that stay.
- No change in any consumer repo — each app has its own order.
- No change to the test venv drift, the repo-root `tests` package layout, or the stray `src;C`
  directory.

## Tier · precondition / gate

- Tier 3: a change inside shared-core that removes dependencies consumers use today, plus the
  Postgres driver switch every consumer inherits on its next bump.
- Precondition: none. Ordering: released before any app order of this reduction starts.

## Risks

- A moved package is imported at module level on a start path the audit missed: every consumer
  without the extra fails at boot. Covered by the boot-without-extras test below.
- psycopg 3 behaves differently from psycopg2 in edge cases (adaptation of custom types,
  server-side cursors, timezone handling); dcm's own suite is the first place this shows.
- Consumers that rely on `openai`/`anthropic`/`pypdf`/`Pillow`/`pywebpush`/`PyYAML` arriving through
  dcm lose them on the bump unless they request the extra or pin the package themselves; the
  migration table is what prevents that.

## Required tests to WRITE

- Boot without extras: with `openai`, `anthropic`, `pypdf`, `PIL`, `pywebpush` and `yaml` made
  unimportable (import blocker in `sys.meta_path` for the test), importing every CORE_APPS app
  config, the models, `django_core_micha.api_urls` and `settings_base` succeeds.
- Feature without extra: one `ai`-gated and one `extraction`-gated entry point each raise an error
  whose message names the extra.
- Driver: under the test settings, `django.db.connection` uses psycopg 3 (the backend's `Database`
  module is `psycopg`).

# B. Implementation map — filled by the Orchestrator — ADDRESSED TO THE IMPLEMENTER

Work from this package; do not explore broadly from scratch; open only the named files to verify. If you must dig deeper, delegate to a read-only Explore sub-agent (Haiku). Part A is the authoritative spec; this map points at the seams.

## Target repo working directory (absolute)

`C:\Users\Micha Bigler\Documents\webapps\django-core-micha` (branch `main`). Never the workspace root.

## Context package

**Import sites of the moved packages (verified):**
- `openai`: `src/django_core_micha/extraction/drivers/openai_driver.py:38` (lazy, inside a try that already raises `DocumentExtractionError(MISSING_DEPENDENCY, ...)`), `src/django_core_micha/assistant/turns.py:103` (`_make_driver`, lazy).
- `anthropic`: `extraction/drivers/anthropic_driver.py:40` (lazy, same pattern), `assistant/turns.py:107`.
- `Pillow`: `extraction/input_prep.py:8` is a TOP-LEVEL `from PIL import Image`; `messaging/attachments.py:59` is lazy. Verify whether anything reached on app start imports `extraction.input_prep`; either way make the PIL import lazy there.
- `pypdf`: `extraction/input_prep.py:61` (lazy, inside try).
- `pywebpush`: `notifications/delivery.py:13` is already import-guarded; keep that behaviour, add no error.
- `PyYAML`: top-level `import yaml` in `scripts/drift_check.py`, `generate_env.py`, `provision_app.py`, `provision_server_access.py`, `run_dev.py`, `sync_secrets.py` only (entry points); nothing else under `src/` imports it. Leave them as they are.
- Postgres: `settings/settings_base.py:151` uses `django.db.backends.postgresql`; the `OPTIONS` (`connect_timeout`, `keepalives*`) are libpq parameters valid for psycopg 3, keep them.

**Packaging (`pyproject.toml`):** `[project].dependencies` currently lists `psycopg2-binary`, `PyYAML`, `pywebpush`, `Pillow`, `pypdf`, `openai`, `anthropic` (these move or change); keep every other entry as is, add `redis` and `psycopg[binary]`. Add extras `ai`, `extraction`, `push`, `scripts` under `[project.optional-dependencies]` with the SAME version floors the packages carry today (openai>=2.54.0, anthropic unpinned, pypdf>=6.19.0, Pillow>=12.3.0, pywebpush unpinned, PyYAML unpinned). The `test` extra stays unchanged. Do NOT touch `version` or `CHANGELOG.md` (the Orchestrator releases 3.0.0 and writes the migration table), and do NOT touch `.github/workflows/`.

**Tests that must change with the move:**
- `tests/test_declared_dependencies.py`: `_declared_distribution_names()` reads only `[project].dependencies`; after the move the lazy imports of openai, anthropic, pypdf, PIL, pywebpush, yaml must count as declared when present in an extra. Use the union of dependencies and the four feature extras, not `test`.
- `tests/test_extraction.py` imports `PIL` and the `openai`/`anthropic` drivers and runs in an environment that has all extras installed (CI installs them); do not weaken it.
- New tests go in a NEW file `tests/test_optional_extras.py` (the repo-root `tests` package is already in `testpaths`): (1) boot without extras in a subprocess whose `sys.meta_path` blocks `openai`, `anthropic`, `pypdf`, `PIL`, `pywebpush`, `yaml` (a finder raising `ModuleNotFoundError`), importing the CORE_APPS app configs and models, `django_core_micha.api_urls` and `settings_base` (see `tests/test_settings_base_dependencies.py` for how it already runs `settings_base` in a subprocess and which env it needs); (2) one `ai`-gated and one `extraction`-gated entry point (suggested: `assistant.turns._make_driver("openai_api", "x")` and the `extraction/input_prep.py` image path with PIL blocked) raise an error whose message contains `django-core-micha[ai]` / `django-core-micha[extraction]`; (3) driver: `django.db.backends.postgresql.base.Database.__name__ == "psycopg"` and `psycopg2` is not declared in `pyproject.toml`.

**Suggested seam for the error:** one small helper module (for example `src/django_core_micha/_extras.py`) with a function that turns a missing import into an `ImportError` naming the extra; the lazy import sites call it. For `extraction`, keep the existing `DocumentExtractionError(MISSING_DEPENDENCY, ...)` contract and put the extra name in its message instead of inventing a new exception type there. For `assistant/turns.py` the surrounding code maps `DriverError`; the client-facing code stays `not_configured`, the extra name goes into the exception message and log, never a stack trace to the client.

**Invariants and pitfalls:**
- Nothing reached on app start may import a moved package at module level (CORE_APPS app configs and `ready()`, models, `api_urls`, `settings_base`). If you find one besides `input_prep`, make it lazy and say so in your final report.
- Do not change behaviour for consumers that DO have the extras installed.
- If a test cannot run because an extra or psycopg 3 is missing from the local environment, report `RESULT: BLOCKED` naming what is missing instead of installing anything.
- Windows host: use `pathlib`, explicit UTF-8 for file I/O.

## Required tests

As in Part A "Required tests to WRITE" and the file list above.

## Preamble — REQUIRED block

> The text above is the COMPLETE spec — the committed WO file's content, not a plan to refine; there
> is no separate plan file. Read the nearest `AGENTS.md`, the relevant `.codex/skills/<role>/SKILL.md`, and the
> app `MEMORY.md` ONLY for conventions. Stay in scope; do not touch auth/permissions/deps/schema/CI
> unless the spec says so; do not update `MEMORY.md`. **Do NOT edit `WORK_ORDERS.md` — the register
> row and the review verdicts are the orchestrator's alone.** **Your tools are for editing source
> and test files and for running the tests you wrote — nothing else.** Do NOT install dependencies,
> touch a lockfile, run a package manager, or tidy up stray files; if something in the repo state
> blocks you, stop and report it as `RESULT: BLOCKED <reason>` instead of fixing it. Do NOT
> `git add`/`commit`/`push` — leave every
> change uncommitted in the working tree for the orchestrator's independent review. WRITE the tests
> the `Required tests` section calls for AND **RUN the tests you just wrote** to confirm they execute
> and pass — that is the ONLY test run you do (NOT the app's affected/full suite, NOT any review).
> The orchestrator re-runs the authoritative set + does the independent review after you finish —
> those are the gate; your own run does not count as the gate.
>
> Narrate continuously: a `PLAN: <step1> | <step2> | …` line up front, then a single-line
> `PROGRESS: [<n>/<total>] <present-tense action>` before every relevant action (and `… done` on
> completion), spaced so no gap exceeds ~2 min, stdout unbuffered, plus exactly one final
> `RESULT: DONE|BLOCKED <reason>`. Use explicit UTF-8 for all file I/O.

# C. Orchestrator only — not addressed to the implementer

> **If this WO was dispatched to you as an implementer: STOP here.** Everything below is what the Orchestrator does after you finish. You do none of it.

Gate #1 recorded at django-core-micha@83a0a7b. Tier 3 · tests: boot without extras, feature without extra, driver. Execution: codex/gpt-6-luna (`implementation`), cwd = repo root; Codex available per the record of 2026-10-15. Orchestrator-owned: version 3.0.0, CHANGELOG with the migration table, `.github/workflows/publish.yml` install line (`pip install -e ".[test,ai,extraction,push,scripts]"`), a fresh venv with the extras plus psycopg 3 for the authoritative run, review lenses (all four plus `sec_reviewer`), register row, commit, push and observing the publish run. Report back to local_d0c99207-e683-4325-94e7-9889d999eb6e.
