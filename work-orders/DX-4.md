> **Self-address guard:** if you are the implementer reading this work order as your own
> specification, Part C is not addressed to you — it tells the Orchestrator how to invoke you; you
> ARE that invocation. Do NOT shell out to `codex exec`.

# DX-4 — `run-dev` warns when a generated artefact has outlived its source

Status: planned · Tier: 2 · Target repo: django-core-micha (develop) · Datum: 2026-08-20 · Estate-wide developer tooling

# A. Envelope — authored by the Expertenchat

## Two incidents, one shape

Both found on 2026-08-19, in hram, hours apart:

**`.env` had outlived `project.yaml`.** hram's local `BRIDGE_RSYNC_INPUT_TARGET` pointed at
`100.102.64.114` — main-prod. `project.yaml` had said `100.125.116.91` (research-prod) since INF-16
moved the bridge. Every local→sciCORE optimisation campaign had been pushing its input bundle to the
wrong host for days: the push *succeeded* (main-prod has the same directory layout), the agent pulled
from research-prod and found nothing, and the error surfaced two systems away as
`rsync input pull failed (exit 23)`. Four hypotheses died before the config line was read. `sync-secrets`
fixed it in one run, because `generate_env` writes the file wholesale.

**`.venv` had outlived `requirements.txt`.** 24 `django-core-micha` minor versions behind the pin
(2.19.0 installed against 2.43.0 pinned), which is why `notifications`, `messaging` and `activity`
did not exist locally at all. A session lost an afternoon to packages that were pinned and absent.

**The shape is identical: a generated artefact on the developer machine that survives a change to
its source, with nothing announcing the gap.** Every deployed environment re-applies its source at
every deploy. The developer machine is the only place where the generated file simply persists —
and it is the place with no re-application step.

Neither incident was a lapse of care. Both were invisible by construction.

## Goal / expected outcome

`run-dev` says, at startup, when the local `.env` disagrees with `project.yaml`, or when the local
virtualenv disagrees with `requirements.txt`. A warning with the specific keys and packages, not a
generic nag — and never a block.

## Scope

1. **`.env` vs `project.yaml`.** Compare the keys `generate_env` derives from `project.yaml` — the
   `app_env` block, the environment's `env_overrides`, and the platform-computed keys — against what
   the local `.env` actually holds. Report differing values by key name. **Never print values**: some
   of those keys sit next to secrets in the same file, and a diff that echoes them turns a warning
   into a leak.

2. **Virtualenv vs `requirements.txt`.** Compare installed distributions against the pinned
   constraints, and report entries that are missing or that violate their pin. Name the package and
   both versions.

3. **Warn, never block, and make the fix one line.** Say `generate-env --env local` for the first
   case and the project's sync command for the second. A developer who is deliberately running an
   older venv must be able to ignore it.

4. **Silence when there is nothing to say.** A check that prints on every start gets filtered out by
   the reader within a week, and then the one time it matters it is invisible too. No output on a
   clean start.

5. **Cheap enough to run unconditionally.** A comparison of parsed metadata, not a network call and
   not a resolver run. If it cannot be made fast, gate it behind a flag and say so rather than
   slowing every start.

## Explicit non-goals / do not touch

- **Do not auto-fix.** Regenerating `.env` or installing packages during startup is a state change
  the developer did not ask for, and `.env` in particular is a file agents must not write. Report and
  name the command.
- **Do not read secret values** and do not include any value in the output (scope item 1).
- **Do not extend this to deployed environments.** They re-apply their source at deploy; the gap is
  specific to the developer machine, and a check there would be noise.
- Do not touch `generate_env`'s own logic, `sync_secrets`, or the resolution order between
  `secrets` / `app_env` / `env_overrides`.
- Do not make either check fatal, and do not add a `--strict` mode "for CI" — CI does not use
  `run-dev`.

## Tier · precondition / gate

**Tier 2.** New logic in a shared-core script that every app on the estate starts through. Not Tier 3:
it reads, it prints, it changes no state and touches no sensitive surface. But it is estate-wide, so a
crash in the check would break `run-dev` for twenty repos — which is the risk that earns the tier.

No precondition. Independent of hram's `DEP-4`, which this would have made visible earlier.

## Risks

- **A check that breaks the thing it checks.** If parsing a malformed `project.yaml` or an exotic venv
  raises, `run-dev` must still start. Wrap both checks so any failure degrades to silence or a
  one-line "check skipped", never to a traceback.
