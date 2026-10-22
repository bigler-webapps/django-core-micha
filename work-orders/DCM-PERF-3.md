# DCM-PERF-3 — Hashed Vite assets are cached for only 60 seconds

# A. Envelope — authored by the Expertenchat

## Goal & expected outcome

- Goal: content-hashed frontend assets served through dcm's static-file setup are cacheable for a
  long time by browsers and by the Cloudflare edge, so a returning user and the edge stop asking the
  origin in Germany about files that can never change.
- Expected outcome: in a non-local environment, a request for a Vite-built, content-hashed asset
  (for example `static/index-jQGc1USQ.js`, `static/apiClient-CJL0hVEu.css`,
  `static/dm-sans-latin-400-normal-CW0RaeGs.woff2`) is answered with
  `Cache-Control: public, max-age=31536000, immutable` (or WhiteNoise's equivalent immutable
  header). Every other static file keeps today's short lifetime.

## Background (measured 2026-10-21 from Tanzania)

- Non-local `STORAGES` in `src/django_core_micha/settings/settings_base.py` use
  `whitenoise.storage.CompressedManifestStaticFilesStorage`. WhiteNoise's default immutable test
  only recognises Django-manifest hashes (`name.<12 hex>.ext`), so Vite outputs
  (`name-<8 chars>.ext`) are served with WhiteNoise's default `max-age=60`.
