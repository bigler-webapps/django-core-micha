# Receipt recognition (`django_core_micha.receipts`)

Opt-in module (label `dcm_receipts`) on top of `django_core_micha.extraction`. Install: add `django_core_micha.receipts` to `INSTALLED_APPS`, install `django-core-micha[ai,extraction,receipts]` and migrate (one table, the consent). Without the app there is no table and no route. The module stores nothing else: neither files nor results.

## What it does

`read_receipts(user, content, *, context=None, extension=None)` reads one file that holds one or several receipts and returns normalised receipt dicts. Order of checks, each before the next: authenticated user, standing consent, size, type, optional daily count, file preparation, consent again (a revocation that arrived while the file was prepared), provider call through `extract_document` (budget reservation and the spend cap apply to every reading), JSON salvage, normalisers, extension normaliser, account filter, "nothing recognised".

Core fields of a receipt: `merchant`, `date` (ISO or empty), `total` (absolute value, two places, or empty), `currency` (default `CHF`), `payment_method` (`cash|card|other|unknown`), `items` (`description`, `amount`, `box`), `vat` (`rate`, `amount`), `box` (`x,y,w,h`, 0-1, `null` when unknown), `confidence` (`high|low`), and `account_suggestions` (`account_id`, `confidence`) only when the app configured the accounts hook. The provider schema is strict (every property required, `additionalProperties: false`, no type arrays; an unknown value is an empty string, an unknown box is zeros that the normaliser turns into `null`).

## App seams

- `DCM_RECEIPTS_EXTENSION`: dotted path to an object with `properties` (JSON-schema fragments added to every receipt), `prompt` (appended after the core rules, never replacing them) and `normalise(receipt, context) -> receipt` (runs after the core normalisers; whatever it does to a core field is discarded). Naming a core property is a configuration error. The `extension=` argument wins over the setting.
- `DCM_RECEIPTS_ACCOUNTS_HOOK`: dotted path to `fn(user, context) -> [{"id", "name"}]` (at most 200). The prompt lists them; only ids from that list come back, at most three per receipt. A failing hook means no suggestion, never a failed reading. `context` is whatever the client sent to the endpoint: the hook must validate it.
- Settings: `DCM_RECEIPTS_PROVIDER` (`openai`), `DCM_RECEIPTS_MODEL` (`gpt-6-luna`, central default, rate-table row exists), `DCM_RECEIPTS_EFFORT` (`low`), `DCM_RECEIPTS_MAX_OUTPUT_TOKENS` (4000), `DCM_RECEIPTS_DAILY_LIMIT` (unset = no count limit; N refuses the N+1th reading of a user per day, atomically; a failed provider call keeps its count), `DCM_RECEIPTS_READER` (`live`, or `fake`).
- Keys: `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` as for the assistant.

## Consent

`ReceiptConsent` (one row per user; `granted_at`, `revoked_at`, `source`). Active when granted and not revoked after. Every change is audited through the audit log (`dcm_receipts.receiptconsent.*`). `consent.grant/revoke/state/has_consent`; `consent.import_consents([(user, granted_at), ...], source=...)` for the consumer migrations: idempotent, never re-grants someone who revoked later, never lowers a grant. Recognition without consent is refused (`consent_required`) before the file is read.

## REST (the app includes `django_core_micha.receipts.urls`)

Session auth, CSRF enforced, every response `Cache-Control: no-store`, errors are `{"code": ...}` only.

- `GET|POST|DELETE consent/` -> `{"granted", "granted_at", "revoked_at"}` (POST grants, DELETE revokes).
- `POST read/` multipart: `file`, optional `context` (JSON object string) -> `{"receipts": [...]}`.

| code | status |
|---|---|
| `authentication_required` | 401 |
| `consent_required` | 403 |
| `invalid_request` | 400 |
| `file_too_large` | 413 |
| `unsupported_file_type` | 415 |
| `nothing_recognised` | 422 |
| `spend_cap_reached`, `count_limit_reached` | 429 |
| `provider_unavailable` | 503 |

Provider text, exception text and file names never reach the client or the log (class names only).

## Files

Type is sniffed from the bytes, not the name: JPEG, PNG, PDF, HEIC/HEIF, up to 10 MB (`file_too_large` is also answered from the declared `Content-Length` before the multipart body is parsed; the deployment's proxy should still cap request bodies). Images larger than 40 megapixels are refused before decoding. HEIC is converted by `pillow-heif` and everything is resized and sent as JPEG. A PDF with a text layer sends the text; a PDF without goes through the upload path of the extraction module under the neutral name `receipt.pdf`. The provider never receives the user's file name or identity.

## Duplicate fingerprint

`fingerprint.perceptual_hash(bytes)` is a 64-bit difference hash (Pillow only), `hamming_distance`, `is_probable_duplicate(a, b, threshold=5)`.

## Fake reader

`DCM_RECEIPTS_READER = "fake"` returns deterministic receipts without a provider (two receipts when the content starts with `FAKE-MUL`). It is refused unless `ENV_TYPE` is `local` or `test` (system check `dcm_receipts.E001` and a runtime refusal) and runs the same consent, type, size and count gates.

## Known limits

Consent is checked twice (at the start and right before the provider call), not held as a lock across the call: a revocation in the last milliseconds can still let one call through. PDF text extraction is bounded by the extraction module's character cap, not by page count.
