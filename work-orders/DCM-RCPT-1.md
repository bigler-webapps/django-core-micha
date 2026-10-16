# DCM-RCPT-1 — Shared receipt recognition on top of the extraction module

Prepared: 2026-10-16. Target repository: `django-core-micha` (`main`). Companion: `ui-core-micha/UCM-RCPT-1` (capture and review components). Consumers:
- `reimbursements/RMB-RCPT-1`;
- `spesix/SPX-RCPT-1`;
- `bigler-consult/BCO-EXP-1`.

# A. Envelope — addressed to everyone

## Goal and expected outcome

Two apps read receipts with a language model, and a third (bigler-consult) is about to. The provider layer is already shared: `django_core_micha.extraction` (AI-1, AI-5) prepares images and PDFs, calls the provider, salvages JSON and enforces the spend cap. The receipt layer on top of it is not shared:
- **reimbursements** (`backend/claims/services/openai_ocr.py`, 1122 lines) has its own schemas, prompts and normalisers for single receipts, several receipts per file and payment-proof classification.
- **spesix** (`backend/claims/ocr.py`, 308 lines) bypasses the module entirely. It has its own OpenAI Chat Completions call, its own cost guard, its own consent and its own account suggestion.

Expected outcome: dcm offers one receipt-recognition module. It covers:
- a core receipt schema that apps can extend;
- the prompt and normalisers;
- file preparation;
- one or several receipts per file, with their position on the image;
- an account suggestion against the app's own chart of accounts;
- a duplicate fingerprint;
- a standing, revocable user consent;
- one central default model.

An app supplies only its extension fields, its accounts and what it does with the result.

## Operator decisions this Envelope records (2026-10-16)

- **Scope of the lift:** the receipt layer plus UI (`UCM-RCPT-1`). The ZIP flow of reimbursements that pairs receipts with bank proofs stays in reimbursements; it calls this module for the reading.
- **Schema:** core fields plus app extension.
- **Model:** `gpt-6-luna` on the OpenAI driver as the central default, overridable per app by setting. The keys come centrally (webapp-management WM-SEC-5).
- **Consent:** standing, per user, revocable, recorded in dcm with an audit entry. Recognition is refused without it.
- **Consumers in this campaign:** reimbursements and spesix move now, and bigler-consult uses the module in `BCO-EXP-1`.
- **Defaults** set by the Expertenchat, overridable:
  - no central per-day count limit, only the existing spend cap; an optional per-user daily count is available as a setting;
  - the prompt is multilingual with a Swiss focus (CHF default, Swiss date and number formats).

## Scope

- **Module:** `django_core_micha.receipts`. It is opt-in through `INSTALLED_APPS`, because it brings the consent table, and it needs the `extraction` and `ai` extras.
- **Core schema** of one recognised receipt:
  - merchant;
  - date (ISO);
  - total amount (decimal string, two places);
  - currency (ISO 4217, default CHF);
  - payment method (`cash`, `card`, `other`, `unknown`);
  - line items (description, amount, optional position on the image);
  - optional VAT lines (rate, amount);
  - the receipt's position on the image when there are several;
  - a confidence per receipt (`high` or `low`).

  Every object follows the extraction module's schema contract.
- **App extension:** an app can add fields, such as reimbursements' converted amount, FX rate and description, together with prompt text for them and a normaliser that runs after the core normalisers. Extension fields cannot replace core fields.
- **Normalisers:**
  - amounts are absolute values with two decimal places, and an empty or zero amount becomes empty;
  - currency is three uppercase letters;
  - the date must be valid ISO, and an invalid one becomes empty;
  - the payment method is mapped to its enum;
  - positions are clamped to the image (0–1).
- **Reading:**
  - one call reads a file that holds one receipt or several;
  - images are prepared by the module: HEIC/HEIF converted to JPEG, then resized;
  - PDFs use their text layer, and a PDF without text goes through the upload path (AI-5);
  - a file type and size allowlist applies: JPG, PNG, HEIC, PDF, up to 10 MB.
