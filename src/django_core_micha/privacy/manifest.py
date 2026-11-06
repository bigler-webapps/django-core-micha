"""Strict validation and public-safe projection of the host privacy manifest."""

import hashlib
import json
import re
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import EmailValidator


APP_SLUG = re.compile(r"[a-z0-9_-]{1,80}", re.ASCII)
PURPOSE_ID = re.compile(r"[a-z][a-z0-9_-]{1,40}", re.ASCII)
DOC_ID = re.compile(r"[a-z0-9_-]{1,40}", re.ASCII)
VERSION = re.compile(r"[A-Za-z0-9._-]{1,64}", re.ASCII)
LOCALE = re.compile(r"[a-z]{2}(?:-[A-Z]{2})?", re.ASCII)
STORAGE_KINDS = {"cookie", "localStorage", "sessionStorage", "indexedDB", "none"}
CHANNELS = {"banner", "settings", "other"}


def _object(value, required, optional, context, errors):
    if not isinstance(value, dict):
        errors.append(f"{context} must be an object.")
        return False
    keys = set(value)
    if not all(isinstance(key, str) for key in keys):
        errors.append(f"{context} keys must be strings.")
        return False
    unknown = keys - set(required) - set(optional)
    missing = set(required) - keys
    if unknown:
        errors.append(f"{context} contains unknown keys.")
    if missing:
        errors.append(f"{context} is missing required keys.")
    return not unknown and not missing


def _text(value, maximum, context, errors, allow_empty=False):
    valid = (
        isinstance(value, str)
        and len(value) <= maximum
        and (allow_empty or bool(value.strip()))
        and not any(ord(character) < 32 for character in value)
    )
    if not valid:
        errors.append(f"{context} must be text of at most {maximum} characters.")
        return None
    return value


def _string_list(value, maximum_item, context, errors, maximum_count=50):
    if not isinstance(value, list) or len(value) > maximum_count:
        errors.append(f"{context} must be a list of at most {maximum_count} strings.")
        return None
    items = [_text(item, maximum_item, context, errors) for item in value]
    if len(set(item for item in items if item is not None)) != len(items):
        errors.append(f"{context} must not contain duplicates.")
    return items


def _locale_map(value, locales, maximum, context, errors):
    if not isinstance(value, dict):
        errors.append(f"{context} must be an object keyed by every declared locale.")
        return None
    locale_keys = {locale for locale in locales if isinstance(locale, str)}
    if set(value) != locale_keys or len(locale_keys) != len(locales):
        errors.append(f"{context} must contain exactly the declared locales.")
    result = {}
    for locale in locales:
        if isinstance(locale, str) and locale in value:
            result[locale] = _text(value[locale], maximum, f"{context}.{locale}", errors)
    return result


def _valid_document_reference(reference):
    if not isinstance(reference, str) or not reference or len(reference) > 500:
        return False
    if "\\" in reference or any(ord(character) < 32 for character in reference):
        return False
    try:
        parsed = urlsplit(reference)
        _ = parsed.port
        if parsed.scheme in ("http", "https"):
            return bool(parsed.hostname) and parsed.username is None and parsed.password is None
        return (
            not parsed.scheme
            and not parsed.netloc
            and reference.startswith("/")
            and not reference.startswith("//")
        )
    except ValueError:
        return False


def _valid_origin(origin):
    if not isinstance(origin, str) or not origin or len(origin) > 300 or "\\" in origin:
        return False
    try:
        parsed = urlsplit(origin)
        _ = parsed.port
        return (
            parsed.scheme in ("http", "https")
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and not parsed.path
            and not parsed.query
            and not parsed.fragment
            and parsed.netloc == parsed.netloc.lower()
            and origin == f"{parsed.scheme}://{parsed.netloc}"
        )
    except ValueError:
        return False


