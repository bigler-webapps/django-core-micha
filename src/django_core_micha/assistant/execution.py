"""Fail-closed contracts for provider-hosted Assistant code execution.

The consuming application remains the authority for whether a requester may
transfer a particular dataset. This module only accepts that decision through
the trusted server-side hook and applies the shared transport and size limits.
"""
import csv
import hashlib
import io
import inspect
import json
import logging
import math
import re
from dataclasses import dataclass, field

from django.conf import settings

from .drivers.base import DriverError
from .hooks import _hook
from .limits import get_limit

log = logging.getLogger(__name__)
_INPUT_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\.(?:csv|json)\Z", re.I)
_OUTPUT_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\.(?:csv|json|txt)\Z", re.I)
_MEMORY_LIMITS = {"1g", "4g", "16g", "64g"}
ANTHROPIC_RETENTION_POLICY = "code_execution_up_to_30_days_non_zdr"
# Provider documentation confirms these exact model/tool combinations. Unknown
# identifiers remain disabled until their support has been independently verified.
VERIFIED_EXECUTION_MODELS = {
    "openai_api": {"gpt-5"},
    "anthropic_api": {"claude-haiku-4-5"},
}


@dataclass(frozen=True)
class Dataset:
    name: str
    data_class: str
    content: bytes
    media_type: str


@dataclass(frozen=True)
class ExecutionConfig:
    provider: str
    model: str
    tool_version: str
    memory_limit: str
    max_execution_seconds: int
    max_continuations: int
    reserved_usd_micros: int
    datasets: tuple[Dataset, ...]
    session: object = field(default_factory=lambda: ExecutionSession())


@dataclass
class ExecutionSession:
    """Provider references kept only for the lifetime of one Assistant turn."""
    container_id: str | None = None
    input_file_ids: list = field(default_factory=list)
    container_file_ids: list = field(default_factory=list)
    provider_file_ids: list = field(default_factory=list)
    dataset_manifest: dict = field(default_factory=dict)
    output_file_count: int = 0
    output_bytes: int = 0
    reservation: object = None
    reservation_attempted: bool = False
    possible_charge: bool = False
    cost_reported: bool = False
    execution_records: set = field(default_factory=set)
    completed_execution_records: set = field(default_factory=set)
    execution_calls: int = 0
    finalized: bool = False


def _value(value, key, default=None):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _limits():
    names = {
        "max_execution_files": 8,
        "max_execution_file_bytes": 2 * 1024 * 1024,
        "max_execution_total_bytes": 8 * 1024 * 1024,
        "max_execution_output_files": 4,
        "max_execution_output_file_bytes": 2 * 1024 * 1024,
        "max_execution_output_total_bytes": 4 * 1024 * 1024,
        "max_execution_text_bytes": 64 * 1024,
        "max_execution_seconds": 300,
        "max_execution_continuations": 4,
    }
    overrides = getattr(settings, "DCM_ASSISTANT_LIMITS", {}) or {}
    result = {}
    for key, default in names.items():
        value = overrides.get(key, default)
        if type(value) is not int or value <= 0:
            raise DriverError("execution_unavailable")
        result[key] = value
    return result


def execution_scope_is_private(scope):
    """Unknown shared-scope decisions fail closed for execution result access."""
    try:
        fn = _hook("SHARED_SCOPE")
    except Exception:
        return False
    if fn is None:
        return not bool(scope)
    try:
        return not bool(fn(scope))
    except Exception:
        return False


def validate_price_table(value):
    """Validate explicit conservative per-analysis reservations (USD micros).

    Entries are keyed by ``<driver>:<model>``. These are reservations, not a
    promise that a provider account's free allowance or negotiated pricing will
    apply. Missing entries are intentionally unpriced and unavailable.
    """
    if not isinstance(value, dict):
        return False
    for key, price in value.items():
        if not isinstance(key, str) or ":" not in key or not isinstance(price, dict):
            return False
        if set(price) != {"usd_micros", "unit"}:
            return False
        if type(price["usd_micros"]) is not int or price["usd_micros"] <= 0:
            return False
        if price["unit"] not in {"openai_container_20m", "anthropic_execution_5m"}:
            return False
    return True


def minimum_execution_rate(driver, memory_limit="1g"):
    """Public-list conservative floor in USD micros for one provider billing unit."""
    if driver == "anthropic_api":
        # $0.05/hour with the published five-minute minimum, rounded upward.
        return 4_167
    return {"1g": 30_000, "4g": 120_000, "16g": 480_000, "64g": 1_920_000}.get(memory_limit, 0)