- **Account suggestion:** an app hook returns the accounts that apply to the user and context, as id and name. The suggestion is part of the reading. The module returns only ids from that list, each with a confidence, and drops any other id. Without the hook there is no suggestion.
- **Duplicate fingerprint:** a perceptual-hash helper for images, as spesix uses today, so apps can warn about duplicates.
- **Consent:**
  - a per-user consent record (granted, revoked, with timestamps) and an audit entry for each change;
  - REST endpoints to read, grant and revoke one's own consent;
  - every reading checks it first;
  - existing app consents can be imported through a provided migration helper, used by the consumer orders.
- **REST endpoint** for the ui-core-micha components: it reads an uploaded file for the authenticated user and returns the recognised receipts.
  - It requires consent.
  - It applies the app's extension and account hooks.
  - It stores nothing; storing the file and the result is the app's business.
  - Apps keep calling the Python API from their own endpoints when they need more, such as creating drafts or cropping.
- **Model and limits:**
  - the default model is a setting (`gpt-6-luna`), with a rate-table row;
  - the existing spend cap applies to every reading;
  - an optional per-user daily count limit is available; without the setting, no count limit applies.
- **Fake reader for end-to-end tests:** a deterministic reader can be selected by setting, as spesix's E2E suite does today (SPX-E2E-21). It never calls a provider, and the module refuses it outside local and test environments.
- **Errors:** one stable code per failure:
  - no consent;
  - file type or size;
  - nothing recognised;
  - spend cap or count limit reached;
  - provider unavailable or failed.

  The codes map to HTTP statuses, and clients never see provider details.
- **Documentation:** a design document under `docs/design/`, the `CHANGELOG`, and `webapp-management/SHARED_CAPABILITIES.md`. That file gets rows for receipts and for the extraction module, which has none today. The dcm `AI-1` register row says AI-3 switches to Claude; AI-3 was dropped, so the row is corrected.

## Non-goals

- Pairing receipts with bank proofs, ZIP sessions and creating claims or expenses. Those stay in the apps.
- Storing files or results.
- The UI (`UCM-RCPT-1`).
- Mailbox ingestion (`bigler-consult/BCO-ING-1`).
- Changing the extraction module's drivers.

## Tier

Tier 3: a change inside shared-core that adds a consent record (privacy) and a new endpoint sending user files to a provider.

## Risks

- **Personal data to a provider:** receipts carry names, card digits and locations. Consent before every reading, the allowlist and the spend cap are the controls, and nothing is stored by the core.
- **Behaviour change for consumers:** the normalisers and the default model differ from what each app does today. The consumer orders pin the differences with tests.
- **Prompt drift:** a single shared prompt serves three apps. Extension prompt text is appended and never replaces the core rules.

## Required tests to write

- Schema: the core schema passes `validate_schema`, and an extension adds fields without replacing core ones.
- Normalisers: amounts, currency, date, payment method and positions, each with valid and invalid inputs.
- Reading, with fake drivers:
  - one receipt;
  - several receipts with positions;
  - a PDF with text;
  - a PDF without text through the upload path;
  - HEIC conversion;
  - the type and size allowlist.
- Account suggestion: only ids from the hook's list come back, and an unknown id is dropped.
- Consent:
  - a reading without consent is refused before any provider call;
  - grant and revoke are recorded with audit entries;
  - the import helper carries existing consents over.
- Limits: the spend cap applies to every reading, and the optional count limit refuses after N.
- Errors: each failure maps to its code and status, without provider detail.
- REST: the endpoint requires authentication and consent, and stores nothing.

# B. Implementation map — addressed to the implementer

Work from this package; do not explore broadly. Part A is the authoritative spec; this map fixes names, shapes and invariants so that `UCM-RCPT-1` (UI) and the consumer apps build against them. One dispatch; complete, no stubs. The module handles personal data that goes to an AI provider: when in doubt, refuse rather than send.

### Target repo working directory (absolute)

`C:\Users\Micha Bigler\Documents\webapps\django-core-micha` (branch `main`). Never the workspace root. Do not touch other repositories. Do not touch `extraction/*` (its drivers and cost guard are used as they are), `assistant/*`, `billing/*`, `pyproject.toml`, `tests/settings.py`, `tests/test_declared_dependencies.py`, `docs/`, `CHANGELOG.md`, `WORK_ORDERS.md` (the Orchestrator already prepared `pyproject.toml` with the extra `receipts = ["pillow-heif>=1.8.0"]`, the pytest path `src/django_core_micha/receipts/tests`, the INSTALLED_APPS entry in `tests/settings.py` and the dependency test; `pillow-heif` is installed in the interpreter named in your dispatch note). Run tests with that interpreter.

