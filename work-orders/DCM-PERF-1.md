# DCM-PERF-1 — Cache the singleton auth-policy lookup

Status: planned · Tier 3 · Target repo: `django-core-micha` (main)

---

## Part A — Envelope (Expertenchat, 2026-08-29)

### Goal

`get_or_create_auth_policy()` (`django_core_micha/auth/policy.py`) issues a real
`Model.objects.get_or_create(pk=1)` query every time it is called, with no caching anywhere in the
call chain. It is the swappable-model accessor for a **singleton** row (`AUTH_POLICY_MODEL`, e.g.
`users.AuthPolicy`, configured per consuming app — jg-ferien, Cinevia, kira all set it). Add a
process-shared cache (Django's cache framework, backed by the existing Redis `default` cache —
`settings_base.py:197-207`) in front of this lookup, invalidated on write, so repeated calls within
the cache lifetime read from Redis instead of the database.

### How this was found

Investigated in this session from an operator-reported slow endpoint: `GET /api/users/` in
jg-ferien took 4.77s / 176 kB for a user list. Read-tracing `UserSerializer`/`BaseUserSerializer`
found `BaseUserSerializer.get_security_state` (`auth/serializers.py:199-201`) calling
`get_policy_state()` → `get_or_create_auth_policy()` **once per serialized row**, with a second,
independent call to the same function from jg's own `get_auth_policy` field
(`users/serializers.py:214-218`). Neither call is cached anywhere in the chain, so a list of N users
issues at least N of these queries just for policy state, before any other per-row cost.
`get_or_create_auth_policy()` has 9 call sites across `auth/views.py`, `auth/security.py`,
`auth/policy.py` itself, and `invitations/mixins.py` (via `get_policy_state()`) — none cached. This
WO fixes the shared root cause once, at the source, rather than caching it separately at each of the
9 call sites (and the jg-ferien-specific duplication above it).

### Scope

**In scope — `django-core-micha` only:**
- `get_or_create_auth_policy()` (`auth/policy.py`): wrap the `get_or_create(pk=1)` read in the shared
  Redis-backed cache (`django.core.cache.cache`, the `default` cache already configured in
  `settings_base.py`). Cache key must be derived from the resolved model (`get_auth_policy_model()`,
  e.g. its `_meta.label`), since `AUTH_POLICY_MODEL` differs per consuming app but is fixed for the
  lifetime of one Django process.
- Invalidation: connect a `post_save` **and** `post_delete` receiver for the resolved
  `AUTH_POLICY_MODEL` class that clears the cache key, so a policy write is visible immediately to
  the next call in any process (the cache backend is shared Redis, so one process's invalidation is
  visible to all). Connect this once (e.g., lazily on first resolution, or from an `AppConfig.ready()`
  hook) — do not double-connect across repeated calls.
- A bounded TTL on the cache entry as defense-in-depth for any write path that bypasses the ORM's
  `save()`/`delete()` (raw SQL, `.update()`, a future bulk operation) and therefore would not fire the
  signal. Pick a TTL that is short enough to bound staleness to something clearly acceptable for a
  registration-policy setting (this is not a security-critical toggle re-checked per request elsewhere
  — see `auth/security.py` also calling `get_policy_state()` for the *required* 2FA level) — state the
  chosen value and the reasoning in the diff, do not leave it an unexplained magic number.
- `get_policy_state()` itself is unaffected except that its `policy` input now typically arrives
  pre-cached; no change to its own derivation logic.

**Out of scope — do not touch:**
- **jg-ferien** (or any other consuming app). This WO fixes the dcm-side root cause only. The
  jg-ferien-local duplication — `get_auth_policy` re-deriving the same policy state as a second field,
  and `get_capabilities`/`get_ui_permissions`/`get_available_roles` issuing their own separate
  membership/capability queries per row instead of using the PERF-5 prefetch cache — is real and
  already noted for a **separate Tier-2 WO in jg-ferien**. Do not fold it in here; it would blur the
  independence of the two diffs and the two repos' review/commit cadence.
