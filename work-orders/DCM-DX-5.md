> **Self-address guard:** if you are the implementer reading this work order as your own
> specification, Part C is not addressed to you — it tells the Orchestrator how to invoke you; you
> ARE that invocation. Do NOT shell out to `codex exec`.

# DCM-DX-5 — `run-dev` becomes predictable for agent sessions and stops paying avoidable build cost

Status: planned · Tier: 3 · Target repo: django-core-micha (main) · Datum: 2026-08-27 · Estate-wide developer tooling

# A. Envelope — authored by the Expertenchat

## The reported symptom

Operator report 2026-08-27: `git commit`/`git push` and the staging build are consistently clean,
but local `run-dev` "funktioniert nicht immer sauber" — and agent sessions in particular "brechen ab,
warten Stunden, obwohl es nur einige Minuten geht".

Three distinct causes were established by reading `run_dev.py` and hram's `Dockerfile` +
`docker-compose.local.yml`, not inferred:

1. **No readiness signal.** `docker compose up -d` returns when containers are *created*. No
   `backend` service on the estate declares a healthcheck (checked: hram has them for `java_backend`
   and `redis` only; `survey_app` and `spesix` have none at all). There is therefore nothing an agent
   can wait on. `--no-log-stream` returns immediately into an app that is not yet serving; every other
   mode blocks forever by design. Both readings end the same way — the agent guesses.
2. **`--build` is far more expensive than it needs to be.** It sets `UV_FLAGS=--refresh`, which
   deliberately defeats the `--mount=type=cache,target=/root/.cache/uv` in the Dockerfile. Staging
   builds with the default `ARG UV_FLAGS=""` and hits that cache in full. This is the single largest
   contributor to the local/staging gap.
3. **Frontend dependency drift is silent.** `ensure_frontend_node_modules` installs only when
   `node_modules` is *absent*. A changed `package.json`/`pnpm-lock.yaml` is never picked up, so the
   host-side build — which is what the container actually serves via `FRONTEND_BUILD_DIR` — runs
   against stale dependencies with no error. This is a silent-wrong-result class, not a crash.

## Goal / expected outcome

`run-dev` gives a machine-readable completion signal in its detached mode, no longer bills every
`--build` for a cache-defeating dependency refresh, and stops serving a build made against stale
frontend dependencies. Interactive operator use is unchanged except for being faster.

## Scope

Six changes, all in `src/django_core_micha/scripts/run_dev.py` unless stated.

1. **Decouple the uv refresh from `--build`.** `--build` no longer sets `UV_FLAGS=--refresh`; it
   leaves `UV_FLAGS` empty, matching the Dockerfile's default `ARG`. A new **`--refresh-deps`** flag
   sets `UV_FLAGS=--refresh` and implies `--build`. The existing `--refresh` flag keeps exactly its
   current meaning — the `argparse.SUPPRESS`-ed deprecated alias for `--build`, with its current
   warning — and is **not** repurposed. Silently flipping the meaning of a flag that already exists
   is the worse option; the two names stay distinct.

2. **Install frontend dependencies on lockfile drift.** `ensure_frontend_node_modules` additionally
   runs `pnpm install` when `pnpm-lock.yaml` (or `package.json`) is newer than the installed-state
   marker (`node_modules/.modules.yaml`). Operator decision 2026-08-27: install, do not merely warn —
   the existing behaviour already installs unasked when `node_modules` is missing, and a warning does
   not stop the stale build from being served.

3. **Readiness gate in the detached path.** When `--no-log-stream` is active, poll the backend until
   it answers over HTTP, with a hard timeout, then emit exactly one final line:
   `READY <url>` or `TIMEOUT after <n>s`. Operator decision 2026-08-27: automatic in
   `--no-log-stream` only — no new flag, and the blocking log-stream modes are untouched, so no
   existing interactive invocation changes behaviour. This gives agent sessions the same
   parse-one-line contract that `AGENTS.md` already relies on for Codex (`RESULT: DONE|BLOCKED`).

   **Invariant: resolve the port via `docker compose port backend 8000`, never by reading `.env`.**
   `WEB_PORT` lives in `.env`; asking Compose keeps the check away from that file entirely.

4. **Condition the traefik removal.** `docker rm -f traefik` currently runs unconditionally, twice in
   a row (the second call is redundant), from whichever app repo you started in. Remove the container
   only when it belongs to this invocation's own Compose project; leave a foreign traefik — in
   practice `webapp-management`'s — alone. Note the blast radius is smaller than it first appears:
   `generate_env` sets `TRAEFIK_ENABLE=false` and `USE_EXTERNAL_PROXY=false` for local, so local app
   stacks do not route through traefik at all. This is an annoyance for a parallel
   `webapp-management` stack, not a local outage.

5. **Unbuffered output plus a watch-loop heartbeat.** `print(..., flush=True)` on the script's own
   output, and a periodic status line from the frontend watch loop. `AGENTS.md` instructs agents to
   treat output silence beyond ~5 minutes as a suspected stall; today a long `vite build` produces
   exactly that silence, so the agent is following the rule correctly and still reaching the wrong
   conclusion.

