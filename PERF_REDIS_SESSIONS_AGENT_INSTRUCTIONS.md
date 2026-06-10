# Agent Instructions — Redis-backed sessions in `settings_base`

**Repo:** `django-core-micha` · **Trunk:** `main` (no `develop` — commit directly to `main`, NO feature branches)
**Tier:** 1 · Operator approved this scope on 2026-06-10.

> Supersedes "Change 3" in `PERF_USERS_CURRENT_AGENT_INSTRUCTIONS.md` — that section was
> written into the doc but never implemented (v2.18.2 shipped only Changes 1+2).

## Problem

No `CACHES` is configured and no `SESSION_ENGINE` override exists in
`src/django_core_micha/settings/settings_base.py` → every authenticated request in every
consumer app pays one session SELECT against Postgres. All consumer apps already
hard-depend on Redis: the Channels layer (~line 170) uses
`env("REDIS_HOST", default="redis")` — so this adds no new infrastructure.

## Change

In `src/django_core_micha/settings/settings_base.py`, near the channel-layer config,
reusing the same `REDIS_HOST` env var:

```python
# Session reads go to Redis (db 1 — db 0 belongs to the Channels layer);
# writes still hit Postgres, so a Redis flush only costs cache misses, never logouts.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": f"redis://{env('REDIS_HOST', default='redis')}:6379/1",
    }
}
SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"
```

Rules:
- Redis **db 1**, not db 0 (Channels pubsub uses db 0).
- `cached_db`, NOT `cache`: sessions must survive a Redis restart/flush.
- The lib's own test settings (`tests/settings.py`) must keep tests green — if the test
  run has no Redis available, override `CACHES` to `LocMemCache` there. Note that
  `cached_db` with LocMemCache is functionally correct (DB is the source of truth), so
  the SESSION_ENGINE override may stay global.

## Tests

- Full suite green: `pytest tests/ -q`
- Add one test asserting `SESSION_ENGINE == "django.contrib.sessions.backends.cached_db"`
  and that `CACHES["default"]["BACKEND"]` is set (guards against accidental removal).

## Finalize

1. Bump `pyproject.toml` version: minor bump (new consumer-visible behavior) →
   next free minor, e.g. `2.19.0` (check the current version first — other work may
   have moved it past 2.18.2).
2. CHANGELOG entry with a **Migration** note: consumer apps need no code change, but
   their Redis instance will start serving session reads on db 1; apps that pin Redis
   `maxmemory` should account for session keys.
3. Standard finalize per workspace `AGENTS.md`: reviewer pass, review summary +
   commit message, explicit operator approval, commit to `main`, push.

## Orientation (before touching code)

`git status` (clean tree expected; this file may be untracked — include it in the
commit), `git fetch`, `git branch --show-current` == `main`, `git pull --ff-only`.