def validate_manifest_setting(raw):
    """Return ``(normalized_manifest, errors)`` without exposing raw values in errors."""
    errors = []
    required_keys = {
        "app", "site", "locales", "controller", "notice", "choice_validity_days",
        "evidence_retention_days", "purposes",
    }
    if not _object(raw, required_keys, {"origins"}, "DCM_PRIVACY", errors):
        return None, errors

    app = raw["app"]
    site = raw["site"]
    if not isinstance(app, str) or not APP_SLUG.fullmatch(app):
        errors.append("DCM_PRIVACY app must be a lower-case app slug.")
    if not isinstance(site, str) or not APP_SLUG.fullmatch(site):
        errors.append("DCM_PRIVACY site must be a lower-case site slug.")

    locales = raw["locales"]
    if not isinstance(locales, list) or not locales or len(locales) > 20:
        errors.append("DCM_PRIVACY locales must be a non-empty list of at most 20 locales.")
        locales = []
    else:
        all_locales_are_strings = all(isinstance(locale, str) for locale in locales)
        if any(not isinstance(locale, str) or not LOCALE.fullmatch(locale) for locale in locales):
            errors.append("DCM_PRIVACY locales contain an invalid locale.")
        if all_locales_are_strings and len(set(locales)) != len(locales):
            errors.append("DCM_PRIVACY locales must be unique.")

    origins = None
    if "origins" in raw:
        raw_origins = raw["origins"]
        if not isinstance(raw_origins, list) or len(raw_origins) > 50:
            errors.append("DCM_PRIVACY origins must be a list of at most 50 exact origins.")
            origins = []
        else:
            origins = list(raw_origins)
            if any(not _valid_origin(origin) for origin in origins):
                errors.append("DCM_PRIVACY origins must be exact HTTP(S) origins without paths.")
            if len(set(origin for origin in origins if isinstance(origin, str))) != len(origins):
                errors.append("DCM_PRIVACY origins must be unique.")

    controller_raw = raw["controller"]
    controller = {}
    if _object(controller_raw, {"name", "contact"}, set(), "DCM_PRIVACY controller", errors):
        name = _text(controller_raw["name"], 200, "controller.name", errors)
        contact = _text(controller_raw["contact"], 200, "controller.contact", errors)
        if contact is not None:
            valid_contact = False
            if "@" in contact:
                try:
                    EmailValidator()(contact)
                    valid_contact = True
                except ValidationError:
                    pass
            if not valid_contact:
                try:
                    parsed_contact = urlsplit(contact)
                    _ = parsed_contact.port
                    _ = parsed_contact.port
                    valid_contact = (
                        parsed_contact.scheme in ("http", "https")
                        and bool(parsed_contact.hostname)
                        and parsed_contact.username is None
                        and parsed_contact.password is None
                    )
                except ValueError:
                    valid_contact = False
            if not valid_contact:
                errors.append("controller.contact must be an email address or HTTP(S) URL.")
        controller = {"name": name, "contact": contact}

    notice_raw = raw["notice"]
    notice = {}
    if _object(notice_raw, {"version", "documents"}, set(), "DCM_PRIVACY notice", errors):
        version = notice_raw["version"]
        if not isinstance(version, str) or not VERSION.fullmatch(version):
            errors.append("notice.version is invalid.")
        documents_raw = notice_raw["documents"]
        documents = {}
        if not isinstance(documents_raw, dict) or not documents_raw or len(documents_raw) > 20:
            errors.append("notice.documents must contain between 1 and 20 documents.")
        else:
            for document_id, locale_refs in documents_raw.items():
                if not isinstance(document_id, str) or not DOC_ID.fullmatch(document_id):
                    errors.append("notice document IDs must be lower-case document slugs.")
                    continue
                locale_keys = {locale for locale in locales if isinstance(locale, str)}
                if (
                    not isinstance(locale_refs, dict)
                    or set(locale_refs) != locale_keys
                    or len(locale_keys) != len(locales)
                ):
                    errors.append(f"notice document {document_id} must reference every declared locale.")
                    continue
                refs = {}
                for locale in locales:
                    reference = locale_refs.get(locale)
                    if not _valid_document_reference(reference):
                        errors.append(f"notice document {document_id}.{locale} has an unsafe reference.")
                    else:
                        refs[locale] = reference
                documents[document_id] = refs
        notice = {"version": version, "documents": documents}

    validity = raw["choice_validity_days"]
    retention = raw["evidence_retention_days"]
    if type(validity) is not int or not 1 <= validity <= 400:
        errors.append("choice_validity_days must be an integer from 1 to 400.")
    if type(retention) is not int or not 1 <= retention <= 1095:
        errors.append("evidence_retention_days must be an integer from 1 to 1095.")
    if type(validity) is int and type(retention) is int and retention < validity:
        errors.append("evidence_retention_days must be at least choice_validity_days.")

    raw_purposes = raw["purposes"]
    if not isinstance(raw_purposes, list) or len(raw_purposes) > 20:
        errors.append("purposes must be a list of at most 20 entries.")
        raw_purposes = []
    purposes = []
    purpose_ids = set()
    service_ids = set()
    storage_owners = {}
    for index, purpose_raw in enumerate(raw_purposes):
        context = f"purposes[{index}]"
        purpose_required = {"id", "revision", "required", "label", "summary", "services", "withdrawal_clears"}
        purpose_optional = {"justification"}
        if not _object(purpose_raw, purpose_required, purpose_optional, context, errors):
            continue
        purpose_id = purpose_raw["id"]
        if not isinstance(purpose_id, str) or not PURPOSE_ID.fullmatch(purpose_id):
            errors.append(f"{context}.id is invalid.")
        elif purpose_id in purpose_ids:
            errors.append("purpose IDs must be unique.")
        else:
            purpose_ids.add(purpose_id)
        revision = purpose_raw["revision"]
        if type(revision) is not int or revision < 1:
            errors.append(f"{context}.revision must be an integer of at least 1.")
        required = purpose_raw["required"]
        if type(required) is not bool:
            errors.append(f"{context}.required must be boolean.")
            required = False
        justification = None
        if required:
            justification = _text(purpose_raw.get("justification"), 500, f"{context}.justification", errors)
        elif "justification" in purpose_raw:
            errors.append(f"{context}.justification is only allowed for required purposes.")

        label = _locale_map(purpose_raw["label"], locales, 80, f"{context}.label", errors)
        summary = _locale_map(purpose_raw["summary"], locales, 400, f"{context}.summary", errors)
        raw_services = purpose_raw["services"]
        if not isinstance(raw_services, list) or len(raw_services) > 10:
            errors.append(f"{context}.services must contain at most 10 services.")
            raw_services = []
        services = []
        purpose_storage = set()
        for service_index, service_raw in enumerate(raw_services):
            service_context = f"{context}.services[{service_index}]"
            service_keys = {
                "id", "provider", "trigger", "data_categories", "recipients", "transfers",
                "browser_storage", "server_retention",
            }
            if not _object(service_raw, service_keys, set(), service_context, errors):
                continue
            service_id = service_raw["id"]
            if not isinstance(service_id, str) or not APP_SLUG.fullmatch(service_id):
                errors.append(f"{service_context}.id is invalid.")
            elif service_id in service_ids:
                errors.append("service IDs must be unique across the manifest.")
            else:
                service_ids.add(service_id)
            provider = _text(service_raw["provider"], 120, f"{service_context}.provider", errors)
            trigger = _text(service_raw["trigger"], 200, f"{service_context}.trigger", errors)
            categories = _string_list(service_raw["data_categories"], 80, f"{service_context}.data_categories", errors)
            recipients = _string_list(service_raw["recipients"], 120, f"{service_context}.recipients", errors)
            transfers = _string_list(service_raw["transfers"], 120, f"{service_context}.transfers", errors)
            server_retention = _text(service_raw["server_retention"], 120, f"{service_context}.server_retention", errors)
            raw_storage = service_raw["browser_storage"]
            if not isinstance(raw_storage, list) or len(raw_storage) > 30:
                errors.append(f"{service_context}.browser_storage must be a list of at most 30 entries.")
                raw_storage = []
            browser_storage = []
            for storage_index, storage_raw in enumerate(raw_storage):
                storage_context = f"{service_context}.browser_storage[{storage_index}]"
                if not _object(storage_raw, {"kind", "name", "scope", "lifetime"}, set(), storage_context, errors):
                    continue
                kind = storage_raw["kind"]
                if not isinstance(kind, str) or kind not in STORAGE_KINDS:
                    errors.append(f"{storage_context}.kind is invalid.")
                storage_name = _text(storage_raw["name"], 80, f"{storage_context}.name", errors)
                scope = _text(storage_raw["scope"], 120, f"{storage_context}.scope", errors)
                lifetime = _text(storage_raw["lifetime"], 60, f"{storage_context}.lifetime", errors)
                if storage_name is not None and kind != "none":
                    purpose_storage.add(storage_name)
                    prior_owner = storage_owners.get(storage_name)
                    if prior_owner is not None and prior_owner != purpose_id:
                        errors.append("a browser storage name may be owned by only one purpose.")
                    storage_owners[storage_name] = purpose_id
                browser_storage.append({"kind": kind, "name": storage_name, "scope": scope, "lifetime": lifetime})
            services.append({
                "id": service_id, "provider": provider, "trigger": trigger,
                "data_categories": categories, "recipients": recipients, "transfers": transfers,
                "browser_storage": browser_storage, "server_retention": server_retention,
            })

        clears = _string_list(purpose_raw["withdrawal_clears"], 80, f"{context}.withdrawal_clears", errors, maximum_count=100)
        if clears is not None and any(name not in purpose_storage for name in clears):
            errors.append(f"{context}.withdrawal_clears must be owned browser storage names for this purpose.")
        purpose = {
            "id": purpose_id, "revision": revision, "required": required,
            "label": label, "summary": summary, "services": services,
            "withdrawal_clears": clears,
        }
        if required:
            purpose["justification"] = justification
        purposes.append(purpose)

    if errors:
        return None, errors
    return {
        "app": app, "site": site, "origins": origins, "locales": list(locales),
        "controller": controller, "notice": notice, "choice_validity_days": validity,
        "evidence_retention_days": retention, "purposes": purposes,
    }, []


