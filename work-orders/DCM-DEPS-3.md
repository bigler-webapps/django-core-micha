# DCM-DEPS-3 — django-core-micha declares only what every consumer needs

Part of the estate dependency reduction decided 2026-10-15 (operator + Expertenchat). This order
and `ui-core-micha` `UCM-DEP-2` land and release first; every app then has its own order that bumps
to these releases (`<TOKEN>-DEP(S)-<n>` in each app repo, e.g. `SPX-DEP-6`, `JG-DEPS-6`).

# A. Envelope

## Goal & expected outcome

- Ziel: a consumer that does not use an optional dcm feature no longer installs that feature's
  third-party packages, and the estate runs on one Postgres driver.
- Expected outcome: a new **major** release of `django-core-micha` whose `[project].dependencies`
  hold only what is needed on every app start; optional features are behind named extras; the
  CHANGELOG entry carries a consumer migration table (extra -> dcm features that need it -> packages
  it brings) that the app orders read to pick their extras.

## Scope

1. **Feature extras.** Move out of `[project].dependencies` into `[project.optional-dependencies]`:
   - `ai`: `openai`, `anthropic` (used by `extraction/drivers/*` and `assistant/turns.py`, both lazy
     imports today);
   - `extraction`: `pypdf`, `Pillow` (`extraction/input_prep.py`; `messaging/attachments.py` imports
     PIL lazily);
   - `push`: `pywebpush` (`notifications/delivery.py`, already import-guarded);
   - `scripts`: `PyYAML` (only the `[project.scripts]` entry points: `generate-env`, `sync-secrets`,
     `run-dev`, `provision-app`, `provision-server-access`).
   Keep `test` as is. If the audit's file list above turns out incomplete, the rule is: a package
   moves to an extra exactly when no module reached on app start (CORE_APPS app configs and their
   `ready()`, models, `api_urls`, `settings_base`) imports it at module level.
2. **Boot without extras.** An app with none of the extras installed starts and serves every
   CORE_APPS endpoint. Any module-level import of a moved package that IS reached on app start
   becomes lazy. Specifically verify `extraction/input_prep.py:8` (top-level `PIL` import) is not on
   the start path; if it is, make it lazy.
3. **Clear failure when a feature is used without its extra.** Calling an `ai`- or
   `extraction`-gated feature without the extra raises an error whose message names the missing
   extra (`django-core-micha[ai]` etc.). `pywebpush` keeps today's behaviour (push delivery disabled
   when the package is absent) — no new error there.
4. **One Postgres driver.** Declare `psycopg[binary]` (psycopg 3) and remove `psycopg2-binary`. No
   `pool` extra. dcm's own test environment runs on psycopg 3.
5. **Declare `redis`.** `settings_base` configures `django.core.cache.backends.redis` and
   `SESSION_ENGINE=cached_db`, so `redis` is a core runtime dependency; declare it in
   `[project].dependencies`.
6. **Release** as the next major version with the CHANGELOG migration table from the expected
   outcome, including the driver switch and the `redis` declaration.

## Non-goals / do-not-touch

- `cryptography`, `filetype`, `channels`, `channels_redis`, `whitenoise`, `django-cors-headers`,
  `django-environ`, `djangorestframework`, `django-allauth[mfa,socialaccount]` stay core.
- `django-anymail` stays. (The audit claim that `settings.MAILERS` is ignored came from a Django 6.0
  test venv; dcm requires Django >= 6.1. Not this order's subject.)
- No allauth change: the `[headless]` extra and the unconditional `allauth.headless.urls` include
  are untouched. No new version floors/caps for packages that stay.
- No change in any consumer repo — each app has its own order.
- No change to the test venv drift, the repo-root `tests` package layout, or the stray `src;C`
  directory.

## Tier · precondition / gate

- Tier 3: a change inside shared-core that removes dependencies consumers use today, plus the
  Postgres driver switch every consumer inherits on its next bump.
- Precondition: none. Ordering: released before any app order of this reduction starts.

## Risks

- A moved package is imported at module level on a start path the audit missed: every consumer
  without the extra fails at boot. Covered by the boot-without-extras test below.
- psycopg 3 behaves differently from psycopg2 in edge cases (adaptation of custom types,
  server-side cursors, timezone handling); dcm's own suite is the first place this shows.
- Consumers that rely on `openai`/`anthropic`/`pypdf`/`Pillow`/`pywebpush`/`PyYAML` arriving through
  dcm lose them on the bump unless they request the extra or pin the package themselves; the
  migration table is what prevents that.

## Required tests to WRITE

- Boot without extras: with `openai`, `anthropic`, `pypdf`, `PIL`, `pywebpush` and `yaml` made
  unimportable (import blocker in `sys.meta_path` for the test), importing every CORE_APPS app
  config, the models, `django_core_micha.api_urls` and `settings_base` succeeds.
- Feature without extra: one `ai`-gated and one `extraction`-gated entry point each raise an error
  whose message names the extra.
- Driver: under the test settings, `django.db.connection` uses psycopg 3 (the backend's `Database`
  module is `psycopg`).
