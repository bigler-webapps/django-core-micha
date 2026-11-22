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
