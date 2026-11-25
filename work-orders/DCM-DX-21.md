# DCM-DX-21 — The test suite runs only against PostgreSQL

## A. Envelope

Target: django-core-micha (main). Tier 3 (shared core, its CI workflow and a test-database Compose file). Part of the estate decision of 2026-11-25 that every app and the core are always tested against PostgreSQL (workspace WS-GOV-151 tracks the app side).

### Evidence

- `tests/settings.py:46-50` configures SQLite `:memory:` for the whole suite (`pyproject.toml` `[tool.pytest.ini_options]` `DJANGO_SETTINGS_MODULE = "tests.settings"`); every `project.yaml` scope uses it.
- Six tests skip when the database is not PostgreSQL, so they never run: billing (`test_object_checkout_concurrency.py:29` and two more billing tests), `notifications/tests/test_notification_feed_indexes.py` (two), `tests/test_registration_extension.py:420`. DCM-AUTH-15 ran its PostgreSQL concurrency test by hand once.
- `.github/workflows/ci.yml` runs `pytest -q` with no database service; `publish.yml` runs the suite as a gate before publishing, also without one.
- The apps' shared CI (`workflow-templates` `app-ci.yml`) already starts `postgres:18` for backend tests.

### Goal and expected outcome

- The suite runs only against PostgreSQL. The test settings read host, port, database, user and password from environment variables with loopback defaults that match the repo's own test database; no SQLite path remains. When the database is unreachable the run fails at the start with a message naming the start command; nothing is skipped silently.
- The repo carries a Compose file with one PostgreSQL service (same major as `app-ci.yml`, synthetic test-only credentials, published on a loopback port, no other service). The `project.yaml` scopes that use the database declare it through `stack_container`, `stack_env` and `stack_fix`, so `tests.py` checks it before running and prints the start command when it is not running.
- `ci.yml` and the test gate in `publish.yml` run the suite against a PostgreSQL service.
- A test asserts that the configured database is PostgreSQL through psycopg (as Gustav's `tests/test_database_driver.py`).
- The six PostgreSQL-only tests run and pass; tests that only passed on SQLite are fixed in this WO.

### Scope and constraints

- In scope: `tests/settings.py`, `pyproject.toml` pytest options if needed, `project.yaml`, the new Compose file, `ci.yml`, the test step of `publish.yml`, the driver test, fixes to tests or code that fail only on PostgreSQL.
- Out of scope: ui-core-micha (no database), app repos, `run-dev`, the publish logic itself, a version bump (no `src` change is expected; if one becomes necessary it follows the normal release flow).

### Risks

- Tests that relied on SQLite behaviour (ordering, case sensitivity, transaction semantics) turn red; if they are many, report the count before fixing.
- Two runs in parallel share the test database name (DX-1 is the planned fix); scopes run one after the other.
- Starting the test database is not covered by the free `run-dev` path and may ask the operator once per session.

### Required tests to write

- The database driver test (PostgreSQL through psycopg).
- A settings test that no configured database uses the SQLite engine.
- The evidence run lists the six formerly skipped tests as passed.

## B. Implementation map

- Settings: tests/settings.py (DATABASES ~46-50); pyproject.toml ([tool.pytest.ini_options] ~55-60).
- Scopes: project.yaml (tests: backend, test-management-launcher, test-management-isolation, test-management-disposal-proof).
- PostgreSQL-only tests: src/django_core_micha/billing/tests/test_object_checkout_concurrency.py (~29) and the other billing skips; src/django_core_micha/notifications/tests/test_notification_feed_indexes.py; tests/test_registration_extension.py (~420).
- CI: .github/workflows/ci.yml (job test); .github/workflows/publish.yml (pytest gate).
- Reference: workflow-templates/.github/workflows/app-ci.yml (PostgreSQL service ~249-256); Gustav/backend/tests/test_database_driver.py; tests.py stack_* keys (workspace .claude/scripts/tests.py docstring).
- New: a Compose file for the test database at the repo root.

## Preamble

> **You are the dispatched implementer of this work order, not its Orchestrator. Rules in `AGENTS.md`
> and in skills that address the Orchestrator, a Codex parent or the Expertenchat (delegation, dispatch,
> review, register, landing) do not apply to you. You never run `codex` or `codex exec`, never write to
> git, and never edit the register or `.claude/codex-status.md`.**
>
> **Your first tool action is the hook canary: run the shell command `codex-hook-canary-ws-gov-60`
> exactly (it is a command string, not a file). The expected result is a denial,
> `WS-GOV-60 canary: workspace hooks are active`; continue after it. If it is not denied, report
> that once as unverified hook coverage and continue.**
>
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
> **Running your tests in the sandbox (WS-GOV-110, measured).** Django-core / Django app pytest: use the
> repo's own interpreter, `<repo>\.venv-test\Scripts\python.exe -m pytest -p no:cacheprovider <file>`; the bare
> `python` has no `django_core_micha`. Vitest works only when your writable root is that repo
> (`node node_modules/vitest/vitest.mjs run <file>`; with another root it fails with EPERM on
> `node_modules\.vite-temp`). Workspace gate suites: `python -I -B <test file>`. Never redirect a cache to
> make a tool work and never start a database or Docker. A test run that cannot work in the sandbox is NOT
> a blocker: when the code is complete and only the run is missing, end `RESULT: DONE` and add the line
> `TESTS NOT RUN: <reason>` (known reasons: needs PostgreSQL/Docker/WSL; SWC native addon refuses any
> sandbox cache folder, so Vitest on an SWC-plugin frontend such as jg-ferien cannot start). Use
> `RESULT: BLOCKED` only when the code itself cannot be completed.
>
> **Files.** Read a file over ~400 lines by locating with `Grep -n` and reading only the needed
> range — never the whole file, never the same range twice without a change in between. Write new code
> into cohesive modules named by behaviour: a new source or test file stays under ~400 lines; code
> added to a file already over ~800 lines goes into a new module unless it is a small change to
> existing logic. Name test files by the behaviour they cover, never by the work-order ID.
>
> Narrate continuously: a `PLAN: <step1> | <step2> | …` line up front, then a single-line
> `PROGRESS: [<n>/<total>] <present-tense action>` before every relevant action (and `… done` on
> completion), spaced so no gap exceeds ~2 min, stdout unbuffered, plus exactly one final
> `RESULT: DONE|BLOCKED <reason>`.

## C. Orchestrator only

> **If you are the implementer reading this work order as your own specification: STOP at this line.
> Everything below describes what the Orchestrator does AFTER you finish. You do none of it — no
> reviewers, no verification run, no register edit, no commit.** You ARE the implementation
> instance described below; do NOT launch or dispatch another implementer.

Tier 3 in a shared core: configured implementer, full independent review (envelope, regression, duplication, tests, sec_review for the CI and Compose part). Run the suite through `tests.py` against the new test database; the evidence lists the six formerly skipped tests as passed. Prove the CI change on a pull request run or `workflow_dispatch` of `ci.yml` (free as a test workflow). A push to `main` that touches only tests, settings, `project.yaml`, Compose and workflows does not publish (`publish.yml` path filter: `src`, `pyproject.toml`; a `pyproject.toml` change without a version bump publishes nothing). Carried findings go to `work-orders/carried.md` of this repo.

## Gate record

Gate #1 pending operator approval (plan agreed 2026-11-25; local database: repo-owned test database).