def configuration_errors(entries):
    """Startup-check explicit support and prices; absent opt-in remains valid."""
    enabled = []
    errors = []
    for row in entries:
        if "code_execution" not in row:
            continue
        support = row.get("code_execution")
        if isinstance(support, dict) and support.get("enabled") is False:
            continue
        if not isinstance(support, dict) or support.get("enabled") is not True:
            errors.append(f"Invalid code_execution configuration for {row.get('model', 'unknown')}.")
            continue
        driver = row.get("driver")
        versions = {"openai_api": {"code_interpreter"}, "anthropic_api": {"code_execution_20250825"}}
        if driver not in versions or support.get("tool_version") not in versions[driver]:
            errors.append(f"Unsupported code_execution provider or tool version for {row.get('model', 'unknown')}.")
            continue
        if support.get("memory_limit", "1g") not in _MEMORY_LIMITS:
            errors.append(f"Invalid code_execution memory limit for {row.get('model', 'unknown')}.")
        for key, ceiling in (("max_execution_seconds", 3600), ("max_continuations", 32)):
            if key in support and (type(support[key]) is not int or not 0 < support[key] <= ceiling):
                errors.append(f"Invalid code_execution {key} for {row.get('model', 'unknown')}.")
        if set(support) - {"enabled", "tool_version", "memory_limit", "max_execution_seconds", "max_continuations"}:
            errors.append(f"Unknown code_execution setting for {row.get('model', 'unknown')}.")
        enabled.append((driver, row.get("model")))
    if enabled:
        prices = getattr(settings, "DCM_ASSISTANT_EXECUTION_PRICING", None)
        if not validate_price_table(prices):
            errors.append("DCM_ASSISTANT_EXECUTION_PRICING must contain positive usd_micros and a supported unit for every execution-enabled model.")
        else:
            for driver, model in enabled:
                price = prices.get(f"{driver}:{model}")
                unit = "openai_container_20m" if driver == "openai_api" else "anthropic_execution_5m"
                if not price or price["unit"] != unit:
                    errors.append(f"Missing or mismatched execution price for {driver}:{model}.")
                else:
                    memory = next((row["code_execution"].get("memory_limit", "1g") for row in entries
                        if row.get("driver") == driver and row.get("model") == model), "1g")
                    if price["usd_micros"] < minimum_execution_rate(driver, memory):
                        errors.append(f"Execution price is below the published conservative floor for {driver}:{model}.")
    return errors


def model_supports_execution(entry):
    support = entry.get("code_execution") if isinstance(entry, dict) else None
    if not isinstance(support, dict) or support.get("enabled") is not True:
        return False
    capabilities = entry.get("capabilities") if isinstance(entry, dict) else None
    if isinstance(capabilities, (list, tuple, set)):
        driver = entry.get("driver")
        expected_tool = {"openai_api": "code_interpreter",
                         "anthropic_api": "code_execution_20250825"}.get(driver)
        expected_unit = {"openai_api": "openai_container_20m",
                         "anthropic_api": "anthropic_execution_5m"}.get(driver)
        memory = support.get("memory_limit", "1g")
        model, source = entry.get("model"), entry.get("source_id")
        checker = getattr(settings, "DCM_AI_EXECUTION_CAPABILITY_CHECK", None)
        try:
            from django_core_micha.ai_policy.policy import current_policy
            policy = current_policy()
            verified = bool(policy and any(
                proof.get("source_id") == source and proof.get("model_id") == model and
                proof.get("driver") == driver and proof.get("tool_version") == expected_tool and proof.get("evidence")
                for proof in policy.verified_execution))
            if isinstance(checker, str):
                from django.utils.module_loading import import_string
                checker = import_string(checker)
            if checker and verified:
                verified = checker(source, driver, model, expected_tool) is True
        except Exception:
            verified = False
        return bool(verified
            and "code_execution" in capabilities and expected_tool and support.get("tool_version") == expected_tool
            and support.get("unit") == expected_unit and type(support.get("usd_micros")) is int
            and support["usd_micros"] >= minimum_execution_rate(driver, memory)
            and memory in _MEMORY_LIMITS and driver in {"openai_api", "anthropic_api"}
            and isinstance(model, str) and source)
    if entry.get("model") not in VERIFIED_EXECUTION_MODELS.get(entry.get("driver"), set()):
        return False
    return not configuration_errors([entry])


