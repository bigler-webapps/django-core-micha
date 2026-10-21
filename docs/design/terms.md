# Shared terms-of-use acceptance contract

`django_core_micha.terms` (DCM-AUTH-12) records that a signed-in person explicitly accepted one version of an application's terms of use. It is opt-in: an application that does not install it gets no table, no route and no check. The record is technical evidence of a submitted acceptance, not the terms themselves; each application owns its document, its version identifier and its URL.

## Installation

1. Add `"django_core_micha.terms"` to `INSTALLED_APPS` (app label `dcm_terms`).
2. Configure `DCM_TERMS_DOCUMENT` (below).
3. Run `manage.py migrate dcm_terms`. The migration `dcm_terms.0001_initial` creates one table (`TermsAcceptance`). It changes no profile, user or billing table, so no consuming app needs a profile migration.
4. Mount the API at a prefix of the application's choice, for example `path("api/terms/", include("django_core_micha.terms.urls"))`.
5. Gate the application's own operations where it requires acceptance, with `HasAcceptedCurrentTerms` or `has_accepted_current_terms(user)` (below). Installing the app adds no middleware and no global login or access gate.

## Configuration

```python
DCM_TERMS_DOCUMENT = {
    "app": "maana",
    "version": "2026-10-21",
    "url": "https://maana.example/terms",
}
```

The dictionary has exactly the keys `app`, `version` and `url`. `app` matches `[a-z0-9_-]{1,80}`, `version` matches `[A-Za-z0-9._-]{1,64}`, `url` is an absolute `http` or `https` URL with a host and at most 500 characters. The server is the only source of these values; the browser never supplies them authoritatively.

- Setting absent: system check warning `dcm_terms.W001`; the API answers 404 `terms_not_configured` and the check refuses everybody.
- Setting present but malformed: system check error `dcm_terms.E001`; at runtime it is treated exactly like an absent setting.
- A new `version` requires a new explicit acceptance from every person; acceptances of earlier versions stay stored unchanged and no longer satisfy the check.

## The record

`TermsAcceptance` stores `user`, `app`, `version`, `document_url` (the configured `url` at the time of acceptance) and `accepted_at` (server time of the first accepted submission). One row per person, app and version (unique constraint `dcm_terms_acceptance_unique`); a later version adds a row and never rewrites an earlier one. No IP address, user agent or browser-supplied timestamp is stored. The row is deleted with the user account. Creation is recorded in the audit log; there is no admin registration and no update or delete path in the API.

A row is created only by an explicit acceptance through `POST <prefix>/` or `accept_current_terms(...)`. It is never inferred from the privacy statement (`accepted_privacy_statement`), the convenience-cookie preference (`accepted_convenience_cookies`), an existing account, a Google or Microsoft login, a successful checkout, an administrator's edit, or a data migration. The profile fields and the user API (`BaseUserSerializer`, `GET|PATCH /users/current/`, the generic user routes) are unchanged and cannot read or write terms acceptance.

## API

Session-authenticated JSON. Every response carries `Cache-Control: no-store`. `POST` is CSRF-protected independently of the host application's middleware order. Other methods answer 405. A person can read and accept only their own acceptance; the API has no user, app or timestamp parameter.

### `GET <prefix>/`

```json
{
  "document": {"app": "maana", "version": "2026-10-21", "url": "https://maana.example/terms"},
  "accepted": false,
  "accepted_at": null
}
```

`accepted` and `accepted_at` describe the signed-in person's acceptance of exactly the configured app and version (ISO 8601 timestamp, or null).

### `POST <prefix>/`

Body, exactly these two keys:

```json
{"version": "2026-10-21", "accept": true}
```

- 201: the acceptance was recorded; the body has the `GET` shape with `accepted: true`.
- 200: the person had already accepted this version; the body is the same and `accepted_at` is the original timestamp. Repeated and concurrent submissions produce one row.

Errors write nothing. They are checked in this order:

| Status | Body | When |
| --- | --- | --- |
| 401 | `{"detail": "authentication_required"}` | not signed in |
| 404 | `{"detail": "terms_not_configured"}` | no valid `DCM_TERMS_DOCUMENT` |
| 400 | `{"detail": "invalid_request"}` | body is not a JSON object |
| 400 | `{"detail": "unexpected_fields", "fields": ["accepted_at"]}` | any key other than `version` and `accept` (sorted list), for example a submitted timestamp, user, app or URL |
| 400 | `{"detail": "acceptance_required"}` | `accept` missing or not the JSON value `true` |
| 400 | `{"detail": "version_required"}` | `version` missing, not a string or empty |
| 409 | `{"detail": "version_mismatch", "document": {...}, "accepted": false, "accepted_at": null}` | `version` differs from the configured version (stale page); the body carries the current document and acceptance state so the client can show the current terms and ask again |

A CSRF failure answers 403 with the host application's CSRF failure response.

## Server-side check

```python
from django_core_micha.terms.services import (
    TermsDocument, TermsNotConfigured, TermsVersionMismatch,
    current_document, has_accepted_current_terms, accept_current_terms,
)
from django_core_micha.terms.permissions import HasAcceptedCurrentTerms
```

- `current_document()` returns `TermsDocument(app, version, url)` or `None` when the setting is absent or malformed.
- `has_accepted_current_terms(user)` returns `True` only for a signed-in person with a row for the configured app and version. It returns `False` for anonymous users and when the document is not configured (fail closed).
- `accept_current_terms(user, version)` returns `(acceptance, created)`. It raises `TermsNotConfigured` without a valid document and `TermsVersionMismatch` (with `.document`) when `version` differs; it never takes a timestamp, app or URL from the caller.
- `HasAcceptedCurrentTerms` is a DRF permission for the application's own views. It denies with 403 `{"detail": "terms_acceptance_required"}` (code `terms_acceptance_required`) when the signed-in person has not accepted the current version; anonymous requests get DRF's not-authenticated response. The application decides which operations use it.
