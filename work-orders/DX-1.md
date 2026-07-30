# WORK ORDER DX-1 (django-core-micha) — make parallel test runs survivable: session-unique test DB + the `timeout` trap

**EXECUTION DIRECTIVE.** Implement through `codex exec` in the background — invoked **directly via
Bash** (never the `debugger`/`*_coder` Agent wrappers) with **both** flags `--skip-git-repo-check`
and `--dangerously-bypass-approvals-and-sandbox`, prompt passed as a positional argument from this
file. Fall back to direct Claude implementation only on Codex quota / rate-limit / non-zero exit.

Feature-level WO (Expertenchat envelope). Verified against dcm `main` and jg `develop` on 2026-07-30.

## TIER
Tier 2 — the code change lands in **shared base settings** consumed by every app, so per AGENTS.md
"Test scope" this is critical-tier: the blast-radius suite across affected apps applies, not just dcm's
own. Independent `reviewer` mandatory. Low urgency; it blocks nothing.

## WHY — a real incident, and the proposed fix only addresses half of it
During the NOTIF-22 session (2026-07-30) a killed test run kept running for 26 minutes, held the test
database, and drove Postgres to 92%. It cost two wrong diagnoses before the cause was found: a test was
reported as "pre-existing broken" (it was not — it passes on a clean DB), and the interference was
initially attributed to a parallel session (it was not).

**Two distinct problems produced that. Do not conflate them.**

**(1) The actual cause — `timeout` does not kill the container.** `timeout … docker compose run …`
kills only the *client*. The container keeps running, keeps its connections, and keeps the test
database locked. Every symptom above followed from this. This is an operational gotcha, not a design
flaw, and the fix is documentation plus a correct command pattern.

**(2) The amplifier — one shared test database per app.** Neither dcm's `settings/settings_base.py`
(`DATABASES`, `:149`) nor jg's `backend/backend/test_settings.py` sets a `TEST` key, so Django derives
the default `test_<NAME>` — for jg, `test_jg-dg`. Two sessions running tests in the same repo therefore
contend for one database. Without (1) this is merely occasional; with (1) it turns a stuck client into
a cross-session outage.

## GOAL
Make a second concurrent test run in the same repo harmless, and make the `timeout` trap impossible to
walk into unknowingly.

## SCOPE

**A. Opt-in session-unique test database (dcm, the code change).** In
`src/django_core_micha/settings/settings_base.py`, derive `DATABASES["default"]["TEST"]["NAME"]` from
an environment variable (e.g. a suffix appended to the default name). **When the variable is unset,
the resulting name must be byte-identical to today's** — CI, every app's pipeline and every existing
local workflow must be unaffected. Prove that equality in a test, do not assert it in prose.

**B. Document the command pattern (approval-gated — propose, do not self-apply).** The correct
invocation and the `timeout` trap belong in the per-app testing docs (jg's `CLAUDE.md` "Commands"
section is the concrete instance). **`CLAUDE.md` is part of the governance ruleset under AGENTS.md**,
so prepare the exact wording and obtain explicit operator approval before editing. Cover: `timeout`
kills the client only; how to stop the container; and how to set the variable from A when a second
session is active.

## DO NOT TOUCH
- The default test-database name when the variable is unset. This is the whole safety property.
- Production/staging `DATABASES` behaviour, connection pooling, `TEST_DB_HOST` handling in app
  `test_settings.py`.
- pytest configuration in any app, `pytest.ini` `testpaths`, fixtures.
- Any app repo's code. Scope A is dcm-only; scope B is a documentation proposal.
- CI workflow files — if CI needs the variable, report it rather than changing pipelines here.

## RISKS
- **Silent CI breakage** is the one real risk: a changed default test-DB name would hit every app at
  once, and dcm publishes to PyPI, so it reaches them on their next pin bump rather than immediately —
  a delayed, confusing failure. Hence the byte-identical-default requirement.
- Over-engineering: pytest-xdist-style worker isolation is **not** wanted here. One opt-in variable.
- Fixing (2) while leaving (1) undocumented would leave the actual incident cause in place.

## REQUIRED TESTS / ACCEPTANCE
- with the variable unset, the resolved test-DB name is identical to the current one — asserted in a
  test, for the exact shape apps rely on;
- with it set, two differing values yield two distinct database names;
- dcm's own suite passes; then the blast-radius run per AGENTS.md "Test scope" (shared core), i.e. the
  affected apps' suites, since this touches settings every app imports;
- scope B delivered as a proposed diff plus the operator's explicit approval recorded — not silently
  applied.

## TARGET REPO / WORKING DIRECTORY
`C:\Users\biglmi\Documents\webapps\django-core-micha`. Never the workspace root.
Platform repo: commit directly to `main`. No feature branches. This is a publishable change — follow
publish-from-main and the registry live-check before any app pin bump. **No app pin bumps in this WO.**

## PROGRESS CONTRACT
Emit a `PLAN: <step1> | <step2> | …` line up front, then a single-line
`PROGRESS: [<n>/<total>] <present-tense action>` **before every relevant action** (file opened, file
edited, command/test run) and `PROGRESS: [<n>/<total>] done` on step completion, spaced so no gap
exceeds ~2 min. stdout unbuffered. Exactly one final `RESULT: DONE|BLOCKED <reason>`.

## MINI-HANDOVER (paste into a fresh Orchestrator session)
```
Orchestrator: implement work-orders/DX-1.md in django-core-micha (main). git pull first, read the WO.
Two halves: scope A is a shared-settings change whose default must stay byte-identical (critical-tier
test scope applies), scope B is an approval-gated CLAUDE.md documentation proposal — prepare it, do not
apply it. Then follow orchestrate-codex (Codex-first, own independent review, commit on green).
```