def _validate_dataset(item, caps):
    if isinstance(item, Dataset):
        row = item
    elif isinstance(item, dict) and set(item) == {"name", "data_class", "content", "media_type"}:
        row = Dataset(**item)
    else:
        raise DriverError("execution_input_invalid")
    if (not isinstance(row.name, str) or not _INPUT_FILENAME.fullmatch(row.name) or
            not isinstance(row.data_class, str) or not row.data_class.strip() or
            not isinstance(row.content, bytes) or not row.content or
            len(row.content) > caps["max_execution_file_bytes"]):
        raise DriverError("execution_input_invalid")
    if row.name.lower().endswith(".csv"):
        if row.media_type not in {"text/csv", "application/csv"}:
            raise DriverError("execution_input_invalid")
        try:
            decoded = row.content.decode("utf-8-sig")
            next(csv.reader(io.StringIO(decoded)), None)
        except (UnicodeDecodeError, csv.Error) as exc:
            raise DriverError("execution_input_invalid") from exc
    else:
        if row.media_type != "application/json":
            raise DriverError("execution_input_invalid")
        try:
            json.loads(row.content)
        except (UnicodeDecodeError, ValueError) as exc:
            raise DriverError("execution_input_invalid") from exc
    return row


def _dataset_fingerprint(dataset):
    digest = hashlib.sha256(dataset.content).hexdigest()
    return (dataset.data_class, dataset.media_type, digest)


def pending_execution_datasets(session, datasets):
    """Return new inputs; refuse snapshots that revoke or replace sent data."""
    incoming = {}
    for dataset in datasets:
        if dataset.name in incoming:
            raise DriverError("execution_input_invalid")
        incoming[dataset.name] = dataset
    manifest = session.dataset_manifest
    if not set(manifest).issubset(incoming):
        raise DriverError("execution_unavailable")
    pending = []
    for name, dataset in incoming.items():
        fingerprint = _dataset_fingerprint(dataset)
        if name in manifest:
            if manifest[name] != fingerprint:
                raise DriverError("execution_unavailable")
        else:
            pending.append(dataset)
    return pending


def mark_execution_dataset_uploaded(session, dataset):
    """Remember the exact trusted dataset snapshot transferred to this analysis."""
    fingerprint = _dataset_fingerprint(dataset)
    existing = session.dataset_manifest.get(dataset.name)
    if existing is not None and existing != fingerprint:
        raise DriverError("execution_unavailable")
    session.dataset_manifest[dataset.name] = fingerprint


