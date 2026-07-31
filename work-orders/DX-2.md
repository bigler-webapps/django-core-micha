# DX-2 — CI has never collected the messaging tests

Status: planned · Tier 1 · **Approval-gated: changes what CI executes** (AGENTS.md → CI/CD) · Target repo: `django-core-micha` (main)
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

To be filled by the Orchestrator session on `git pull`, within the envelope above.
