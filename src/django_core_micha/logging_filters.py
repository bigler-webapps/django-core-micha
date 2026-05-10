"""Logging filters for redacting sensitive data from log records.

Used by `LOGGING` in `django_core_micha.settings.settings_base` to prevent
secrets and PII from landing in console/aggregator logs via Django tracebacks
or third-party library output.
"""
from __future__ import annotations

import logging
import re


class SensitiveDataFilter(logging.Filter):
    """Redact common secret/PII patterns in log messages and args.

    Patterns are intentionally conservative — they target `key=value` and
    `"key": "value"` shapes for known sensitive keys and replace the value
    portion with `***REDACTED***`. Emails are partially masked.
    """

    _KV_PATTERN = re.compile(
        r"(?i)(password|passwd|secret|token|api[_-]?key|authorization|cookie|set-cookie)"
        r"(\s*[:=]\s*)"
        r"(['\"]?)([^'\"\s,;}&]+)\3"
    )
    _JSON_PATTERN = re.compile(
        r"(?i)(\"(?:password|passwd|secret|token|api[_-]?key|authorization)\"\s*:\s*\")"
        r"([^\"]+)"
        r"(\")"
    )
    _EMAIL_PATTERN = re.compile(
        r"\b([A-Za-z0-9._%+-]{1,3})[A-Za-z0-9._%+-]*(@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b"
    )

    _REDACTED = "***REDACTED***"

    @classmethod
    def _scrub(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        scrubbed = cls._KV_PATTERN.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{cls._REDACTED}{m.group(3)}", value)
        scrubbed = cls._JSON_PATTERN.sub(lambda m: f"{m.group(1)}{cls._REDACTED}{m.group(3)}", scrubbed)
        scrubbed = cls._EMAIL_PATTERN.sub(lambda m: f"{m.group(1)}***{m.group(2)}", scrubbed)
        return scrubbed

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = self._scrub(record.msg)
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {k: self._scrub(v) for k, v in record.args.items()}
                elif isinstance(record.args, tuple):
                    record.args = tuple(self._scrub(a) for a in record.args)
        except Exception:
            # Never let a logging filter break logging itself.
            pass
        return True
