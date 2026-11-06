# Shared privacy module

`django_core_micha.privacy` is an opt-in Django app. A host adopts it by adding it to `INSTALLED_APPS`, providing a validated
`DCM_PRIVACY` manifest, and mounting `django_core_micha.privacy.urls`. The module is not mounted through the shared API router.
Without valid configuration its endpoints return `not_configured`, and its grant guard fails closed.

The manifest binds choices to one app and site. It declares supported locales, the controller and contact, versioned notice
references, optional and required purposes, the services within each purpose, data categories, recipients, transfers, browser
storage, server retention, request triggers, and purpose-owned withdrawal cleanup. Public document references are validated
URLs or site-relative paths; the server does not fetch or proxy them. Required purposes need a necessity justification and are
never stored as choices. A manifest with no optional purposes has no optional-consent banner to show.

Hosts mount `GET manifest/`, `GET choices/`, and `POST choices/`. The first two expose public metadata and browser-scoped state;
the write endpoint is anonymous-safe, CSRF protected, origin checked, size limited, and throttled. Successful writes create or
reuse an opaque signed receipt cookie scoped to the site and server-side receipt row. Choices expire after the configured
validity period, including refusals. The receipt has no user/account link. Reads do not create or refresh it. The signed receipt
and current server-side choice are both required by `has_grant`; unknown, expired, outdated, or unavailable state means no grant.

`PrivacyDecision` stores only purpose and service revision context, decision kind, receipt sequence, server time, locale, notice
version, channel, and request ID. It does not store IP address, user agent, fingerprint, page history, referrer, content, or user
ID. Privacy decisions are not sent to the generic audit log because that would create another evidence copy with independent
retention. `prune_privacy_evidence` prunes decisions, long-expired choices, and then empty old receipts; this release does not
schedule it. Terms acceptance, study consent, AI consent, browser permissions, sessions, and unrelated functional data remain
separate.

Consumers must check `has_grant(request, purpose_id)` before optional server work and require the matching browser grant before
initializing optional browser services. The shared app does not automatically adopt consumers, implement a site-specific notice
UI, or verify deployment edge/runtime behavior. Each host needs route-level evidence before claiming complete coverage.

## Client duties and known limits (review round, 2026-11-06)

- `POST choices/` requires `config_version` (from the last `GET`/manifest) in addition to `notice_version`; a mismatch answers 409
  `stale_config` with the current state and stores nothing, so a tab showing an older purpose text cannot grant against a newer
  revision.
- `has_grant(request, purpose_id, service_id=None)`: pass `service_id` whenever a specific declared service is being enabled; a
  service not declared under that purpose never inherits the purpose's grant.
- The write API is CSRF protected and its `GET` sets no cookie: clients obtain the token from the host's existing `csrf/`
  endpoint first.
- `site` must be unique per host; the receipt cookie is named `dcm_privacy_<site>` and host-only.
- Known limit: before the first receipt cookie exists, two tabs of one browser can each create a receipt (the server cannot
  correlate cookie-less requests). Clients MUST serialize the first write across tabs (e.g. a Web Locks mutex), re-read state
  after acquiring it, and treat the cookie set by the most recent response as authoritative. Later writes are ordered by
  `expected_seq` and cannot be overridden by a stale grant.
- Choice evidence is pruned from the decision date (`effective_at`), never later than `evidence_retention_days`.
