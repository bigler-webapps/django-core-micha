# DCM-DX-18 · Local test-management proxy answers a full page burst

## A. Envelope

### Goal & expected outcome

- Goal: the isolated local test-management proxy answers every request when a browser loads a large single-page app, so login flows in acceptance runs do not fail on refused asset requests.
- Expected outcome:
  - a burst of 120 simultaneous connections through the proxy is accepted and answered, none refused;
  - the per-request cost of the proxy drops, so a page with about 45 chunks loads within the runner's normal timeouts;
  - the cause of the refusals left after DCM-DX-17 is measured and recorded;
  - a patch release that the global `run-dev` installation picks up;
  - hram's F2 (HRAM-CI-26) no longer fails on refused or failed asset requests.

### Why

- DCM-DX-17 (3.41.3) raised the listen backlog from 5 to 128. The hram session measured on 2026-11-22:
  - 10 and 30 parallel requests are now all answered (before: 1 and 21 refused);
  - 60 parallel: 31 refused (`ECONNREFUSED`); 120 parallel: 64 refused;
  - hram F2 fails on every runner run at the first sign-in.
- A backlog of 128 alone should not refuse 60 connections. DCM-DX-17 named this risk: the per-request handling may be the bottleneck.
- Per request, `src/django_core_micha/test_management/local_proxy.py:105-113` runs the following, so a page with about 45 chunks starts about 180 processes:
  - `consumer()` and `validate_compose_file()`;
  - three `docker inspect` calls (`_runtime_snapshot`, line 42);
  - one `docker compose exec` of the relay.
- The proxy runs with the interpreter of the `run-dev` process (`scripts/isolated_test_start.py:547`), that is the global `run-dev` installation, not the consumer app's pin. Which version the measurement ran with is not recorded.

### Existing capabilities

- `QuietServer` (`local_proxy.py:33`) and the burst test from DCM-DX-17 (`tests/test_test_management_proxy_backlog.py`).
- The monitor thread (`local_proxy.py:138`) already re-checks every 10 s that the backend runs.

### Scope

1. **Measure first and record in the Completion:**
   - the dcm version the global `run-dev` runs;
   - whether refusals at 60 and 120 connections come from the accept side (backlog, accept rate) or from the handling;
   - the time per proxied request split into validation and relay.
2. **Lower the per-request validation cost.**
   - The runtime and compose-file validation result may be reused for a short fixed time (at most 2 s), shared across request threads.
   - A failed validation is never reused; the request gets 502 as today, and the next request validates again.
   - The checks themselves stay unchanged.
3. **Remove the remaining refusals** so the burst outcome holds. Allowed means, chosen by the measurement:
   - accept-side tuning;
   - a bounded number of concurrent relays, with waiting requests held instead of refused;
   - reuse of the computed compose environment.
4. **Unchanged:**
   - bind address `127.0.0.1` with an ephemeral port;
   - the `READY <port>` line;
   - the quiet `handle_error` and `log_message`, so no request data, cookies or headers reach any output;
   - the path allow list, the size limit and the 150 s relay timeout;
   - the monitor shutdown;
   - the isolation guarantees of `isolated_test_start.py`.
5. **Patch release** with the version synced across `pyproject.toml` and the changelog, following the repo's publish-from-main flow. The changelog names that the global `run-dev` installation must be updated to take effect.

### Non-goals

- No persistent or multiplexed relay process; a single `docker compose exec` per request stays.
- No change to `isolated_test_start.py` startup, migration or diagnostics.
- No retry of refused connections in the workspace acceptance runner.
- No pin bumps in consumer apps. hram's F2 rerun belongs to HRAM-CI-26.
- No change to the workspace runner's sign-in error reporting.

### Tier

- Tier 3: a change inside shared-core (`django-core-micha`) that touches the isolation checks of the test-management proxy.

### Risks

- Reusing a validation result for up to 2 s leaves a short window in which a replaced or changed container could receive a request before the next check. The time bound, the unchanged checks and the monitor limit it; the security lens must judge it.
- Holding waiting requests could hide a stuck relay. The existing 150 s relay timeout still ends each request.
- The measured refusals could depend on Windows behaviour that tests on CI do not reproduce. The Completion records the local measurement next to the CI test result.

### Required tests to WRITE

- **Burst:** with `QuietServer` bound to `127.0.0.1:0` and a handler that takes about 200 ms per request, 120 simultaneous connections are all accepted and answered with 200.
- **Validation reuse:**
  - many requests within the reuse time call the runtime validation once;
  - a request after the reuse time validates again;
  - a failed validation is not reused, the request gets 502, and the next request validates again.
- **Concurrent relays**, if bounded:
  - no more than the bound run at once;
  - waiting requests are answered, not refused.
- **Unchanged behaviour:**
  - only `127.0.0.1` is bound;
  - `handle_error` and `log_message` write nothing;
  - a path outside the allow list still gets 404.

## Gate record

Gate #1 approved by the operator 2026-11-22 (AskUserQuestion: "Freigeben + Übergabetext").
