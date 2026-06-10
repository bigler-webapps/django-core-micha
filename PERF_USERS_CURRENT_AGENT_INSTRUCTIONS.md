# Agent Instructions — Fix query explosion in `/api/users/current/` (BaseUserSerializer)

**Repo:** `django-core-micha` · **Trunk:** `main` (no `develop` branch — commit directly to `main`, NO feature branches)
**Tier:** 1 (small bounded change) · **Touches auth logic** — operator has already approved this scope on 2026-06-10.

## Problem

`GET /api/users/current/` (served by `BaseUserViewSet.current` → `BaseUserSerializer`) fires
~10+ identical DB queries per request **for admin users**, because:

1. **`_admin_policy_satisfied()` re-fetches the auth policy on every call.**
   It is reached from each of the 8 `ui_permissions` helpers, plus `can_manage_support_agents`,
   plus `security_state` — all within ONE serialization. Call chain:

   ```
   _admin_policy_satisfied → is_user_security_sufficient
     → get_required_security_level_for_user → get_required_auth_factor_count_for_user
       → get_policy_state(None) → get_or_create_auth_policy()   ← 1 DB query, EVERY time
   ```

   Non-admin users short-circuit at `is_subject_to_admin_auth_policy()` (in-memory) — the
   explosion only hits admins, but admins are exactly who uses the app most.

2. **`get_user_security_state()` runs 3 separate `Authenticator` queries**
   (`.exists()` for TOTP, WEBAUTHN, RECOVERY_CODES) where one suffices.

On a remote DB (prod), each roundtrip costs tens of ms → the endpoint blocks the entire
frontend request waterfall for ~0.5–1 s.

## Scope — exactly two changes, nothing else

### Change 1: per-request memoization in `_admin_policy_satisfied`

**File:** `src/django_core_micha/auth/permissions.py` (function at ~line 22)

Current:

```python
def _admin_policy_satisfied(user, request=None) -> bool:
    if request is None:
        return True
    if not is_subject_to_admin_auth_policy(user):
        return True
    return is_user_security_sufficient(user, request=request)
```

Target:

```python
def _admin_policy_satisfied(user, request=None) -> bool:
    if request is None:
        return True
    if not is_subject_to_admin_auth_policy(user):
        return True
    # Per-request memo: this helper is called ~10x per serialization of
    # BaseUserSerializer (ui_permissions, can_manage_support_agents,
    # security_state); each uncached call costs one auth-policy DB fetch.
    cache = getattr(request, "_dcm_admin_policy_cache", None)
    key = getattr(user, "pk", None)
    if cache is not None and key in cache:
        return cache[key]
    result = is_user_security_sufficient(user, request=request)
    if cache is None:
        cache = {}
        request._dcm_admin_policy_cache = cache
    cache[key] = result
    return result
```

**Cache invalidation:** `set_security_level()` in `src/django_core_micha/auth/security.py`
(~line 25) changes the session's `auth_level`, which is an input to the memoized result.
Add invalidation there, after the session write:

```python
    request.session["auth_level"] = level
    # Invalidate the per-request admin-policy memo (see permissions._admin_policy_satisfied)
    if hasattr(request, "_dcm_admin_policy_cache"):
        del request._dcm_admin_policy_cache
```

### Change 2: single Authenticator query in `get_user_security_state`

**File:** `src/django_core_micha/auth/security.py` (~lines 141–145)

Current:

```python
    authenticators = Authenticator.objects.filter(user=user)
    has_totp = authenticators.filter(type=Authenticator.Type.TOTP).exists()
    has_webauthn = authenticators.filter(type=Authenticator.Type.WEBAUTHN).exists()
    has_recovery = authenticators.filter(type=Authenticator.Type.RECOVERY_CODES).exists()
```

Target:

```python
    authenticator_types = set(
        Authenticator.objects.filter(user=user).values_list("type", flat=True)
    )
    has_totp = Authenticator.Type.TOTP in authenticator_types
    has_webauthn = Authenticator.Type.WEBAUTHN in authenticator_types
    has_recovery = Authenticator.Type.RECOVERY_CODES in authenticator_types
```

