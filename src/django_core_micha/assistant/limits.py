"""Maana-derived defaults with a single settings override seam."""
from django.conf import settings

DEFAULTS = {
    "frame_rate_window": 10, "frame_rate_count": 30,
    "max_concurrent_turns": 4, "max_turns_per_user": 2,
    "max_message_length": 20_000, "max_prompt_bytes": 120_000, "max_output_bytes": 512 * 1024,
    "provider_timeout": 120, "idle_timeout": 30, "heartbeat_seconds": 5, "stale_turn_seconds": 30,
    "max_tool_calls": 10,
    "max_client_context_bytes": 65536, "max_repair_rounds": 2,
    "access_recheck_seconds": 1,
    "session_rebuild_tokens": 200_000,
    "context_limit_tokens": 40_000, "summary_keep_turns": 6,
}

def get_limit(name):
    if name not in DEFAULTS: raise KeyError(name)
    overrides = getattr(settings, "DCM_ASSISTANT_LIMITS", {}) or {}
    value = int(overrides.get(name, DEFAULTS[name]))
    if value <= 0: raise ValueError(f"DCM_ASSISTANT_LIMITS.{name} must be positive")
    return value
