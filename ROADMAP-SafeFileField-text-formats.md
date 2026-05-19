# Roadmap — `SafeFileField` text-format validators (planned for 2.9.5)

> Status: **planned, not implemented**. Captured here so the design is not
> lost between sessions. Drives the hram `MapLayer.vector_file` migration.

## Why

`SafeFileField` today validates uploads through `filetype.guess`, which is a
magic-byte detector. It is reliable for binary formats (images, PDFs, …) but
returns `None` for ASCII/UTF-8 text formats such as JSON, GeoJSON and XML.
This means structured text uploads cannot pass `allowed_mimes` validation and
the field cannot be used for `MapLayer.vector_file` (GeoJSON vector layers)
or similar text-payload fields.

## Scope

### Library (this repo)

1. `validators/upload.py` — add helpers
   - `validate_geojson(file_obj)` — read content, `json.loads`, assert
     `type in {"FeatureCollection", "Feature", "Geometry"}`, restore seek.
   - `validate_xml(file_obj)` — parse with `defusedxml.ElementTree.iterparse`
     as smoke test, hardened against XXE. Adds a runtime dependency on
     `defusedxml` (Approval-Gate: dependency change).
2. `validators/upload.py` — extend `validate_upload`
   - Accept an optional `text_validators: Mapping[str, Callable[[Any], None]]`.
   - When `detect_mime` returns `None` and `text_validators` is non-empty,
     iterate through the validators; the first one that does not raise
     `ValidationError` "wins" and its key is treated as the detected MIME.
   - Binary detection still takes precedence; text-validators are a fallback,
     not a parallel path.
3. `fields/safe_file.py` — extend `SafeFileField`
   - **Migration-safe API**: register validators in a module-level constant
     (`TEXT_VALIDATORS = {"application/geo+json": validate_geojson, …}`) and
     let the field accept `text_validator_keys: Iterable[str] | None`. The
     keys are deconstruct-safe; the callables are looked up at runtime.
   - Plain callable API was considered and rejected because Django
     migrations cannot reliably serialise arbitrary functions.

### Tests (this repo)

- valid GeoJSON `FeatureCollection`, `Feature`, `Geometry`
- malformed JSON → rejected
- valid JSON without `type` → rejected
- valid XML smoke test
- XXE payload rejected (defusedxml protection)
- combined `allowed_mimes` + `text_validator_keys`: binary detection still
  wins when present; text fallback only activates when `detect_mime` is `None`.
- `deconstruct()` round-trip with `text_validator_keys` produces stable
  migration output.

### Release

- Bump version 2.9.x → **2.9.5** (or current head + 1).
- Update `CHANGELOG.md` / equivalent.
- Tag and push (release-coordination, Approval-Gate).

### hram migration (separate session)

- `MapLayer.vector_file`: `FileField` → `SafeFileField(allowed_mimes={"application/json"}, text_validator_keys=["application/geo+json"], max_size=…)`.
- Migration generated and reviewed.
- `backend/requirements.txt`: bump `django-core-micha` to `==2.9.5`.
- Existing rows / fixtures audited for compatibility with stricter validation.

## Approval gates

| Aspect | Type | Approval |
|---|---|---|
| `defusedxml` runtime dependency | dependency change | required |
| `SafeFileField` API surface | security / file upload | required |
| Release 2.9.5 of `django-core-micha` | dependency version (downstream) | required |
| hram `MapLayer` migration | DB schema | required |

## Priority

**Low.** `MapLayer.vector_file` in hram is admin-only (`IsEngineAdmin`); the
exposed threat surface is small. This roadmap can wait for the per-app
hardening phase of the broader file-upload security work.

## Out of scope

- Other text formats (CSV, TSV, YAML). Add only on concrete need.
- Streaming validation for very large files. Current design loads the whole
  payload — acceptable for boundary geojsons and small vector layers, not
  for raster data.
- Frontend client-side preview / lint of GeoJSON. Server-side validation is
  the source of truth.
