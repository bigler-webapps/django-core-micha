# DX-2 — CI has never collected the messaging tests

Status: done · **Tier 2** · **Approval-gated: changes what CI executes** (AGENTS.md → CI/CD) · Target repo: `django-core-micha` (main)
*Reclassified 2026-08-01: labelled Tier 1 at authoring, but it touches CI — a binding Tier-2 surface
per AGENTS.md's Tiering gate — in a shared-core repo. The independent review did run and was clean.*
Release: **no version bump, no publish** — test configuration only, nothing in the package changes.

---

## Part A — Envelope (Expertenchat, 2026-07-31)

### Goal

Make the promotion gate actually cover the `messaging` subpackage, and make it impossible for the next
subpackage to fall out of the gate silently.

### The finding

`pyproject.toml:39`:

```
testpaths = ["tests", "src/django_core_micha/notifications/tests", "src/django_core_micha/onboarding/tests"]
```

`testpaths` is an **allowlist**, and it was never extended when the `messaging` subpackage was added in
MSG-2. CI runs a bare `pytest -q` (`.github/workflows/…:104`). Measured on `main` at 2.37.0:

- bare `pytest --collect-only -q` → **401 tests, zero from messaging**
- `pytest --collect-only -q src/django_core_micha/messaging/tests` → **58 tests**

There are exactly three `tests` directories under `src/`; the newest one is the one missing.

**Consequences.** The promotion gate has never covered messaging — MSG-2, MSG-2b and MSG-2c all passed
a CI that did not know their tests existed. Every "messaging suite green" figure in those WOs came from
a local run with an explicit path; the tests are real and they pass, they are simply not anchored to
anything. It also explains cleanly why Codex wrote no tests in MSG-2c: an implementer who runs `pytest`
and sees no messaging tests reasonably concludes none are expected. That was a configuration hole, not
disobedience.

### Expected outcome

1. `src/django_core_micha/messaging/tests` added to `testpaths`.
2. **A guard test** so this cannot recur silently: it discovers every `src/django_core_micha/*/tests`
   directory on disk, reads `testpaths` from `pyproject.toml`, and fails naming any directory that is
   not covered. It must live in `tests/` (always collected) and must read the real `pyproject.toml`
   rather than a copy, so it tracks the file that actually configures CI.
3. The full suite green with messaging included — this is the substance of the WO, not a formality.
   Adding 58 previously-unrun tests to a shared session can surface real cross-module interference
   (fixture scope, registry pollution between `messaging` and `notifications`, database state). If it
   goes red, **that is the finding** and it goes back to the operator rather than being silenced by
   narrowing the path again.

### Non-goals / do-not-touch