### Change 3: Redis cache backend + cached_db sessions in `settings_base`

**File:** `src/django_core_micha/settings/settings_base.py`

Currently no `CACHES` is configured and no `SESSION_ENGINE` override exists → every
authenticated request pays one session SELECT against Postgres. All consumer apps
already hard-depend on Redis (the Channels layer at ~line 170 uses
`env("REDIS_HOST", default="redis")`), so a Redis-backed session cache adds no new
infrastructure dependency.

Add (near the channel-layer config, reusing the same `REDIS_HOST` env var):

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
- Use Redis **db 1**, not db 0 (Channels pubsub uses db 0).
- `cached_db`, NOT `cache`: sessions must survive a Redis restart/flush.
- The lib's own test settings (`tests/settings.py`) must keep tests green — if the
  test run has no Redis available, override `CACHES` to `LocMemCache` and keep the
  default DB session engine **in the test settings only**.
- Mention in the CHANGELOG entry that consumer apps need no change but will start
  using Redis db 1 — flag it as a Migration note.

### Explicitly OUT of scope

- Do NOT change `get_policy_state`, `get_or_create_auth_policy`, or add any cross-request /
  TTL caching. Per-request memoization only — no staleness window for policy changes.
- Do NOT touch `get_user_security_state`'s double call of
  `get_required_auth_factor_count_for_user` — both calls receive `policy=policy_state`
  and do not re-fetch.
- Do NOT touch the hram frontend (duplicate `getCurrentUser()` call is a separate task).
- Do NOT modify any permission SEMANTICS — results must be bit-identical, only fewer queries.

## Tests

Add `tests/test_auth_perf.py` (follow the style of existing tests in `tests/`,
reuse `conftest.py` fixtures where possible):

1. **Memo correctness:** mock `is_user_security_sufficient`
   (patch target: `django_core_micha.auth.permissions.is_user_security_sufficient`),
   call `_admin_policy_satisfied(admin_user, request=req)` twice with the same request
   object → assert the mock was called exactly once and both results are equal.
2. **Memo invalidation:** after `set_security_level(req, "strong")`, the cache attribute
   is gone (next `_admin_policy_satisfied` call recomputes).
3. **Single authenticator query:** wrap `get_user_security_state(user)` in
   `django.test.utils.CaptureQueriesContext`; assert exactly ONE query whose SQL contains
   the Authenticator table name.
4. **End-to-end bound:** serialize an admin user with `BaseUserSerializer`
   (context = request with authenticated admin) inside `CaptureQueriesContext`;
   assert that at most ONE captured query touches the auth-policy table.

Run the full suite:

```
pytest tests/ -q
```

All pre-existing tests must stay green. If any existing test asserts query counts that
change due to this fix, update the count with an inline comment referencing this change.

## Finalize

1. Bump `version` in `pyproject.toml`: `2.18.1` → `2.18.2`.
2. Add a CHANGELOG entry at the top, following the existing format:

   ```markdown
   ## [2.18.2] — <today's date>

   ### Fixed

   **Query explosion in `BaseUserSerializer` for admin users**

   `_admin_policy_satisfied` re-fetched the auth policy from the DB on every call —
   ~10x per `/api/users/current/` serialization (8 ui_permissions helpers +
   can_manage_support_agents + security_state). Now memoized per request
   (`request._dcm_admin_policy_cache`, invalidated by `set_security_level`).
   `get_user_security_state` now reads all authenticator types in one query
   instead of three `.exists()` calls. Permission semantics unchanged.
   ```

3. Standard finalize flow per workspace `AGENTS.md`: independent reviewer pass on the
   diff, present review summary + proposed commit message, wait for explicit operator
   approval, then commit directly to `main` and push (`git push origin main`).
   **No feature branch. No PR.**

## Orientation (before touching code)

Per workspace `AGENTS.md`: `git status` (tree must be clean — if this instructions file
is the only untracked file, that is expected; include it in the commit), `git fetch`,
`git branch --show-current` (must be `main`), `git pull --ff-only`.