async def prepare_execution(chat, user, turn, entry, *, authorize):
    """Resolve an opted-in analysis from app-owned server hooks, or return None."""
    support = entry.get("code_execution") if isinstance(entry, dict) else None
    if not isinstance(support, dict) or support.get("enabled") is not True:
        return None
    from asgiref.sync import sync_to_async
    if not await sync_to_async(model_supports_execution, thread_sensitive=True)(entry):
        return None
    if (turn.driver != entry.get("driver") or turn.model != entry.get("model") or
            (entry.get("source_id") and turn.source_id != entry.get("source_id"))):
        return None
    if not execution_scope_is_private(chat.scope) or not await authorize():
        return None
    driver = turn.driver
    if driver == "claude_cli" or driver not in {"openai_api", "anthropic_api"}:
        return None
    tool_version = support.get("tool_version")
    allowed_versions = {"openai_api": {"code_interpreter"},
                        "anthropic_api": {"code_execution_20250825"}}
    if tool_version not in allowed_versions[driver]:
        return None
    expected_unit = "openai_container_20m" if driver == "openai_api" else "anthropic_execution_5m"
    if isinstance(entry.get("capabilities"), (list, tuple, set)):
        price = {"unit": support.get("unit"), "usd_micros": support.get("usd_micros")}
    else:
        prices = getattr(settings, "DCM_ASSISTANT_EXECUTION_PRICING", None)
        if not validate_price_table(prices):
            return None
        price = prices.get(f"{driver}:{turn.model}")
    if (not price or price["unit"] != expected_unit or
            type(price.get("usd_micros")) is not int or
            price["usd_micros"] < minimum_execution_rate(driver, support.get("memory_limit", "1g"))):
        return None
    hook = _hook("EXECUTION_POLICY")
    if hook is None:
        return None
    try:
        if inspect.iscoroutinefunction(hook):
            decision = await hook(chat, user, turn, dict(entry))
        else:
            from asgiref.sync import sync_to_async
            decision = await sync_to_async(hook, thread_sensitive=True)(chat, user, turn, dict(entry))
    except Exception as exc:
        log.warning("Assistant execution policy hook failed (%s)", type(exc).__name__)
        return None
    if not isinstance(decision, dict) or decision.get("enabled") is not True:
        return None
    raw_datasets = decision.get("datasets")
    caps = _limits()
    if not isinstance(raw_datasets, (list, tuple)) or len(raw_datasets) > caps["max_execution_files"]:
        raise DriverError("execution_input_invalid")
    datasets = tuple(_validate_dataset(item, caps) for item in raw_datasets)
    if sum(len(row.content) for row in datasets) > caps["max_execution_total_bytes"]:
        raise DriverError("execution_input_invalid")
    if driver == "anthropic_api":
        approvals = decision.get("anthropic_retention_approval")
        classes = {row.data_class for row in datasets}
        if (not isinstance(approvals, dict) or approvals.get("policy") != ANTHROPIC_RETENTION_POLICY or approvals.get("app") is not True or
                not isinstance(approvals.get("data_classes"), (list, tuple, set)) or
                not all(isinstance(row, str) for row in approvals["data_classes"]) or
                not classes.issubset(set(approvals["data_classes"]))):
            return None
    memory_limit = support.get("memory_limit", "1g")
    if memory_limit not in _MEMORY_LIMITS:
        return None
    max_seconds = min(caps["max_execution_seconds"], support.get("max_execution_seconds", caps["max_execution_seconds"]))
    unit_seconds = 1200 if driver == "openai_api" else 300
    return ExecutionConfig(driver, turn.model, tool_version, memory_limit,
        max_seconds,
        min(caps["max_execution_continuations"], support.get("max_continuations", caps["max_execution_continuations"])),
        price["usd_micros"] * math.ceil(max_seconds / unit_seconds), datasets)


async def refresh_execution_config(current, refresh):
    """Recheck trusted execution policy while retaining this analysis session."""
    if refresh is None:
        return current
    updated = await refresh()
    fields = ("provider", "model", "tool_version", "memory_limit", "max_execution_seconds",
        "max_continuations", "reserved_usd_micros")
    if (not isinstance(updated, ExecutionConfig) or updated.session is not current.session or
            any(getattr(updated, field) != getattr(current, field) for field in fields)):
        raise DriverError("execution_unavailable")
    return updated


def bounded_text(value, limit):
    """Return valid UTF-8 text bounded by bytes and exact omission metadata."""
    if not isinstance(value, str):
        return "", {"truncated": False, "omitted_bytes": 0}
    parts = []
    kept = 0
    omitted = 0
    truncated = False
    for char in value:
        codepoint = ord(char)
        size = _utf8_char_size(codepoint)
        safe_char = "?" if 0xD800 <= codepoint <= 0xDFFF else char
        if not truncated and kept + size <= limit:
            parts.append(safe_char)
            kept += size
        else:
            truncated = True
            omitted += size
    return "".join(parts), {"truncated": omitted > 0, "omitted_bytes": omitted}


def _utf8_char_size(codepoint):
    if 0xD800 <= codepoint <= 0xDFFF:
        return 1
    if codepoint <= 0x7F:
        return 1
    if codepoint <= 0x7FF:
        return 2
    if codepoint <= 0xFFFF:
        # Lone surrogates use the encoder's one-byte replacement character.
        return 3
    return 4


def _utf8_size(value):
    """Count UTF-8 bytes without allocating an encoded copy of provider text."""
    return sum(_utf8_char_size(ord(char)) for char in value)


def bounded_text_parts(values, limit, *, separator=""):
    """Bound joined provider text without first allocating the joined value."""
    parts = []
    kept = 0
    omitted = 0
    first = True
    truncated = False

    def append(value):
        nonlocal kept, omitted, truncated
        value = value if isinstance(value, str) else str(value)
        if truncated:
            omitted += _utf8_size(value)
            return
        text, metadata = bounded_text(value, max(0, limit - kept))
        parts.append(text)
        kept += _utf8_size(text)
        omitted += metadata["omitted_bytes"]
        truncated = metadata["truncated"]

    for value in values:
        if not first and separator:
            append(separator)
        append(value)
        first = False
    return "".join(parts), {"truncated": omitted > 0, "omitted_bytes": omitted}


