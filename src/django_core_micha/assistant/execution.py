"""Fail-closed contracts for provider-hosted Assistant code execution.

The consuming application remains the authority for whether a requester may
transfer a particular dataset. This module only accepts that decision through
the trusted server-side hook and applies the shared transport and size limits.
"""
import csv
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
_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\.(?:csv|json)\Z", re.I)
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
    provider_file_ids: list = field(default_factory=list)
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
    if (not isinstance(row.name, str) or not _FILENAME.fullmatch(row.name) or
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


def normalize_text(value, limit):
    if not isinstance(value, str):
        return ""
    raw = value.encode("utf-8", errors="replace")
    return raw[:limit].decode("utf-8", errors="ignore")


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


def validate_output_file(name, media_type, content, caps):
    """Return inert, bounded CSV/JSON output; reject everything else."""
    if (not isinstance(name, str) or not _FILENAME.fullmatch(name) or
            not isinstance(content, bytes) or not content or
            len(content) > caps["max_execution_output_file_bytes"]):
        raise DriverError("execution_output_invalid")
    suffix = name.rsplit(".", 1)[-1].lower()
    if suffix == "csv" and media_type in {"text/csv", "application/csv"}:
        try:
            decoded = content.decode("utf-8-sig")
            rows = list(csv.reader(io.StringIO(decoded)))
            if len(rows) > 100_000 or any(len(row) > 1000 for row in rows):
                raise ValueError
            safe = decoded
        except (UnicodeDecodeError, csv.Error, ValueError) as exc:
            raise DriverError("execution_output_invalid") from exc
    elif suffix == "json" and media_type == "application/json":
        try:
            safe = json.loads(content)
            encoded = json.dumps(safe, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        except (UnicodeDecodeError, ValueError, TypeError) as exc:
            raise DriverError("execution_output_invalid") from exc
        if len(encoded.encode("utf-8")) > caps["max_execution_output_file_bytes"]:
            raise DriverError("execution_output_invalid")
    else:
        raise DriverError("execution_output_invalid")
    return {"name": name, "media_type": media_type, "data": safe}


def validate_outputs(files):
    caps = _limits()
    if (not isinstance(files, list) or len(files) > caps["max_execution_output_files"] or
            any(not isinstance(file, dict) or set(file) != {"name", "media_type", "content"} for file in files)):
        raise DriverError("execution_output_invalid")
    result = [validate_output_file(**file, caps=caps) for file in files]
    total = sum(len((row["data"] if isinstance(row["data"], str) else
                     json.dumps(row["data"], ensure_ascii=False)).encode("utf-8")) for row in result)
    if total > caps["max_execution_output_total_bytes"]:
        raise DriverError("execution_output_invalid")
    return result