Any change to a messaging or notifications test's content in order to make the combined run pass —
if the combined run fails, report it, do not adjust the tests to fit. No other CI workflow change. No
new dependency (`tomllib` is stdlib on the project's Python). No change to `python_files`,
`DJANGO_SETTINGS_MODULE` or any other pytest option.

### Required tests to WRITE

- The guard test itself (outcome 2), which must **fail** if any `src/**/tests` directory is absent from
  `testpaths` — verify that by temporarily reasoning through the negative case, not only the positive.

### Verification

- `pytest --collect-only -q` collects the messaging tests (count rises by 58 from 401 to 459).
- Full bare `pytest -q` green.
- The guard test passes with the corrected `testpaths` and would fail without it.

### Risks

- **The combined run may go red**, per outcome 3. That is the reason this is a deliberate WO rather
  than a drive-by one-liner.
- The guard test hard-codes an assumption about layout (`src/django_core_micha/*/tests`). Keep it
  narrow and readable; a clever generic implementation that silently matches nothing is worse than none.

### Preconditions

Operator approval to change what CI executes — **granted 2026-07-31, explicitly, with the instruction
to pull this forward ahead of MSG-3b's completion.**

### Measured during scoping (2026-07-31) — information, not a substitute for this WO's own verification

The two questions this WO exists to answer were probed while scoping it, on `main` at 2.37.0:

- **Collection:** adding the path takes the bare run from **401 to 461** collected tests (+58 messaging,
  +2 for the guard).
- **Does the combined run hold?** A full bare `pytest -q` with messaging included came back
  **461 passed** in ~90 s — no cross-module interference from running `messaging` and `notifications`
  in one session.
- **Would the guard have caught this?** Re-running its logic against the pre-fix `testpaths` list
  reports `src/django_core_micha/messaging/tests` as uncovered, i.e. it fails on the configuration
  that shipped. A coverage guard that cannot go red is worthless, so this is the important half.

Treat these as de-risking, not as done work: re-run and re-confirm as part of the WO. In particular the
green combined run was measured locally — CI is the run that matters, and it has never executed these
tests before.

### Execution directive

Implement through `codex exec` in the background — invoked **directly via Bash** (never the
`debugger`/`*_coder` Agent wrappers) with **both** flags `--skip-git-repo-check` and
`--dangerously-bypass-approvals-and-sandbox`, prompt passed as a positional argument from a file;
fall back to direct Claude implementation only on Codex quota / rate-limit / non-zero exit.

**Note for the implementer:** the `testpaths` hole is why a plain `pytest` shows no messaging tests.
Until this WO lands, run messaging tests by passing the path explicitly.

### Mini-handover (pastable)

Orchestrator: implement `work-orders/DX-2.md` in `django-core-micha` (main). `git pull` first, read the
WO, then follow `orchestrate-codex` (Codex-first, own independent review, commit on green). Small WO —
one `testpaths` line plus a guard test — but the verification (full bare suite green with messaging
included) is the substance, not a formality. No version bump, no publish.

---

## Part B — Implementation map (Orchestrator)

### Execution directive (place first when generating the Codex prompt)

> Implement through `codex exec` in the background — invoked directly via Bash (never the
> `debugger`/`*_coder` Agent wrappers) with BOTH flags `--skip-git-repo-check` and
> `--dangerously-bypass-approvals-and-sandbox`, prompt passed as a positional argument from a file.
> Fall back to direct Claude implementation only on Codex quota / rate-limit / non-zero exit.

### Target repo working directory (absolute)

`C:\Users\biglmi\Documents\webapps\django-core-micha` (repo root). Never `…\webapps`.

### Context package

**Named files to change:**

1. **`pyproject.toml`** — `[tool.pytest.ini_options]` block, currently (line ~39):
   ```
   testpaths = ["tests", "src/django_core_micha/notifications/tests", "src/django_core_micha/onboarding/tests"]
   ```
   Add `"src/django_core_micha/messaging/tests"` to the list. Do not touch `python_files`,
   `DJANGO_SETTINGS_MODULE`, `asyncio_mode`, or anything else in this block or file.

2. **New guard test — `tests/test_testpaths_coverage.py`** (repo-root `tests/` — this directory is
   always in `testpaths`, so the guard test itself is always collected regardless of the bug it
   guards against). Read `pyproject.toml` with `tomllib` (stdlib, `import tomllib`; the runtime here
   is 3.13/3.14 — confirmed by `.github/workflows/publish.yml`'s `python-version: "3.14.5"` — `tomllib`
   needs no dependency add despite the package's own `requires-python = ">=3.10"` floor, since this
   test never ships to a consumer, it only runs in this repo's own CI/dev environment). Shape:
   - A small pure function, e.g. `_uncovered_test_dirs(testpaths: list[str], repo_root: Path) -> list[str]`,
     that: globs `repo_root.glob("src/django_core_micha/*/tests")` for directories (a real directory
     named `tests` directly under a subpackage — do not overreach into a smarter/recursive glob, the
     WO explicitly warns against a "clever generic implementation that silently matches nothing"),
     converts each match to a POSIX-style repo-relative string (`src/django_core_micha/<name>/tests`,
     matching the exact string form already used in `testpaths`), and returns the ones **not** present
     verbatim in `testpaths`.
   - **Test 1 (the actual guard):** load the real `pyproject.toml` from the repo root (locate it via
     `Path(__file__).resolve().parents[1] / "pyproject.toml"` — this file lives at `tests/`, one level
     below root), parse `tool.pytest.ini_options.testpaths` with `tomllib`, call the helper, and
     `assert not uncovered, f"..." ` naming any offending directories in the failure message.
   - **Test 2 (the "would it have caught this" case, per Required Tests — do not skip):** call the same
     helper directly with a **hardcoded** stale list reproducing the exact bug this WO fixes —
     `["tests", "src/django_core_micha/notifications/tests", "src/django_core_micha/onboarding/tests"]`
     (i.e. the list literally taken from the WO's "The finding" section, pre-fix) — and assert the
     helper reports `src/django_core_micha/messaging/tests` as uncovered. This is what proves the guard
     is not vacuous; do not implement Test 1 alone.
   - Use `repo_root = Path(__file__).resolve().parents[1]` for both tests (don't re-derive it
     differently in each), so both walk the same real `src/django_core_micha/*/tests` set on disk —
     only the `testpaths` list fed to the helper changes between the two tests.

### Invariants / do-not-touch / pitfalls

- **Do not touch messaging or notifications test content** to make the newly-included combined run
  pass. If enabling `messaging/tests` alongside the rest of the suite surfaces real interference
  (shared registry state — `register_messaging_app`/`register_messaging_policy` teardown, fixture
  scope, DB state), that is the finding this WO exists to surface — stop and report it, do not patch
  around it by narrowing `testpaths` back or editing an existing test's assertions.
- **No dependency change**, no other `pyproject.toml` edit beyond the one `testpaths` line, no CI
  workflow file change (the workflow already runs bare `pytest -q`; nothing there needs to change once
  `testpaths` is correct).
- The guard test path must be `tests/test_testpaths_coverage.py` or equivalent directly under the
  root `tests/` dir — not under any subpackage's `tests/`, or it collects nowhere near universally
  enough to guard the thing it's guarding.
- **Verification is the substance of this WO, not a formality** (Part A, outcome 3): after the change,
  actually run the full bare `pytest -q` (or `python -m pytest -q` with `PYTHONPATH` unset — mirror
  however CI invokes it, no explicit path argument) from the repo root and confirm it is green with the
  messaging tests now included, not just that the guard test and messaging tests pass in isolation.

### Required tests to WRITE (Codex writes them; the ORCHESTRATOR runs them)

Exactly the two guard-test cases described above (`tests/test_testpaths_coverage.py`). No other new
test content — this WO does not add product-code tests, only the CI-coverage guard.

### Preamble (append verbatim to the Codex prompt)

> The text above is the COMPLETE spec — the committed WO file's content, not a plan to refine; there
> is no separate plan file. Read the nearest `AGENTS.md`, the relevant `.codex/skills/<role>/SKILL.md`,
> and the app `MEMORY.md` ONLY for conventions. Stay in scope; do not touch auth/permissions/deps/schema/CI
> unless the spec says so (this WO explicitly authorizes the one `testpaths` line — nothing else in CI).
> Do not update `MEMORY.md`. Do NOT `git add`/`commit`/`push` — leave every change uncommitted in the
> working tree for the orchestrator's independent review. WRITE the tests the `Required tests` section
> calls for AND **RUN the tests you just wrote** to confirm they execute and pass — that is the ONLY
> test run you do (NOT the app's affected/full suite, NOT any review). The orchestrator re-runs the
> authoritative full bare `pytest -q` + does the independent review after you finish — those are the
> gate; your own run does not count as the gate.
>
> Narrate continuously: a `PLAN: <step1> | <step2> | …` line up front, then a single-line
> `PROGRESS: [<n>/<total>] <present-tense action>` before every relevant action (and `… done` on
> completion), spaced so no gap exceeds ~2 min, stdout unbuffered, plus exactly one final
> `RESULT: DONE|BLOCKED <reason>`.