def normalize_text(value, limit):
    return bounded_text(value, limit)[0]


async def read_bounded_response(response, limit):
    """Read at most limit + 1 bytes from an SDK's asynchronous response stream."""
    method = getattr(response, "iter_bytes", None) or getattr(response, "aiter_bytes", None)
    if method is None:
        raise DriverError("execution_output_invalid")
    chunk_size = max(1, min(64 * 1024, limit + 1))
    try:
        iterator = method(chunk_size)
    except TypeError:
        iterator = method()
    if inspect.isawaitable(iterator):
        iterator = await iterator
    content = bytearray()
    truncated = False
    async for chunk in iterator:
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise DriverError("execution_output_invalid")
        remaining = limit + 1 - len(content)
        if remaining <= 0:
            truncated = True
            break
        if len(chunk) > remaining:
            content.extend(chunk[:remaining])
            truncated = True
            break
        content.extend(chunk)
        if len(content) > limit:
            truncated = True
            break
    return bytes(content[:limit]), truncated


def redact_provider_refs(value, provider_ids):
    """Keep opaque provider references and their download URLs server-side."""
    refs = sorted({item for item in provider_ids if isinstance(item, str) and item}, key=len, reverse=True)
    provider_url = re.compile(r"https?://(?:api\.openai\.com|api\.anthropic\.com)/[^\s\"'<>]+", re.I)
    if isinstance(value, str):
        for ref in refs:
            value = value.replace(ref, "[provider reference]")
        return provider_url.sub("[provider download URL]", value)
    if isinstance(value, list):
        return [redact_provider_refs(item, refs) for item in value]
    if isinstance(value, dict):
        return {redact_provider_refs(str(key), refs): redact_provider_refs(item, refs) for key, item in value.items()}
    return value


def _decode_bounded_utf8(content, *, allow_incomplete=False):
    try:
        return content.decode("utf-8-sig"), content
    except UnicodeDecodeError as exc:
        if (allow_incomplete and exc.end == len(content) and
                exc.reason == "unexpected end of data"):
            prefix = content[:exc.start]
            return prefix.decode("utf-8-sig"), prefix
        raise DriverError("execution_output_invalid") from exc


class _TrackingCsvLines:
    def __init__(self, text):
        self._stream = io.StringIO(text, newline="")
        self.position = 0

    def __iter__(self):
        return self

    def __next__(self):
        line = self._stream.readline()
        if line == "":
            raise StopIteration
        self.position += len(line)
        return line


def _last_complete_csv_boundary(text):
    quoted = False
    index = 0
    boundary = 0
    while index < len(text):
        char = text[index]
        if char == '"':
            if quoted and index + 1 < len(text) and text[index + 1] == '"':
                index += 2
                continue
            quoted = not quoted
        elif char in "\r\n" and not quoted:
            if char == "\r" and index + 1 < len(text) and text[index + 1] == "\n":
                index += 1
            boundary = index + 1
        index += 1
    return boundary


def _payload_bytes(value):
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8"))


