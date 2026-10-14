# DCM-NOTIF-29 — Render notifications in the app profile language

# A. Envelope

Spesix stores the recipient language on `user.profile.language`; the shared dispatcher currently reads only `user.contact_profile.language`, so English and French recipients receive German mail. Add the app profile fallback and publish version 2.44.6. Operator explicitly approved the Core correction, release and Spesix update on 2026-10-14.

Tier 3: shared-core internals. Preserve contact-profile precedence: when a contact profile exists, its existing supported-language/default behavior remains authoritative. Only when the contact profile is absent or None use the app profile language. Supported languages remain de/en/fr, missing or unsupported values default to de. Do not change recipient selection, channels, preferences, critical delivery, text registration, templates, dependencies, schema, authentication, CI or unrelated AI budgeting code. No frontend, prototype or new product text.

Required tests: actual registered-text email rendering for app-profile de/en/fr without a contact profile, contact-profile precedence, missing profiles and unsupported languages, and preservation of opted-out email. Exercise actual renderer/delivery rather than only a language resolver stub. Existing dispatch, delivery and preference regression tests remain the affected set. Risk: changing existing consumers' locale precedence; preserve it explicitly.

# B. Implementation map — ADDRESSED TO THE IMPLEMENTER

Target working directory: `C:/Users/Micha Bigler/Documents/webapps/django-core-micha`.

Own only `src/django_core_micha/notifications/dispatch.py` (`_recipient_language` and its use by `_render_content`), NEW `src/django_core_micha/notifications/tests/test_recipient_language.py`, `pyproject.toml` version 2.44.5 to 2.44.6, and the corresponding entry in `CHANGELOG.md`.

Context: `_recipient_language` currently catches AttributeError on contact_profile and defaults to German; Django missing reverse one-to-one relations also inherit AttributeError. `_render_content` merges transient formatting params but does not expose a language override. Reuse this canonical seam, no app monkeypatch/second mail renderer. `notifications/tests/test_dispatch.py`, `test_delivery.py` and `test_preference_matrix.py` show real notification registration, recipients, locmem outbox and preferences. `tests/settings.py` provides in-memory SQLite and testapp. Use existing models/fixtures or controlled profile-shaped user attributes where the test app lacks the consumer's profile model; do not add database models or migrations. Registered texts must actually differ across the three languages. Spesix caller context is read-only at `../spesix/backend/claims/notifications.py` and `test_notif3_bundle.py`.

> This WO is the COMPLETE specification. You are not alone in the shared checkout: preserve others' edits. Explicit UTF-8 file I/O and absolute assigned paths. Source/test edits and ONE run of only your new test file are permitted: from the absolute Core repo directory, set PYTHONUTF8=1 and PYTHONPATH to this repo's src, then python -m pytest src/django_core_micha/notifications/tests/test_recipient_language.py --tb=short --show-capture=no. Any failed run is BLOCKED; no retry or environment repair. No git/add/commit/push, reviews/self-review, agents, package commands/install/lockfiles, startup/Docker, CI, register/MEMORY or .env/secrets reads. Read conventions only as needed. PLAN then PROGRESS within two minutes; RESULT DONE or BLOCKED with changed files/test result, leave uncommitted. Your run is not the root gate.

# C. Orchestrator only — NOT ADDRESSED TO THE IMPLEMENTER

> Implementer: STOP here. Review, register, git and publication belong to root.

Gate #1 is the operator's explicit 2026-10-14 approval of this correction and version 2.44.6. Dispatch fresh native codex/gpt-6-luna/high, no inherited context. Inherited workspace bootstrap versus assigned Core cwd; effective provider/sandbox/cwd not exposed or verified. Four configured independent read-only lenses concurrently; additional privacy/security lens for delivery regression. Root runs affected dispatch/delivery/preferences/security tests, then commits source and register and pushes main only on green. Existing push-triggered publish.yml runs tests before publication; observe it to successful 2.44.6 publication. No manual dispatch or proof ref in Core. Separately review intervening 2.44.2–2.44.5 AI budget changes and map the authorized Spesix dependency update; do not install into the global host environment.