6. **Correct the `--watch` wording.** Its help text must say it runs a full `vite build` per change
   and is not HMR (`--vite` is HMR). Reword `[WARN] --watch has no effect together with
   --no-log-stream`: that combination already does the right thing — one atomic host build, then
   detached — but the wording reads as a usage error.

**Design constraint carried over from `DX-3`:** put each decision in a small pure function
(`compute_active_local_profiles` is the precedent) so it is unit-testable without Docker. Every
existing test in `tests/test_run_dev.py` is a pure-function test; keep that property.

## Explicit non-goals / do not touch

- **`--renew-anon-volumes` stays exactly as it is.** Operator decision 2026-08-27. Its comment names
  a real staleness bug ("stale frontend static/templates artifacts") whose circumstances are recorded
  nowhere — the line predates the `c103a5f` extraction from `webapp-management`, as do the traefik
  and `UV_FLAGS` lines. Trading an unmeasured time saving against a bug class we cannot reconstruct
  is a bad deal. Do not couple it to `--build`, do not remove it.
- **No compose-baseline changes.** Adding a `backend` healthcheck is the right companion fix but
  touches 15 app repos and is registered separately. This WO's readiness gate must work without one.
- **Do not skip the Dockerfile's frontend build stage locally**, even though it is very probably
  redundant against the host build. It has not been measured; measuring it is a separate task.
- Do not touch the `--vite`/HMR path, `drift_check.py`, `generate_env.py`, or any app repo.
- No new runtime dependencies. Use the standard library for the readiness poll.
- Do not read, create or modify `.env` (`AGENTS.md` Core Behaviour) — see the invariant in scope 3.

## Tier · gate

**Tier 3.** A change inside shared-core (`django-core-micha`), which `AGENTS.md` → Tiering lists
explicitly under Tier 3. Note for the record: the two nearest predecessors touching this same file,
`DX-3` and `DX-4`, are registered as Tier 2 under the rule as it then stood; the shared-core entry
has since moved to Tier 3 and the current canon applies.

The blast radius is what earns it either way: one script, **15 app repos** start through it
(`Kira`, `bigler-consult`, `cockpit`, `fitness-monitor`, `hpc-bridge`, `hram`, `innoservice`,
`jg-ferien`, `kerzenziehen`, `musiknoten`, `reimbursements`, `spesix`, `survey_app`,
`survey_contact_app`, `webapp-template`). A crash in any of these code paths breaks local
development estate-wide.

## Risks

1. **A dropped refresh could mask a moved pin.** `--build` no longer refreshing means a dependency
   whose *contents* changed without its *specifier* changing is not re-resolved. Locally this is
   narrow: hram's engine resolves from the mounted source checkout via `PYTHONPATH=/app/engine_src`,
   not from the git pin (`INSTALL_ENGINE=0`). `--refresh-deps` covers the remaining cases and must be
   named in the help text. Related: the estate has already been bitten by unpinned dependencies
   backtracking silently, so the flag needs to be discoverable, not just present.
2. **`pnpm install` at an unexpected moment.** Auto-install adds minutes to a start the operator
   expected to be quick. It must announce itself on its own line before starting, and must not run
   when nothing drifted. A wrong marker file would make it run on *every* start — the failure mode to
   test for.
3. **A readiness poll that reports READY too early or never.** Too early and the agent gets a
   connection error anyway; never and it has replaced a silent hang with a slower one. The timeout is
   a hard bound and the `TIMEOUT` line must still be emitted — leaving containers running, not
   tearing them down.
4. **Windows.** Every subprocess call in this script runs with `shell=True` on win32, and the local
   compose forces `WATCHFILES_FORCE_POLLING=true` because inotify does not cross the WSL2/Hyper-V
   boundary. Anything added here must work under those conditions; the operator's machine is Windows.
5. **Misidentifying traefik's owner** would either restore the current over-broad removal or leave a
   genuinely stale own-project container behind.

## Required tests to WRITE

Narrow and pure-function, in `tests/test_run_dev.py`, no Docker:

1. **`UV_FLAGS` resolution**: `--build` alone → empty; `--refresh-deps` → `--refresh` and `--build`
   implied; the deprecated `--refresh` → still behaves as `--build` (empty `UV_FLAGS`), proving the
   meaning was not flipped.
2. **pnpm drift detection**: lockfile newer than the marker → install; marker newer → no install;
   `node_modules` absent → install (the existing behaviour, unbroken). The "no install" case is the
   one that catches risk 2.
3. **Readiness outcome and its exact output line**: a successful poll yields `READY <url>`, an
   exhausted one yields `TIMEOUT after <n>s`. Assert the literal line shape — agents parse it, so the
   format is the contract.
4. **traefik removal decision**: own-project container → remove; foreign container → keep; no
   container → no-op.
5. **Regression**: the 7 existing tests in `tests/test_run_dev.py` stay green, and
   `tests/test_drift_check.py` is untouched and green.

# B. Implementation map — filled by the Orchestrator — ADDRESSED TO THE IMPLEMENTER

*(placeholder — not yet filled. Per `AGENTS.md` → Work Order, this WO must NOT be dispatched to an
implementer while this section still carries the placeholder and no preamble block is present.)*

# C. Orchestrator only — NOT ADDRESSED TO THE IMPLEMENTER

*(placeholder — filled by the Orchestrator together with Part B.)*