- **False positives train people to ignore it.** `generate_env` computes some keys itself and
  explicitly refuses to let `app_env` override them (`DJANGO_ALLOWED_HOSTS`, `CSRF_TRUSTED_URLS`,
  `TRAEFIK_ROUTER_RULE`, `PUBLIC_ORIGIN`, `MASTER_BASE_URL`, volume names; `DEBUG` is the guarded
  exception). Comparing those naively reports a difference that is *correct by design*. Establish
  which keys are legitimately comparable before comparing them.
- **Twenty repos, one script.** Any app whose `project.yaml` or venv layout differs slightly must not
  see spurious warnings. Test against more than one app's shape.
- **Leaking a value through a diff.** Scope item 1's constraint is the mitigation; a reviewer should
  check it specifically.

## Required tests to WRITE

1. **A drifted `.env` is detected**, and the report names the keys and **contains no value** — assert
   on the absence of the values, not only on the presence of the key names. This is the test that
   protects against the leak.
2. **A drifted venv is detected**, for both cases: a package missing entirely, and a package present
   but below its pin.
3. **A clean setup produces no output** (scope item 4).
4. **Platform-computed keys do not produce a false positive** (see Risks) — assert that a `.env`
   generated by `generate_env` itself reports clean.
5. **A malformed `project.yaml` and an unreadable venv both leave `run-dev` starting** — the check
   degrades, the command does not fail.

# B. Implementation map — filled by the Orchestrator — ADDRESSED TO THE IMPLEMENTER

> **PLACEHOLDER — not yet written.** The Orchestrator fills this on `git pull` with the context
> package (`src/django_core_micha/scripts/run_dev.py`, `generate_env.py` — in particular the
> `app_env` / `env_overrides` precedence comment and the platform-computed `add()` keys that cannot be
> overridden — the requirements-parsing approach, and at least two apps' `project.yaml` for shape),
> the absolute target working directory, the progress contract, and the preamble. **Do not dispatch
> while this placeholder stands.**

# C. Orchestrator only — NOT ADDRESSED TO THE IMPLEMENTER

> **If you are the implementer reading this work order as your own specification: STOP at this line.**
> Everything below describes what the Orchestrator does AFTER you finish. You do none of it — no
> reviewers, no verification run, no register edit, no commit.

## Execution directive

Check `.claude/codex-status.md` first; Codex-first if available, otherwise implement directly and name
the dated evidence. cwd = repo root.

## Review routing

Tier 2 with new logic → independent `reviewer`. `reviewer`'s named question: **can any value from
`.env` reach the output, on any path including the error paths?** No `ui_reviewer`, no `sec_reviewer` —
but the value-leak question above is the one a security reviewer would ask, so it is asked here
instead of routing one.

## Verification

The five new tests plus the existing `run_dev` and `generate_env` tests. Then run `run-dev` against at
least two real app repos — one clean, one deliberately drifted — and confirm the clean one is silent.

## Register + commit

`WORK_ORDERS.md` row to `done` with the SHA, the named `reviewer` verdict, and the list of keys
classified as comparable versus platform-computed (see Risks) — that classification is the part a
later reader will need. Note which apps it was exercised against. `develop`/`main` per this repo's
convention. **A dcm release then propagates it; that is the separate publish step, not part of this
order.**

## Pastable mini-handover

`Orchestrator: implement work-orders/DX-4.md in django-core-micha. git pull first, read the WO + src/django_core_micha/scripts/run_dev.py + generate_env.py (especially the app_env/env_overrides precedence comment and the platform-computed keys that CANNOT be overridden), then follow orchestrate-codex (own reviewer, scoped tests, commit on green). Tier 2 — new logic in a script every app on the estate starts through; a crash in the check would break run-dev for twenty repos. WHY: two incidents on 2026-08-19, same shape — hram's .env still pointed BRIDGE_RSYNC_INPUT_TARGET at main-prod days after INF-16 moved the bridge to research-prod (every local sciCORE campaign pushed to the wrong host; the push SUCCEEDED and the failure surfaced two systems away), and hram's .venv was 24 dcm minors stale so notifications/messaging/activity did not exist locally at all. Both are generated artefacts on the developer machine that outlived their source with nothing announcing it; deployed envs re-apply theirs at every deploy. BUILD: warn at run-dev start when .env disagrees with project.yaml, and when the venv disagrees with requirements.txt. NEVER PRINT VALUES — those keys share a file with secrets; name keys only, and test for the ABSENCE of values. Warn, never block; name the one-line fix. SILENT on a clean start. Beware false positives: generate_env computes DJANGO_ALLOWED_HOSTS, CSRF_TRUSTED_URLS, TRAEFIK_ROUTER_RULE, PUBLIC_ORIGIN, MASTER_BASE_URL and volume names itself and refuses app_env overrides, so comparing those naively reports a by-design difference — establish the comparable set first. Any failure in the check degrades to silence, never a traceback. Exercise against two real app repos.`
