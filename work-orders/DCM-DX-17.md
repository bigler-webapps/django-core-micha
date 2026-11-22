# DCM-DX-17 · Local test-management proxy accepts bursts of parallel connections

## A. Envelope

### Goal & expected outcome

- Goal: the isolated local test-management proxy accepts the parallel connections a browser opens when it loads an app, instead of refusing part of them.
- Expected outcome:
  - a burst of at least 50 simultaneous connections to the proxy is accepted and answered, none refused;
  - a patch release of `django-core-micha` that consumers can pin;
  - hram's acceptance criterion F2 (HRAM-CI-26, central `password_recovery` scenario) no longer fails on refused asset requests.

### Why

- `src/django_core_micha/test_management/local_proxy.py:33`: `QuietServer(ThreadingHTTPServer)` sets no `request_queue_size`, so the listen backlog is the `socketserver.TCPServer` default of 5.
- The hram session measured against the proxy on 2026-11-22: of 10 parallel connections 1 was refused, of 30, 21 were refused (`ECONNREFUSED`). hram's SPA loads about 45 chunks at once. The runner aborts the refused requests and the login form never renders. A manual browser login on the same instance works.
- The same cause probably explains the intermittent BCO-CI-1 F1 failures. That link is not proven.

### Scope

1. Give `QuietServer` a listen backlog large enough for a browser's parallel asset loading (128).
2. Keep all other proxy behaviour unchanged: the bind address `127.0.0.1` with an ephemeral port, the `READY <port>` line, the quiet `handle_error` that keeps request data and cookies out of logs, daemon threads, and the monitor shutdown.
3. Patch release with the version synced across `pyproject.toml`, the changelog and the tag, following the repo's publish flow.

### Non-goals

- No change to the proxy's request handling, validation, isolation checks or relay logic.
- No connection or thread limit beyond the backlog, and no keep-alive or HTTP/2 work.
- No pin bump in consumer apps here. hram's pin bump and its F2 rerun belong to HRAM-CI-26.

### Tier

- Tier 3: a change inside shared-core (`django-core-micha`).
- Tests: the new proxy test plus the existing test-management isolation and launcher tests.

### Risks

- A larger backlog alone may not be enough if the per-request handling is the bottleneck. The burst test must show that every connection gets a response, not only that it is accepted.

### Required tests to WRITE

- With the proxy server class bound to `127.0.0.1:0` and a trivial handler that returns 200, open at least 50 connections at the same time. Each one is accepted and gets the 200 response.
- The server class still binds only to `127.0.0.1`, and its error handler still writes nothing for a failing request.

## Gate record

Gate #1 approved by the operator 2026-11-22 (AskUserQuestion: "Freigeben + Übergabetext").

---

# B. Implementation map — ADDRESSED TO THE IMPLEMENTER

## Context package

- **Named file to change:** `C:/Users/Micha Bigler/Documents/webapps/django-core-micha/src/django_core_micha/test_management/local_proxy.py`, class `QuietServer` (line 33). Add the class attribute `request_queue_size = 128` next to `daemon_threads = True`. Nothing else in that file changes.
- **New test file (name by behaviour):** `C:/Users/Micha Bigler/Documents/webapps/django-core-micha/tests/test_test_management_proxy_backlog.py`. It imports `QuietServer` from `django_core_micha.test_management.local_proxy`, binds it to `("127.0.0.1", 0)` with a trivial `BaseHTTPRequestHandler` subclass answering 200, serves in a daemon thread, opens at least 50 connections at the same time (connect all sockets first, then send one GET on each, then read every response), and asserts all 50 got 200. A second test asserts the bound address is `127.0.0.1` and that `QuietServer.handle_error(None, None)` returns None and writes nothing to stdout/stderr (use `capsys`).
- **Invariants:** keep bind address, `READY <port>` line, quiet `handle_error`, daemon threads and monitor shutdown unchanged. Existing tests `tests/test_test_management_*.py` must stay green.
- **Do not touch:** `scripts/isolated_test_start.py`, `tests/test_test_management_launcher.py` (foreign uncommitted work), `pyproject.toml`, `CHANGELOG.md` (the Orchestrator does the release).

## Target repo working directory (absolute)

`C:/Users/Micha Bigler/Documents/webapps/django-core-micha`

## Preamble

> **You are the dispatched implementer of this work order, not its Orchestrator. Rules in `AGENTS.md` and in skills that address the Orchestrator, a Codex parent or the Expertenchat (delegation, dispatch, review, register, landing) do not apply to you. You never run `codex` or `codex exec`, never write to git, and never edit the register or `.claude/codex-status.md`.**
>
> **Your first tool action is the hook canary: run the shell command `codex-hook-canary-ws-gov-60` exactly. The expected result is a denial, `WS-GOV-60 canary: workspace hooks are active`; continue after it. If it is not denied, report that once as unverified hook coverage and continue.**
>
> The text above is the COMPLETE spec. Stay in scope; do not touch auth/permissions/deps/schema/CI; do not update `MEMORY.md`. Do NOT edit `WORK_ORDERS.md`. Your tools are for editing source and test files and for running the tests you wrote. Do NOT install dependencies, touch a lockfile, or run a package manager. Do NOT `git add`/`commit`/`push`. WRITE the required tests AND RUN the tests you wrote (only that run): `C:\Users\Micha Bigler\Documents\webapps\django-core-micha\.venv-test\Scripts\python.exe -m pytest -p no:cacheprovider tests/test_test_management_proxy_backlog.py`. If the run cannot work in the sandbox end `RESULT: DONE` plus `TESTS NOT RUN: <reason>`. Name test files by behaviour.
>
> Narrate: a `PLAN: ...` line, then `PROGRESS: [n/total] ...` before each action, and exactly one final `RESULT: DONE|BLOCKED <reason>`.

---

# C. Orchestrator only — NOT ADDRESSED TO THE IMPLEMENTER

> **If you are the implementer reading this work order as your own specification: STOP at this line.**

## Execution directive

Configured implementer via `run-worker.py --role implementation --writes`. Release: bump `pyproject.toml` 3.41.2 -> 3.41.3, add CHANGELOG entry, push to `main` (publish-from-main, no tags in this repo).

## Review routing

Tier 3: `review-lenses.py` round 1 (all lenses incl. security), own Envelope pass.

## Verification

`tests.py --repo django-core-micha` scoped to test-management tests.

## Register + commit

Own register line only, via `land.py` with explicit paths.

## Completion

- Implemented by codex/gpt-6.1-terra (medium); diff: `request_queue_size = 128` on `QuietServer`, new `tests/test_test_management_proxy_backlog.py`; release 3.41.3 (pyproject + CHANGELOG, publish-from-main, no tags in this repo).
- Review: two rounds, five lenses. Round 1 raised a defect (burst test could pass with backlog 5; fixed: all 50 connections are opened before the server serves, plus a backlog assertion) and a nit (loopback assertion only checks the test's own bind; carried).
- Tests: on a clean copy of HEAD plus these changes, all test-management test files: 161 passed, 1 skipped; the burst test fails with backlog 5. In the shared working tree `tests.py` scopes test-management-isolation (12 failures, pgvector) and launcher (39 failures) fail because of the uncommitted DCM-DX-15 edits in `isolated_test_start.py` and the launcher test; the same files pass on clean HEAD. Not caused by this change.
- Landing commit includes the foreign one-line register status change of DCM-DX-15 (planned -> in-progress), unavoidable because `land.py` stages the whole register file.