### Context package

- Pattern to copy for an opt-in module: `billing/` (`apps.py` with `label`, `models.py`, hand-written `migrations/0001_initial.py`, `audit_config.py` registering models with `django_core_micha.auditlog.register`, plain Django JSON views with `@csrf_protect` and session auth in `views.py`, `urls.py` that the app includes itself — the module mounts nothing in `api_urls.py`). Tests run with migrations.
- Provider layer, used unchanged: `extraction.extract_document(request, provider=, api_key=, model=, user_id=)` (reserves budget before the call, settles usage; raises `extraction.cost_guard.AICostLimitExceeded` when the daily cap is hit and `DocumentExtractionError(code, message, status_code)` for provider failures), `extraction.ExtractionRequest` (`system_prompt`, `user_prompt`, `schema`, `max_output_tokens`, `effort`, `thinking` (must be passed explicitly, a mapping), `image_bytes`+`image_mime_type`, `pdf_text`, or `upload_bytes`+`upload_mime_type`+`upload_filename`; image and upload are exclusive), `extraction.validate_schema` (every object `additionalProperties: false`, no type arrays), `extraction.resize_image_bytes`, `extraction.extract_pdf_text`, `extraction.require_json_payload` / `extract_json_payload` (JSON salvage), `ExtractionResult.raw_text`. Read `extraction/service.py`, `extraction/types.py`, `extraction/input_prep.py`, `extraction/errors.py` first (about 250 lines). Every schema property must be listed in `required` (strict provider mode): "absent" is an empty string, or zeros for numbers; do not use type arrays or `null` types.
- Credentials: `settings.OPENAI_API_KEY` for provider `openai` (default), `settings.ANTHROPIC_API_KEY` for `anthropic` (same names the assistant uses). The cost rows for `gpt-6-luna` already exist in `extraction/cost_guard.py` and `ai_prices.py`; add a test that pins a non-zero price for the default model, do not edit those files.
- Existing consumer code to learn the prompt and normaliser behaviour from (read-only, do not copy blindly, do not touch): `reimbursements/backend/claims/services/openai_ocr.py` (normalisers, multi-receipt schema, positions), `spesix/backend/claims/ocr.py` and `claims/image_utils.py` (`perceptual_hash`, HEIC handling, fake reader) under `C:\Users\Micha Bigler\Documents\webapps\`.
- Environment detection: `getattr(settings, "ENV_TYPE", "")`; the fake reader is allowed only for `"local"` or `"test"` (the test settings do not define it: tests set it with `override_settings`).

### Fixed design — package `src/django_core_micha/receipts/`

Files: `__init__.py`, `apps.py` (`name="django_core_micha.receipts"`, `label="dcm_receipts"`, imports `audit_config` in `ready`, system check that refuses `DCM_RECEIPTS_READER="fake"` when `ENV_TYPE` is not local/test), `models.py`, `migrations/0001_initial.py` (hand-written), `audit_config.py`, `errors.py`, `schema.py`, `normalise.py`, `prompt.py`, `prepare.py`, `accounts.py`, `fingerprint.py`, `consent.py`, `limits.py`, `reader.py`, `fake.py`, `views.py`, `urls.py`, `tests/` (with `conftest.py` that makes factory requests skip CSRF like `billing/tests/conftest.py`).

1. **Settings** (all read at call time, documented defaults): `DCM_RECEIPTS_PROVIDER` = `"openai"`; `DCM_RECEIPTS_MODEL` = `"gpt-6-luna"`; `DCM_RECEIPTS_EFFORT` = `"low"`; `DCM_RECEIPTS_MAX_OUTPUT_TOKENS` = 4000; `DCM_RECEIPTS_EXTENSION` = dotted path to an extension object (below), unset by default; `DCM_RECEIPTS_ACCOUNTS_HOOK` = dotted path to `fn(user, context) -> list[{"id", "name"}]`, unset by default; `DCM_RECEIPTS_DAILY_LIMIT` = unset (no count limit; integer N refuses the N+1th reading of a user per calendar day); `DCM_RECEIPTS_READER` = `"live"` (`"fake"` selects the fake reader).
2. **Consent** (`models.ReceiptConsent`): one row per user (`user` OneToOne, `granted_at`, `revoked_at` nullable, `source` CharField(40) blank, `updated_at`). Consent is active when `granted_at` is set and `revoked_at` is empty or older than `granted_at`. `consent.py`: `has_consent(user)`, `grant(user, source="user")`, `revoke(user)`, `state(user)` -> `{"granted": bool, "granted_at", "revoked_at"}`, and the migration helper `import_consents(pairs, *, source="import")` where `pairs` is an iterable of `(user, granted_at)`; it creates a missing row, never re-grants a user who revoked after the given time, never lowers an existing `granted_at`, is idempotent, and returns counts (`created`, `skipped`). Register the model in `audit_config.py` so every change is audited (look at `billing/audit_config.py`); no free-text from the user is stored. `grant`/`revoke` change state inside `transaction.atomic()` with `select_for_update`.
3. **Errors** (`errors.py`): `ReceiptError(code, status)` and the stable codes with statuses: `consent_required` 403, `unsupported_file_type` 415, `file_too_large` 413, `invalid_request` 400, `nothing_recognised` 422, `spend_cap_reached` 429, `count_limit_reached` 429, `provider_unavailable` 503, `authentication_required` 401. A `DocumentExtractionError` becomes `provider_unavailable`; `AICostLimitExceeded` becomes `spend_cap_reached`; any other exception from the provider path becomes `provider_unavailable`. The message and body never contain provider text, exception text, file names or file content; logging records the code and the exception class name only.
4. **Core schema** (`schema.py`): `core_schema()` returns the root `{"receipts": [ receipt, ... ]}` with `additionalProperties: false` everywhere and all properties required. A receipt: `merchant` (string), `date` (string, ISO or ""), `total` (string, decimal with two places or ""), `currency` (string), `payment_method` (enum `cash|card|other|unknown`), `items` (array of `{description, amount, box}`), `vat` (array of `{rate, amount}`), `box` (`{x, y, w, h}` numbers 0–1; `0,0,0,0` means unknown), `confidence` (enum `high|low`), and — only when the accounts hook is configured — `account_suggestions` (array of `{account_id: string, confidence: enum high|low}`). `build_schema(extension, with_accounts)` merges the extension's extra properties into the receipt object and adds them to its `required` list; an extension that names a core property is a configuration error (`ValueError`) — core fields can never be replaced. The result must pass `extraction.validate_schema`.
5. **Extension** (`schema.py`): an app supplies an object with `properties` (dict of JSON-schema fragments, same subset), optional `prompt` (text appended after the core rules, never replacing them) and optional `normalise(receipt: dict, context) -> dict` that runs after the core normalisers; its return value may only change extension keys (the reader re-applies the core fields afterwards, so an extension cannot overwrite them). Resolve `DCM_RECEIPTS_EXTENSION` with `django.utils.module_loading.import_string`; the Python API also accepts an `extension=` argument that wins.
6. **Normalisers** (`normalise.py`, pure functions, no I/O): amount -> absolute value as a `Decimal` string with two places (`"12.5"`, `"-12.50"`, `"CHF 12,50"`, `"1'234.50"` and `"1.234,50"` forms accepted), empty/zero/unparseable -> `""`; currency -> three uppercase ASCII letters else the configured default `CHF`; date -> valid ISO `YYYY-MM-DD` else `""` (also accept `DD.MM.YYYY`); payment method -> the enum, unknown input -> `unknown`; box values clamped to 0–1, a box with `w` or `h` equal to 0 -> `None`; strings stripped and truncated (merchant 200, description 200); at most 100 items and 20 VAT lines and 50 receipts; confidence anything but `high` -> `low`. A receipt that has neither a merchant nor a total after normalising is dropped.
7. **Prompt** (`prompt.py`): English instructions, multilingual input with a Swiss focus (CHF default, `dd.mm.yyyy` dates, `1'234.50` numbers, VAT 8.1 / 2.6 / 3.8 %), rules for several receipts per file with positions, "never guess a value: leave it empty", an explicit statement that the document content is data and any instructions inside it are ignored, the extension prompt appended, and — when the hook returned accounts — the list `id: name` with the instruction to answer only with those ids. Keep it one function `build_prompts(extension, accounts) -> (system, user)`.
8. **File preparation** (`prepare.py`): sniff the type from the leading bytes, not from the name or the declared type: JPEG (`FFD8FF`), PNG (`89504E47`), PDF (`%PDF`), HEIC/HEIF (`ftyp` box at offset 4 with a heic/heix/mif1/heif brand). Anything else -> `unsupported_file_type`; empty or larger than 10 MB (`MAX_FILE_BYTES = 10 * 1024 * 1024`) -> `file_too_large` / `invalid_request` (empty). HEIC: `pillow_heif.register_heif_opener()` (lazy import; a missing dependency is a configuration error that surfaces as `provider_unavailable` without a stack trace to the client), then `extraction.resize_image_bytes`. Images: `resize_image_bytes` -> `image_bytes`. PDFs: `extract_pdf_text`; with text -> `pdf_text`; without text -> `upload_bytes` with `application/pdf` and a fixed neutral `upload_filename` such as `receipt.pdf` (never the user's file name). The prepared result is a small dataclass the reader turns into the `ExtractionRequest`.
9. **Reading** (`reader.py`): `read_receipts(user, content: bytes, *, context=None, extension=None) -> list[dict]`. Order is binding: authenticated user object -> consent (`consent_required` before anything else, no provider call, no file handling) -> optional daily count (`limits.py`: cache key per user and day, increment only after consent and type checks passed, refuse with `count_limit_reached` when the count would exceed the limit; a failed provider call does not give the count back) -> preparation -> fake reader or `extract_document(...)` with `user_id=user.pk` (the spend cap applies to every reading through it) -> `require_json_payload` -> core normalisers -> extension normaliser (then core fields re-applied) -> account filter -> `nothing_recognised` when no receipt is left. A returned receipt carries the core fields, the extension fields and `account_suggestions` (only when the hook is configured).
10. **Accounts** (`accounts.py`): `get_accounts(user, context)` calls the hook inside try/except (a failing hook means no suggestion, never a failed reading, logged by class name); accept only items with a non-empty `id` and `name`, ids as strings, at most 200 accounts. `filter_suggestions(suggestions, accounts)` keeps only ids contained in that list, `confidence` forced to `high|low`, deduplicated, at most 3 per receipt, original order.
11. **Fingerprint** (`fingerprint.py`): `perceptual_hash(content: bytes) -> str` — a 64-bit difference hash implemented with Pillow only (no new dependency: grayscale, 9x8 resize, compare neighbours, 16 hex characters); HEIC through the same opener; unreadable content -> `""`. `hamming_distance(a, b) -> int | None` (`None` when either is empty) and `is_probable_duplicate(a, b, threshold=5)`.
12. **Fake reader** (`fake.py`): deterministic, never touches a provider, no network. One receipt (merchant `Fake Merchant`, total derived from the content hash so different files differ, today's date is NOT used so results are stable, currency `CHF`) — two receipts with boxes when the first 8 bytes of the content are `FAKE-MUL`. Selected by `DCM_RECEIPTS_READER = "fake"`; refused (`ImproperlyConfigured`) unless `ENV_TYPE` is `local` or `test`; it still runs consent, type, size and count checks first, so end-to-end tests exercise the same gates.
13. **REST** (`views.py`, `urls.py`; plain Django views like `billing/views.py`, session auth, `@csrf_protect`, every response `Cache-Control: no-store`, JSON): `GET consent/` -> `state(user)`; `POST consent/` -> grant, answer the state; `DELETE consent/` -> revoke, answer the state; `POST read/` (multipart: `file` required, optional `context` as a JSON object string, anything else ignored) -> `{"receipts": [...]}`. Unauthenticated -> 401 `{"code": "authentication_required"}` (not a redirect). Errors -> `{"code": <code>}` with the status above and no other fields. The view reads the upload once, passes bytes to `read_receipts`, stores nothing (no model, no file, no cache of content or result), and logs nothing about the content. A malformed `context` -> 400 `invalid_request`. The urls are named (`receipts-consent`, `receipts-read`) for the app to include.
14. **Docs are the Orchestrator's** (`docs/design/receipts.md`, `CHANGELOG`, capability rows, register); you write only code and tests, plus module docstrings that state the contract in a few lines.

### Tests (new files only, under `receipts/tests/`)

Use fake provider callables by monkeypatching `django_core_micha.receipts.reader.extract_document` (and the upload path through the same seam); no network, no real key. Cover everything in the Envelope's "Required tests to write", concretely: core schema passes `validate_schema` with and without accounts, an extension adds fields, naming a core property raises; each normaliser with valid and invalid inputs (amount formats, zero, currency, date forms, payment enum, box clamp and the w/h zero rule, caps); reading one receipt, several with boxes, PDF with text (`pdf_text` set), PDF without text (`upload_bytes` set, neutral file name), HEIC converted to JPEG (build a tiny HEIC with `pillow_heif` or skip with a clear reason only if encoding is unavailable), type sniffing (a `.jpg` name with PDF bytes is read as PDF; `GIF` and `EXE` bytes refused), 10 MB limit; account suggestion keeps only hook ids and drops unknown ones, a failing hook means no suggestion; consent refuses before any provider call (assert the patched `extract_document` was not called and that preparation did not run), grant and revoke produce audit entries, revoke then read is refused, `import_consents` is idempotent and respects a later revocation; spend cap: `AICostLimitExceeded` from the provider path -> `spend_cap_reached` 429; count limit: refuses the N+1th reading, no limit when unset, a refused call does not consume a count; each failure code and status without provider detail in the body (put a secret-looking string in the patched exception and assert it is absent from the response and the logs); REST: anonymous -> 401, no consent -> 403 `consent_required`, success returns receipts, nothing is stored (no new rows in any table, no files), malformed `context` -> 400; the fake reader works under `ENV_TYPE="test"` and is refused under `"production"`; the fingerprint is stable for the same image, differs for different images, `""` for garbage; the default model has a non-zero price in `ai_prices.price_usage`.

### Invariants

Consent is checked before the file is touched. The provider receives only the prepared image or text plus the prompt, never the user's file name or identity. Nothing is stored by the core except the consent row. No provider detail reaches a client or a log. The extension can never replace a core field, and only ids from the app's list can come back as accounts. The module is inert unless installed.

### Preamble — REQUIRED block

> The text above is the COMPLETE spec — the committed WO file's content, not a plan to refine; there
> is no separate plan file. Read the nearest `AGENTS.md` and the app `MEMORY.md` ONLY for conventions.
> Stay in scope; do not touch auth/permissions/deps/schema/CI unless the spec says so; do not update
> `MEMORY.md`. **Do NOT edit `WORK_ORDERS.md` — the register row and the review verdicts are the
> orchestrator's alone.** **Your tools are for editing source and test files and for running the tests
> you wrote — nothing else.** Do NOT install dependencies, touch a lockfile, run a package manager, or
> tidy up stray files; if something in the repo state blocks you, stop and report it as
> `RESULT: BLOCKED <reason>` instead of fixing it. Do NOT `git add`/`commit`/`push` — leave every
> change uncommitted in the working tree for the orchestrator's independent review. WRITE the tests
> the `Required tests` section calls for AND **RUN the tests you just wrote** to confirm they execute
> and pass — that is the ONLY test run you do (NOT the affected/full suite, NOT any review). The
> orchestrator re-runs the authoritative set + does the independent review after you finish.
>
> Narrate continuously: a `PLAN: <step1> | <step2> | …` line up front, then a single-line
> `PROGRESS: [<n>/<total>] <present-tense action>` before every relevant action (and `… done` on
> completion), spaced so no gap exceeds ~2 min, stdout unbuffered, plus exactly one final
> `RESULT: DONE|BLOCKED <reason>`. Use explicit UTF-8 for all file I/O.

# C. Orchestrator only — not addressed to the implementer

> **If this WO was dispatched to you as an implementer: STOP here.**

Gate #1 open until recorded in the register row. Minor release. `UCM-RCPT-1` can be built against this contract in parallel; the consumer orders start after both are published. Reviews include `sec_reviewer` (privacy, new provider path).
