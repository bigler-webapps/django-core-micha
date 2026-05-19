# django-core-micha — Memory

Durable notes that should outlive single sessions. Append only on real discoveries — not session summaries.

## Follow-ups (when triggered)

### Centralize programmatic login (auth/session helper)
Currently `register_confirm` in `auth/views.py` is the only place that does programmatic `auth_login(request, user)` for a freshly created user. Logic is inline:

```python
backends = get_backends()
if backends:
    first = backends[0]
    user_obj.backend = f"{first.__class__.__module__}.{first.__class__.__name__}"
auth_login(request, user_obj)
```

When a second call-site emerges (e.g. MFA recovery completion that creates/promotes a user, or a future signup mode), refactor into a single helper, e.g. `django_core_micha.auth.session.login_user_with_session(request, user)`.

Robustness improvement to ship with the helper: prefer `ModelBackend` (or any subclass like allauth's `AuthenticationBackend`) via `isinstance` instead of blind first-index pick, then fall back to `backends[0]`. This survives apps that re-order `AUTHENTICATION_BACKENDS` for unusual reasons.

**Do not ship as a standalone release.** Bundle with the next auth-touching change to avoid release churn (8 consumer apps would have to bump for an internal refactor without user-visible benefit).

**Origin:** 2.9.2 introduced the dynamic backend lookup as a hotfix to the hardcoded `ModelBackend` from 2.9.1. Both work in practice because all apps inherit the lib's default `AUTHENTICATION_BACKENDS = [ModelBackend, AuthenticationBackend]`. The helper is a future-proofing measure, not a fix.

## Release discipline

Patch-bumps (`X.Y.Z` → `X.Y.Z+1`) are the default for any change. Only minor-bump (`X.Y+1.0`) when there is a genuinely new public-API surface that consumer apps need to adopt. The 2.9.0 incident (broken release auto-propagated via `>=2.9.0` to all apps) led to switching all consumer apps to exact pins (`==X.Y.Z`); rollouts now require an explicit per-app bump PR.