- No change to `RegistrationPolicyState`'s shape, `AuthPolicySerializer`, or any policy field
  semantics. No new/removed/reordered API fields anywhere. This WO is invisible from outside except in
  speed.
- No schema change, no migration (the underlying model is untouched, only reads of it are cached).
- No change to the swappable-model mechanism itself (`AUTH_POLICY_MODEL` resolution,
  `get_auth_policy_model()`'s fallback-to-`None` behavior for apps that don't configure it).
- Do not touch `auth/security.py`'s own logic beyond it transparently benefiting from the now-cached
  `get_policy_state()` — no behavioral change to required-auth-factor/security-level computation.

### Expected outcome

`get_or_create_auth_policy()` hits the database at most once per cache TTL window per process group
(shared across processes via Redis), instead of once per call. A policy write is visible to the next
read practically immediately (signal-invalidated), with the TTL only as a fallback bound for writes
that bypass signals. No observable behavior change other than reduced query count and latency.

### Risks

- **Staleness window if a write bypasses the ORM signal** (raw SQL, `.update()`, an admin action using
  a queryset method instead of instance `.save()`) — bounded by the TTL, not eliminated. State this
  explicitly rather than presenting the cache as always-consistent.
- **Signal double-registration**: if the connection hook runs more than once (e.g. re-imported module,
  multiple `AppConfig.ready()` calls in tests), the same invalidation could fire twice per write —
  harmless for a cache `delete()`, but worth guarding to avoid accumulating duplicate receivers over a
  long-running process.
- **Model resolution timing**: `AUTH_POLICY_MODEL` is a swappable-model string resolved via
  `apps.get_model(...)`, which is only safe to call after Django's app registry is ready. Connecting
  the signal too early (e.g., at import time of `policy.py`) will fail or silently no-op — verify the
  chosen hook point actually runs after app-loading.
- **Cross-app safety**: the same dcm code runs inside jg-ferien, Cinevia, and kira, each with its own
  concrete `AuthPolicy` model — the cache key and signal must be correct per-resolved-model, not
  hardcoded to one app's model path.

### Tests to WRITE (scoped — run these, not the full suite)

- A call-count/query-count test: two consecutive calls to `get_or_create_auth_policy()` (or through
  `get_policy_state()`) within the same process issue only one DB query, not two.
- An invalidation test: after a policy object is saved (`.save()`) or deleted, the next call reflects
  the change immediately (not stale until TTL expiry) — this is the test that actually proves the
  signal is wired, not just that caching exists.
- A TTL-fallback test: with the cache pre-populated with a stale value and no signal fired (simulating
  a bypassed write), confirm the value expires and re-reads after the configured TTL — or, if that is
  impractical to assert without sleeping in a test, at minimum a unit assertion on the configured TTL
  value itself so a future edit can't silently drop it.
- A cross-model-safety test: two different resolved `AUTH_POLICY_MODEL` values (or a mock swap) do not
  collide on the same cache key.
- Output parity: `get_policy_state()`'s returned `RegistrationPolicyState` is unchanged in shape and
  values versus the uncached path, for at least one non-default policy configuration.

Command shape: `pytest tests/ -k "policy or auth_policy"` plus the new cache/signal test module — no
full suite (that is the promotion gate's job).

### Preconditions

None. Independent of the open jg-ferien-side follow-up WO (not yet written) — that WO depends on this
one only in the sense that fixing dcm's root cause first avoids re-measuring jg's endpoint twice, but
there is no code dependency between them.

### Approval Gate #1

Pending — this WO stops here for the operator's explicit go-ahead before implementation.

### Mini-handover (pastable)

Orchestrator: implement `work-orders/DCM-PERF-1.md` in `django-core-micha` (main). `git pull` first,
read the WO and `CLAUDE.md`, then follow `orchestrate-codex` (write Part B yourself, Codex-first,
independent review — Tier 3, shared-core — commit on green).
