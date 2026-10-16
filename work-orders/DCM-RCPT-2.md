# DCM-RCPT-2 — Receipt reading sees the request context, and the fallback currency is per app

Prepared: 2026-10-17. Target repository: `django-core-micha` (`main`). Correction to `DCM-RCPT-1` (3.6.0), found at the acceptance of `reimbursements/RMB-RCPT-1`. Consumer: `reimbursements/RMB-RCPT-2`.

# A. Envelope — addressed to everyone

## Goal and expected outcome

Two properties of the receipt module put wrong money values into a multi-currency app such as reimbursements:
1. **No per-request hint:** the app's extension prompt is static (`DCM_RECEIPTS_EXTENSION`). A reading through the REST endpoint therefore cannot tell the model the target currency of the claim being filled. The server-side reads in reimbursements do tell it. On a receipt that prints both a foreign amount and a CHF amount, the model can put the wrong one into the original amount.
2. **CHF fallback:** an unreadable currency becomes `CHF` for every app (`DCM-RCPT-1`'s core schema, "default CHF"). That suits a CHF-only app (spesix). In an app with foreign-currency travel, a silently set currency produces a wrong amount.

Expected outcome:
- The extension prompt can use the request's context, after the app's validation.
- The fallback currency is an app setting, including "none", where an unreadable currency stays empty.
- Apps that set nothing behave as in 3.6.0.

## Operator decisions this Envelope records (2026-10-17)

- Both corrections go into the core, not into reimbursements alone.
- spesix keeps CHF as its fallback.

## Scope

- **Context in the extension prompt:**
  - The app's extension can supply its prompt text per reading. It receives the request's context after the app's own validation of it, and the requesting user.
  - A context the validation refuses ends the reading with `invalid_request`, before any provider call.
  - The static form of `DCM_RECEIPTS_EXTENSION` keeps working.
- **Fallback currency:**
  - A setting names the currency used when the receipt's currency is unreadable.
  - The default stays `CHF` (3.6.0 behaviour).
  - A value of "none" leaves the currency empty.
- **Documentation:** `docs/design/receipts.md` and the `CHANGELOG`.

## Non-goals

- Other schema or normaliser changes.
- Changes in ui-core-micha. Its capture card already sends a host-chosen `context`.

## Tier

Tier 3: a change inside shared-core on a money path that adds a client-influenced input to a provider prompt.

## Risks

- **Prompt injection through the context:** the context is user-supplied. The app's validation is the only control. The extension decides which validated values it puts into the prompt, never the raw context.

## Required tests to write

- Context:
  - an extension that uses the validated context gets its text into the prompt of that reading only;
  - a refused context ends the reading with `invalid_request` and no provider call;
  - the static extension still works.
- Fallback currency:
  - default `CHF` unchanged;
  - a set fallback currency is used;
  - "none" leaves the currency empty.

# B. Implementation map — addressed to the implementer

Work from this package; do not explore broadly. Part A is the authoritative spec; this map fixes names, shapes and the order of checks. One dispatch; complete, no stubs. A client-supplied value goes into a provider prompt here: the extension decides which VALIDATED values enter the prompt, the raw request context never does.

### Target repo working directory (absolute)

`C:\Users\Micha Bigler\Documents\webapps\django-core-micha` (branch `main`). Never the workspace root. Do not touch other repositories, `pyproject.toml`, `CHANGELOG.md`, `docs/`, `WORK_ORDERS.md` (docs, version and register are the Orchestrator's), `extraction/*`, `billing/*`, `assistant/*`. Run tests with the interpreter named in your dispatch note.

### Context package (3.6.0 module, paths under `src/django_core_micha/receipts/`)

- `reader.py`: `read_receipts(user, content, *, context=None, extension=None)`. Current order: authenticated -> `has_consent` -> empty/size/`sniff_type` -> `check_and_increment` -> `resolve_extension(extension)` and `get_accounts(user, context)` -> inside one `try`: `prepare`, reader mode check (`live`/`fake`), consent re-check, `build_prompts(ext, accounts)`, `ExtractionRequest(... schema=build_schema(ext, bool(accounts)) ...)`, `extract_document` -> after the try: `normalise_receipts(rows)` (called WITHOUT a default currency, so `normalise.currency(value, default="CHF")` falls back to `"CHF"`), the extension `normalise(row, context)` step (core fields re-applied afterwards), account filter. Errors are `ReceiptError(code)`; a generic exception inside the provider try becomes `provider_unavailable`.
- `normalise.py`: `currency(value, default="CHF")` returns the three uppercase letters of a valid value, else `default`; `normalise_receipt(r, default_currency="CHF")` and `normalise_receipts(rows, default_currency="CHF")` already take a default. `schema.py`: `resolve_extension`, `build_schema(extension, with_accounts)`; an extension object has `properties`, optional static `prompt`, optional `normalise(row, context)`. `prompt.py`: `build_prompts(extension, accounts)` returns `(system, user)`; the system text says "CHF is the default currency"; the extension `prompt` is appended under "App-specific fields:". `views.py`: `read_view` parses the multipart `context` as a JSON object (400 `invalid_request` when malformed or not an object) and calls `read_receipts(request.user, bytes, context=context)`. `errors.py`: `ReceiptError`, code `invalid_request` is 400. `apps.py` holds the system checks `dcm_receipts.E001`/`E002`.
- Tests to model on: `tests/test_receipts.py` (helpers `image_bytes`, `payload`, `fake_result`, `user`, `grant`, monkeypatching `reader.extract_document`), `tests/conftest.py`.

### Fixed design

1. **Per-reading extension prompt with validated context.** An extension object may define two OPTIONAL methods, both called by the reader and never by the client:
   - `validate_context(context, user)` -> the validated value (any object, typically a small dict). It may raise ANY exception to refuse. The reader calls it exactly once per reading, with the request context as the endpoint (or the Python caller) supplied it and the requesting user.
   - `prompt_for(validated, user)` -> a string (or empty) appended to the prompt of THAT reading only, after the static `prompt` (if any), under the same "App-specific fields:" heading; stripped, at most 2000 characters (longer is cut, not an error), control characters other than newline/tab removed. It receives only the validated value, never the raw context.
   An extension with neither method behaves exactly as in 3.6.0 (the static `prompt` form, and `context` passed through as today). When `validate_context` exists, EVERY later consumer of the context in that reading gets the validated value instead of the raw one: `prompt_for`, `get_accounts(user, validated)` and `ext.normalise(row, validated)`. When it does not exist they get the raw context as before. An extension that defines `prompt_for` but no `validate_context` gets the raw context in `prompt_for` (documented as the app's responsibility); the docs recommend always defining both.
2. **Order and refusal.** After the file checks (`sniff_type`) and BEFORE `check_and_increment` and before `prepare` and any provider call, the reader runs `validate_context`; a refusal (any exception from it, logged by class name only, never the message) raises `ReceiptError("invalid_request")` (HTTP 400 through the existing mapping) and consumes no count and never reaches the provider. The static-extension and no-extension paths skip this step. Call `prompt_for` once, when the prompts are built (not at validation time), inside the existing provider-error handling: an exception from `prompt_for` is logged by class name and the reading proceeds WITHOUT that dynamic text (same softness as the extension normaliser), never a crash and never raw context in the prompt. The dynamic text of one reading must not leak into another: no caching, no mutation of the extension object.
3. **Fallback currency setting.** `DCM_RECEIPTS_FALLBACK_CURRENCY`, read at call time, default `"CHF"`. A three-letter ASCII code in any case is normalised to upper case; the string `"none"` in any case means "no fallback": an unreadable currency stays an empty string. Any other value is a configuration error: `dcm_receipts.E003` system check, and at reading time `ReceiptError("provider_unavailable")` (logged by class name) rather than a silent CHF. The resolved fallback is passed to `normalise_receipts(rows, default_currency)` (the empty string for "none"; `currency()` already returns `default` unchanged, make sure the empty default is not replaced by `"CHF"` anywhere, including the `or "CHF"` style shortcuts). A valid currency printed on the receipt always wins; the fallback only replaces an unreadable/empty/invalid one.
4. **Prompt wording follows the setting.** `build_prompts` takes the fallback (keyword `fallback_currency`) and the dynamic text: with a currency fallback the system text keeps saying that code is the default currency (for CHF the sentence stays word for word as in 3.6.0, so an app that sets nothing sends an unchanged prompt); with `"none"` the sentence is replaced by "If the currency is not printed or not readable, leave it empty." Other Swiss-focus sentences stay.
5. **No other change:** schema, normalisers, consent, limits, REST contract, error codes, fake reader (it keeps its fixed `CHF` currency, documented as a test fixture) are untouched. The REST endpoint needs no change: it already passes the parsed `context` object to `read_receipts`, and a malformed/non-object `context` is still 400 before. `ui-core-micha` is not touched.

### Tests (new file `tests/test_receipts_context_currency.py`; existing tests stay green)

Context: an extension with `validate_context` and `prompt_for` gets the validated value (not the raw context) into the system prompt of that reading only (capture the `ExtractionRequest` passed to the patched `extract_document`; a second reading with another context shows the second text and none of the first); the static `prompt` form still works and is combined with `prompt_for` in order; a refused context (validation raises) answers `invalid_request` 400 through `read_view`, never calls `extract_document` or `prepare`, does not consume the daily count (set `DCM_RECEIPTS_DAILY_LIMIT=1`, refused call, then a valid reading still succeeds) and the exception text does not reach the response or the log; `get_accounts` and `ext.normalise` receive the validated value; an exception in `prompt_for` does not fail the reading and adds no text; an over-long `prompt_for` text is cut at 2000 characters and control characters are removed; an extension without the new methods behaves as before (raw context to normalise); the raw request context never appears in the prompt unless the extension put it there (assert with a unique marker in the context). Fallback currency: default `CHF` for an unreadable currency (unchanged), `DCM_RECEIPTS_FALLBACK_CURRENCY="eur"` gives `EUR`, `"none"` (also `"NONE"`) leaves `""`, a currency printed on the receipt wins in all three, an invalid setting value (`"swiss"`, `""`, `123`) -> `provider_unavailable` and the system check `dcm_receipts.E003`, the prompt sentence follows the setting (CHF sentence unchanged, EUR sentence, none sentence).

### Invariants

The raw context never enters a prompt; a refused context never reaches the provider or the counter; the dynamic prompt text cannot leak across readings; an unreadable currency is never silently turned into a different currency than the app configured; apps that set nothing behave as in 3.6.0.

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

Gate #1 open until recorded in the register row. Patch or minor release; `reimbursements/RMB-RCPT-2` pins it.
