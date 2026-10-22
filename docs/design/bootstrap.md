# Shared bootstrap contract

`GET <api prefix>/bootstrap/` (DCM-PERF-4) returns, in one response, what an app shell needs before its first page renders: the current user, the auth methods, the notification feed and unread count, and app-registered start data. It is part of dcm's shared URL set (`django_core_micha.api_urls`, URL name `dcm-bootstrap`), so every app that mounts that set at `api/` serves `/api/bootstrap/`. Adoption is opt-in on the client side (ucm `UCM-PERF-4`); the existing endpoints keep their URLs, payloads and behaviour.

## How each part is produced

The bootstrap does not rebuild any part. It resolves the app's own URL for each part by URL name and calls that view with an internal `GET` request to the part's own path, without query parameters, carrying the caller's session, user, cookies and headers. A part is therefore exactly what the existing endpoint returns for the same session, including an app's own override of that endpoint (an app that mounts its own view under the same URL name, as spesix does for `auth-methods`).

| Key | URL name | Endpoint today |
| --- | --- | --- |
| `user` | `user-current` | `GET /api/users/current/` (the app's `BaseUserViewSet` subclass) |
| `auth_methods` | `auth-methods` | `GET /api/auth-methods/` |
| `notifications.feed` | `canonical-notification-feed` | `GET /api/notifications/feed/` |
| `notifications.unread_count` | `canonical-notification-unread-count` | `GET /api/notifications/feed/unread-count/` |

Each part runs its own authentication, permission and throttle checks, exactly as the separate request would; the bootstrap adds no check of its own and none of its own throttles. An authenticated bootstrap therefore counts against the same throttle buckets as the three DRF requests it replaces; an anonymous bootstrap calls only `auth-methods`, which is not throttled today.

## Response

`200`, `Content-Type: application/json`, `Cache-Control: no-store`, `Vary: Cookie`. The CSRF cookie is set on every response (as `GET /api/csrf/` does), so a first visit needs no separate CSRF request.

Anonymous (or `users/current` refused the session with 401 or 403, for example an inactive account or a login still waiting for its second factor):

```json
{"user": null, "auth_methods": {"...": "as /api/auth-methods/"}, "notifications": null, "extensions": {}}
```

Nothing beyond `auth_methods` is returned, no part other than `auth-methods` is called and no extension provider runs.

Authenticated (`users/current` answered 200):

```json
{
  "user": {"...": "as /api/users/current/"},
  "auth_methods": {"...": "as /api/auth-methods/"},
  "notifications": {
    "feed": {"count": 3, "next": null, "previous": null, "results": ["..."]},
    "unread_count": {"count": 1}
  },
  "extensions": {"application_access": {"...": "the provider's payload"}, "broken_provider": null}
}
```

`notifications.feed` is the whole body of the feed's first page (its pagination links point at the feed URL, not at the bootstrap) and `notifications.unread_count` the whole body of the count endpoint.

## Errors

- A part that answers anything other than 200 (and other than 401/403 from `users/current`, which means anonymous) fails the whole bootstrap: the response has that part's status and the body `{"detail": "bootstrap_part_failed", "part": "<key>"}` (`part` is `user`, `auth_methods`, `notifications.feed` or `notifications.unread_count`), and a part's `Retry-After` header is passed on. The client then falls back to the individual endpoints. A partial response that could look like a signed-out user is never returned.
- If one of the four URL names cannot be resolved in the app, the endpoint answers `501` `{"detail": "bootstrap_unavailable"}`.
- Methods other than `GET` answer `405`.

## Extension providers

```python
from django_core_micha.bootstrap import register_bootstrap_provider

def application_access(request):
    return {"has_access": True}

register_bootstrap_provider("application_access", application_access)  # e.g. in AppConfig.ready()
```

A provider name matches `[a-z][a-z0-9_]{0,39}`. Registering a different callable under a taken name raises `ValueError`; registering the same callable again is a no-op. Providers run only for an authenticated response, in registration order, each with the Django request (the caller's session and user). A provider that raises, or whose return value is not JSON-serialisable (Django's JSON encoder), yields `null` under its key; the failure is logged with the provider name and the exception class only, never the payload, and the rest of the response is unaffected. `extensions` is `{}` when no provider is registered.
