# DCM-DX-7 — run-dev must not print `.env` variable names

## A. Envelope — everyone

Goal: `run-dev` output never contains the names (or values) of variables from an app's `.env`.
Observed 2026-11-16 in `kerzenziehen/KZ-FIX-18`: the Codex Orchestrator's `run-dev` call printed
warnings that named `.env` variables. No values were shown. The workspace rule (`AGENTS.md`) forbids
the agent from learning `.env` contents, names included; a tool the agent is told to use must not
leak them. The likely source is Docker Compose's "variable is not set" warnings passed through
unfiltered; the cause is verified first.

Expected outcome: `run-dev` filters or redacts such warnings (and any other line that would name an
`.env` variable) before printing, keeping a neutral hint ("N compose variables are not set; see the
app's secrets.yaml") so a missing secret is still noticed. Exit codes and the remaining log output
are unchanged.

Scope: `src/django_core_micha/scripts/run_dev.py` and its tests.

Non-goals: no change to how `.env` is generated or read by the stack; no change to compose files;
no change to `sync-secrets`.

Tier 3: a change inside shared core (`django-core-micha`), released as a patch version.

Risks: over-filtering could hide a useful startup error — only lines that name a variable from the
environment file are redacted, and the redaction keeps the line's kind.

Tests to write: a fixture compose warning naming a variable is printed redacted with the neutral
hint; a normal error line passes unchanged; the exit code of a failing compose call is preserved.

## Gate record

Gate #1 approved by the operator in chat 2026-11-16 (workspace Expertenchat session
local_5b5543f0-e394-5ed1-b027-1de7bb41556e: "Ja, bitte so anlegen").

## B. Implementation map — implementer

### Context package

Target: `C:/Users/Micha Bigler/Documents/webapps/django-core-micha`.
Start in the workspace root. Edit only `src/django_core_micha/scripts/run_dev.py`
and `tests/test_run_dev.py`; DCM-DX-6 also modifies the former, so preserve its
current changes. Do not edit this WO or the register.

- `run_dev.py::run_command` passes subprocess output through unfiltered. Direct
  `docker-compose logs` calls and `stream_docker_logs` are additional output paths.
  Preserve streaming, failures, and subprocess exit codes while suppressing
  variable names and values from Compose diagnostics.
- `run_dev.py::main` prints `collect_drift_warnings()` directly. Static source
  inspection verified that `drift_check.py::check_env_drift` constructs warning
  strings containing variable names; suppress or replace these messages at the
  `run_dev.py` boundary without changing the drift checker or reading `.env`.
- Preserve ordinary non-sensitive output. Use a neutral count/hint for redacted
  Compose and drift diagnostics, including `secrets.yaml`; do not expose names or
  values. Do not print raw subprocess exceptions if they could contain secrets.
- Write focused tests with synthetic strings and fake subprocesses only. Do not
  read, create, or modify any `.env` file, including test fixtures. Cover Compose
  missing-variable warnings, drift warnings, unchanged normal error output, and
  nonzero exit propagation; cover streaming and direct log paths as applicable.

### Implementer preamble

> This committed WO and its Envelope are the complete specification. Read nearest
> governance and the relevant skill for conventions. You are the implementer, not
> the Orchestrator. Work only in the named source and test files; other agents are
> active, so preserve their existing changes. Do not read or edit `.env`, update
> the register or MEMORY, install dependencies, edit lockfiles, run package
> managers, spawn implementers, review your own work, or run git add/commit/push.
> Run only tests you wrote, once; leave final tests and independent review to the
> Orchestrator. Start with PLAN, emit PROGRESS with no gap over about two minutes,
> and finish with exactly one RESULT: DONE or RESULT: BLOCKED line.

## C. Orchestrator only

> If you are an implementer reading this work order as your specification: stop here. Everything
> below is the Orchestrator's.

Review per Tier 3. Release as a patch version per the dcm release flow; consumers pick it up with
their next pin bump.