- On imara.tz (hram) the Cloudflare edge in Johannesburg answered HIT at 20 s and 50 s and
  REVALIDATED at 75 s for the same asset: after one minute every asset request costs a round trip
  to the origin in Germany (about 200-275 ms from Tanzania). Browsers receive Cloudflare's default
  4 h and then revalidate every chunk of a page (up to 73 on hram's teaching pages).
- survey_app hosts (poll.imara.tz, survey.imara.tz) show the same headers.

## Scope + non-goals

- In scope: dcm's static-file configuration so that content-hashed frontend build outputs are
  recognised as immutable; anything that recognition needs in dcm.
- Non-goals / do not touch:
  - un-hashed files (`index.html`, favicons, `robots.txt`, files a consumer serves under a fixed
    name) must never be marked immutable;
  - the local-development branch (`IS_LOCAL`) stays as it is;
  - Django's own manifest-hashed files keep working as today;
  - no Cloudflare or Terraform change (operator decision 2026-10-21: the fix lives at the origin);
  - no consumer pin bumps — each app adopts this through its own dcm bump row.

## Tier · precondition / gate

- Tier 3: a change inside shared-core (dcm).
- Precondition: none. Consumers benefit only after their own dcm pin bump (hram is on 3.0.0,
  survey_app on 3.3.0; those bumps are registered separately).

## Risks

- A name-pattern test can match an un-hashed file whose name happens to end in an 8-character
  segment (for example `select2-dropdown.css`). Marked immutable, such a file would be cached for a
  year and a later edit would never reach users. The decision whether a file is hashed must not be
  fooled by such names.
- Consumers that copy the Vite build into `STATIC_ROOT` differently (output directory, asset
  naming) must still be recognised, or at least must fall back to today's behaviour, never to a
  wrong immutable header.
- After deploy, Cloudflare's own Browser Cache TTL setting decides what browsers see when it is
  longer than the origin's value; with a one-year origin value the origin's value should win.

## Required tests to WRITE (you write them and run YOUR OWN new ones; the Orchestrator's run is the gate)

- Hashed Vite outputs are recognised as immutable: at least `index-jQGc1USQ.js`,
  `apiClient-CJL0hVEu.css`, `dm-sans-latin-400-normal-CW0RaeGs.woff2`, and a name whose hash
  contains `-` or `_` (for example `apiClient-BFiUcIo-.js`).
- Un-hashed files are not: `index.html`, `favicon.svg`, `robots.txt`, and the near-miss
  `select2-dropdown.css`.
- Django manifest-hashed names stay immutable as before.
- The local-development settings branch is unchanged.

# B. Implementation map — filled by the Orchestrator — ADDRESSED TO THE IMPLEMENTER

## Target working directory (absolute)

`C:\Users\Micha Bigler\Documents\webapps\django-core-micha` (git root, branch `main`). Use absolute
paths. The checkout is shared: `src/django_core_micha/scripts/sync_secrets.py` and
`tests/test_sync_secrets.py` carry another session's uncommitted edits — do not open, format or
revert them, and preserve any other file you did not create.

## Context package

**The seam is a WhiteNoise setting, not a middleware subclass.** WhiteNoise (installed 6.12) calls
`immutable_file_test(path, url)` once per file when the middleware starts; when
`WHITENOISE_IMMUTABLE_FILE_TEST` is set to a callable, that callable REPLACES the middleware's
default method entirely (`whitenoise/base.py:61-66`), so it must also keep the Django-manifest
behaviour. Most consumers (hram, survey_app, cockpit, maana, ...) declare their own `MIDDLEWARE`
list naming `"whitenoise.middleware.WhiteNoiseMiddleware"` literally, so a dcm middleware subclass
would never reach them; a setting exported by `settings_base` reaches every consumer that does not
set `WHITENOISE_IMMUTABLE_FILE_TEST` itself (none does, measured 2026-10-22).

**WhiteNoise's default test** (`whitenoise/middleware.py:164-200`): URL below `static_prefix`,
`get_name_without_hash` strips the Django-manifest `.<hash>` segment, `get_static_url` asks
`staticfiles_storage.url(name)` and the URL counts as immutable only when the manifest maps the
un-hashed name back to exactly this file. `static_prefix` is computed in
`WhiteNoiseMiddleware.__init__` (`middleware.py:90-100`: `WHITENOISE_STATIC_PREFIX`, else the path of
`STATIC_URL`, minus `FORCE_SCRIPT_NAME`, with leading and trailing slash). Reuse that logic (for
example by calling WhiteNoise's own methods); do not write a second Django-manifest regex.

**Where Vite output lives (measured 2026-10-22, all 18 consumers).** Every frontend builds with
`outDir: 'build'`, `assetsDir: 'static'`, Rollup's default names `<name>-<hash>.<ext>` (hash: 8
characters of `A-Z a-z 0-9 _ -`), and the backend sets `STATIC_ROOT = <build>/static`
(`FRONTEND_BUILD_DIR`). So every Vite output sits DIRECTLY in `STATIC_ROOT` (URL `/static/<file>`),
while Django app files live in namespaced subdirectories (`admin/...`, `rest_framework/...`).
Un-hashed files also reach that top level: Photogallery's `frontend/public/static/` ships
`pdf-template-boho-abstract.png`, `logo512-maskable-v2.png`, `apple-touch-icon.png`, `sw.js`, ...;
font files are often named like `OpenSans-SemiBold.ttf`. hram's local build (`localHostBuild`)
emits un-hashed `static/[name].js` — that is the local branch and must stay non-immutable anyway.

**Decision rule (the Orchestrator's mapping of Part A's risks — implement exactly this):** a URL is
a hashed build asset only when ALL hold:
1. it lies directly below the static prefix (no further `/` after it) — files in subdirectories
   never qualify through this rule (they keep the Django-manifest rule);
2. the file name is `<stem>-<h>.<ext>` where `<h>` is exactly the 8 characters before the LAST
   extension, consists only of `A-Z a-z 0-9 _ -`, and is preceded by a `-` (so
   `apiClient-BFiUcIo-.js` qualifies with `h = BFiUcIo-`);
3. `<h>` contains at least one uppercase AND at least one lowercase letter, and is NOT a run of
   capitalised words (letters only, fully matching `(?:[A-Z]?[a-z]+)+`, e.g. `SemiBold`).
A file is immutable when the Django-manifest test OR this rule says so. Expected effect (simulated
over 200 000 random hashes): about 5.5 % of genuine Vite hashes fail rule 3 and keep today's
60 s — the safe direction; no word-like name passes.

**Files to change**

- New `src/django_core_micha/settings/_static_files.py` — the callable
  `immutable_file_test(path, url)` and its helpers. It is imported while settings load: no import of
  models or the app registry at module level; import `whitenoise`/`django.contrib.staticfiles`
  lazily inside the functions. Pattern for a settings helper module:
  `src/django_core_micha/settings/_email_config.py`.
- `src/django_core_micha/settings/settings_base.py:296-304` — the non-local (`else`) branch of the
  `STORAGES` block sets `WHITENOISE_IMMUTABLE_FILE_TEST` to that callable. The `if IS_LOCAL:` branch
  (`:284-295`) stays byte-identical. No other line of `settings_base.py` changes.
- New `tests/test_static_immutable.py`.

**Required tests (Part A) — how to make them pin the behaviour**

- Name cases: the four hashed names from Part A are immutable at `/static/<name>`; `index.html`,
  `favicon.svg`, `robots.txt`, `select2-dropdown.css`, `OpenSans-SemiBold.ttf`,
  `pdf-template-boho-abstract.png` and `webfont-20261021.woff2` are not; a hashed-looking name in a
  subdirectory (`/static/vendor/index-jQGc1USQ.js`) is not; a URL outside the static prefix is not.
- Django manifest: with `STORAGES["staticfiles"]` set to `ManifestStaticFilesStorage` and a
  `tmp_path` `STATIC_ROOT` holding a `staticfiles.json` manifest
  (`{"version": "1.1", "paths": {"css/app.css": "css/app.0123456789ab.css"}}`), the hashed URL
  `/static/css/app.0123456789ab.css` stays immutable and `/static/css/app.css` does not.
- End to end through WhiteNoise: with `override_settings(STATIC_ROOT=tmp_path, STATIC_URL="/static/",
  WHITENOISE_IMMUTABLE_FILE_TEST=<the callable>)`, build `whitenoise.middleware.WhiteNoiseMiddleware`
  and request a hashed and an un-hashed file through it (Django `RequestFactory`); assert the exact
  `Cache-Control` values (WhiteNoise's immutable header vs. `max-age=60, public`).
- Settings branches: import `settings_base` in a subprocess the way
  `tests/test_settings_base_dependencies.py:23-92` does (a consumer settings module doing
  `from django_core_micha.settings.settings_base import *`): with `ENV_TYPE=local` the setting is
  absent and `STORAGES`/`WHITENOISE_AUTOREFRESH`/`WHITENOISE_USE_FINDERS` are unchanged; with
  `ENV_TYPE=staging` and `DJANGO_SECRET_KEY` set, `WHITENOISE_IMMUTABLE_FILE_TEST` is the dcm callable.
  Do not import `DEPRECATED_EMAIL_SETTINGS` in these new subprocesses (the local Django here is
  6.0.5; that name exists only from 6.1).

Run only the tests you wrote, from the target working directory:
`python -m pytest tests/test_static_immutable.py -q -p no:cacheprovider`

## Invariants and pitfalls

- Never mark a file immutable that the rule above does not accept; when in doubt, return `False`.
- The local branch and its behaviour are untouched; `WHITENOISE_MAX_AGE` is not set.
- No change outside the three files named above; no `CHANGELOG.md`, version, docs or register edit.
- Files are UTF-8: open them with explicit `encoding="utf-8"`.

## Preamble

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
> Narrate continuously: a `PLAN: <step1> | <step2> | …` line up front, then a single-line
> `PROGRESS: [<n>/<total>] <present-tense action>` before every relevant action (and `… done` on
> completion), spaced so no gap exceeds ~2 min, stdout unbuffered, plus exactly one final
> `RESULT: DONE|BLOCKED <reason>`.

Work from this package; do not explore broadly from scratch; open only the named files to verify.

# C. Orchestrator only — NOT ADDRESSED TO THE IMPLEMENTER

> **If you are the implementer reading this work order as your own specification: STOP at this line.
> Everything below describes what the Orchestrator does AFTER you finish. You do none of it — no
> reviewers, no verification run, no register edit, no commit.** You ARE the implementation
> instance described below; do NOT launch or dispatch another implementer.

### Execution directive

Configured `implementation`: `codex/gpt-6-luna`, CLI route from the Claude Orchestrator
(`codex exec -C <target dir> --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox -m
gpt-6-luna -c model_reasoning_effort=high -`, WO on stdin). Launch evidence: one dispatch
2026-10-22, banner read back (`model: gpt-6-luna`, `reasoning effort: high`, workdir the target
directory, session `01a161f6-6c6c-7a93-aa1c-ec62f65706dd`), exit 0 with `RESULT: DONE`; no commit,
staging or register edit by the implementer, foreign `sync_secrets` edits untouched.

### Review routing

Tier 3: all `review.lenses` (envelope, regression, duplication, tests) plus `sec_review`, one
concurrent batch, `codex exec --sandbox read-only -m gpt-6-luna`, diff inline; `review_fallback` only
if Codex is unavailable. No `ui_review` (no frontend in the diff).

### Verification

`tests/test_static_immutable.py` and `tests/test_settings_base_dependencies.py` (its first test
fails locally only because the laptop's Django 6.0.5 lacks `DEPRECATED_EMAIL_SETTINGS`; CI installs
6.1); release as a patch (no new public API).

Recorded from the requirements round (2026-10-21): Recorded from the requirements round (2026-10-21): acceptance is
a before/after header check on a consumer's staging host once that consumer has bumped dcm, recorded
in the register Notiz; no fixed budget.
