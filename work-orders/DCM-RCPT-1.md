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

*To be filled by the Orchestrator.*

# C. Orchestrator only — not addressed to the implementer

> **If this WO was dispatched to you as an implementer: STOP here.**

Gate #1 open until recorded in the register row. Minor release. `UCM-RCPT-1` can be built against this contract in parallel; the consumer orders start after both are published. Reviews include `sec_reviewer` (privacy, new provider path).