def validate_output_file(name, media_type, content, caps, *, source_size=None,
                         read_truncated=False, aggregate_limit=None):
    """Return a valid bounded CSV, JSON, or text representation."""
    if (not isinstance(name, str) or not _OUTPUT_FILENAME.fullmatch(name) or
            not isinstance(content, bytes)):
        raise DriverError("execution_output_invalid")
    suffix = name.rsplit(".", 1)[-1].lower()
    file_limit = caps["max_execution_output_file_bytes"]
    limit = file_limit if aggregate_limit is None else min(file_limit, max(0, aggregate_limit))
    file_truncated = read_truncated or len(content) > file_limit
    content_truncated = file_truncated or len(content) > limit
    raw = content[:limit]
    reported_size = source_size if type(source_size) is int and source_size >= 0 else None
    if reported_size is None and not read_truncated:
        reported_size = len(content)
    reason = "file_limit" if file_truncated else "aggregate_limit"
    if suffix == "csv" and media_type in {"text/csv", "application/csv"}:
        try:
            decoded, _valid_raw = _decode_bounded_utf8(raw, allow_incomplete=content_truncated)
            if content_truncated:
                decoded = decoded[:_last_complete_csv_boundary(decoded)]
            lines = _TrackingCsvLines(decoded)
            reader = csv.reader(lines, strict=True)
            row_count = 0
            row_limit = False
            kept_position = 0
            while True:
                try:
                    row = next(reader)
                except StopIteration:
                    break
                if len(row) > 1000:
                    raise ValueError
                row_count += 1
                if row_count <= 100_000:
                    kept_position = lines.position
                if row_count > 100_000:
                    row_limit = True
                    break
            if row_limit:
                safe = decoded[:kept_position]
                content_truncated = True
                reason = "row_limit"
            else:
                safe = decoded
        except (UnicodeDecodeError, csv.Error, ValueError, RecursionError) as exc:
            raise DriverError("execution_output_invalid") from exc
    elif suffix == "json" and media_type == "application/json":
        if content_truncated:
            safe = None
        else:
            try:
                decoded = content.decode("utf-8-sig")
                safe = json.loads(decoded, parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("invalid constant")))
                encoded = json.dumps(safe, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
                if len(encoded.encode("utf-8")) > file_limit:
                    safe = None
                    content_truncated = True
                    reason = "file_limit"
            except (UnicodeDecodeError, ValueError, TypeError, RecursionError) as exc:
                raise DriverError("execution_output_invalid") from exc
        if aggregate_limit is not None and _payload_bytes(safe) > aggregate_limit:
            safe = None
            content_truncated = True
            reason = "aggregate_limit"
    elif suffix == "txt" and media_type == "text/plain":
        try:
            decoded, _valid_raw = _decode_bounded_utf8(raw, allow_incomplete=content_truncated)
        except DriverError:
            raise
        safe, _text_meta = bounded_text(decoded, limit)
        if _text_meta["truncated"]:
            content_truncated = True
        if aggregate_limit is not None and _payload_bytes(safe) > aggregate_limit:
            safe, _text_meta = bounded_text(safe, aggregate_limit)
            content_truncated = True
            reason = "aggregate_limit"
    else:
        raise DriverError("execution_output_invalid")
    result = {"name": name, "media_type": media_type, "data": safe}
    payload_size = _payload_bytes(safe)
    if content_truncated:
        kept_bytes = len(safe.encode("utf-8")) if isinstance(safe, str) else 0
        omitted = max(0, reported_size - kept_bytes) if reported_size is not None else None
        result.update({"truncated": True, "omitted_bytes": omitted,
            "truncation_reason": reason})
    return result, payload_size


def validate_outputs(files, *, session=None, omitted_files=0, omitted_bytes=0,
                     return_summary=False):
    caps = _limits()
    if not isinstance(files, list):
        raise DriverError("execution_output_invalid")
    already_counted = session.output_file_count if session else 0
    already_bytes = session.output_bytes if session else 0
    file_slots = max(0, caps["max_execution_output_files"] - already_counted)
    total_remaining = max(0, caps["max_execution_output_total_bytes"] - already_bytes)
    result = []
    summary = {"truncated": False, "omitted_files": max(0, int(omitted_files)),
        "omitted_bytes": None if omitted_bytes is None else max(0, int(omitted_bytes))}
    if summary["omitted_files"]:
        summary["truncated"] = True
    for file in files:
        if (not isinstance(file, dict) or
                not {"name", "media_type", "content"}.issubset(file) or
                set(file) - {"name", "media_type", "content", "source_size", "read_truncated"}):
            raise DriverError("execution_output_invalid")
        if (not isinstance(file["name"], str) or not isinstance(file["media_type"], str) or
                not isinstance(file["content"], bytes)):
            raise DriverError("execution_output_invalid")
        content = file["content"]
        size = len(content)
        if len(result) >= file_slots or total_remaining <= 0:
            summary["truncated"] = True
            summary["omitted_files"] += 1
            if summary["omitted_bytes"] is not None:
                summary["omitted_bytes"] += size
            continue
        if file["name"].lower().endswith(".json") and total_remaining < 4:
            summary["truncated"] = True
            summary["omitted_files"] += 1
            if summary["omitted_bytes"] is not None:
                summary["omitted_bytes"] += size
            continue
        row, used = validate_output_file(**file, caps=caps,
            aggregate_limit=total_remaining)
        result.append(row)
        total_remaining = max(0, total_remaining - used)
        if row.get("truncated"):
            summary["truncated"] = True
            if summary["omitted_bytes"] is not None and type(row.get("omitted_bytes")) is int:
                summary["omitted_bytes"] += row["omitted_bytes"]
            elif row.get("omitted_bytes") is None:
                summary["omitted_bytes"] = None
        if session:
            session.output_file_count += 1
            session.output_bytes += used
    return (result, summary) if return_summary else result
