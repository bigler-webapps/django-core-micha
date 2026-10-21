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

Placeholder. Not yet mapped: context package, target directory and preamble are missing, so this
WO must not be dispatched in its current state.

# C. Orchestrator only — NOT ADDRESSED TO THE IMPLEMENTER

> **If you are the implementer reading this work order as your own specification: STOP at this line.**
> Everything below describes what the Orchestrator does AFTER you finish.

To be filled by the Orchestrator. Recorded from the requirements round (2026-10-21): acceptance is
a before/after header check on a consumer's staging host once that consumer has bumped dcm, recorded
in the register Notiz; no fixed budget.