def current_manifest():
    """Return validated configuration, or ``None`` when absent or invalid."""
    manifest, errors = validate_manifest_setting(getattr(settings, "DCM_PRIVACY", None))
    return None if errors else manifest


def public_manifest_from_config(manifest):
    purposes = []
    for purpose in manifest["purposes"]:
        public_purpose = {
            "id": purpose["id"], "revision": purpose["revision"],
            "required": purpose["required"], "label": purpose["label"],
            "summary": purpose["summary"], "services": purpose["services"],
            "withdrawal_clears": purpose["withdrawal_clears"],
        }
        if purpose["required"]:
            public_purpose["justification"] = purpose["justification"]
        purposes.append(public_purpose)
    payload = {
        "app": manifest["app"], "site": manifest["site"],
        "locales": manifest["locales"], "controller": manifest["controller"],
        "notice": manifest["notice"], "choice_validity_days": manifest["choice_validity_days"],
        "purposes": purposes,
        "necessary_storage": [{
            "kind": "cookie", "name": f"dcm_privacy_{manifest['site']}",
            "scope": "host-only; path=/", "lifetime": f"{manifest['choice_validity_days']} days",
        }],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    payload["config_version"] = hashlib.sha256(canonical).hexdigest()
    return payload


def public_manifest():
    manifest = current_manifest()
    return public_manifest_from_config(manifest) if manifest is not None else None
