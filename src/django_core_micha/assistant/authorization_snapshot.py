"""Application-owned authorization generation and lock contract."""
from django.core.exceptions import PermissionDenied
from django.db import connection

from .hooks import _hook


def lock_authorization(user_id):
    """Acquire the app lock inside the caller's transaction and validate its snapshot."""
    if not connection.in_atomic_block or user_id is None:
        raise PermissionDenied("authorization_unverified")
    hook = _hook("LOCK_AUTHORIZATION_SNAPSHOT")
    if not callable(hook):
        raise PermissionDenied("authorization_unverified")
    try:
        snapshot = hook(user_id)
        if not isinstance(snapshot, dict) or set(snapshot) != {"current", "baseline"}:
            raise ValueError("invalid snapshot")
        def pair(value):
            if not isinstance(value, dict) or set(value) != {"global", "user"}:
                raise ValueError("invalid generation pair")
            if any(type(value[k]) is not int or value[k] < 0 for k in ("global", "user")):
                raise ValueError("invalid generation")
            return (value["global"], value["user"])
        current = pair(snapshot["current"])
        baseline = None if snapshot["baseline"] is None else pair(snapshot["baseline"])
        return current, baseline
    except Exception as exc:
        raise PermissionDenied("authorization_unverified") from exc
