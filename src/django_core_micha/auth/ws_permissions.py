"""S112 — Platform WebSocket permission framework.

Mirrors the DRF `permissions.BasePermission` pattern for Channels consumers,
giving every app a consistent, testable authorisation layer for WebSocket
connections.

Usage in an app consumer::

    from django_core_micha.auth.ws_permissions import (
        BaseSecureConsumer, WsPermission,
    )

    class CanDoSomethingWs(WsPermission):
        async def has_permission(self, scope, consumer) -> bool:
            user = scope.get("user")
            slug = scope["url_route"]["kwargs"]["slug"]
            ...

    class MyConsumer(BaseSecureConsumer, AsyncWebsocketConsumer):
        permission_classes_ws = [CanDoSomethingWs]

        async def post_connect(self):
            # Setup-after-accept (group_add, initial send, etc.)
            ...

No channels import required — BaseSecureConsumer duck-types against
self.close() and self.accept() provided by the concrete consumer.
"""
from __future__ import annotations

import importlib
import inspect
import logging
from typing import Sequence

logger = logging.getLogger(__name__)


class WsPermission:
    """Base class — analog to rest_framework.permissions.BasePermission.

    Subclass and override ``has_permission`` (async). Returning False causes
    ``connect()`` to close with code 4403 (WS-layer "forbidden").
    """

    async def has_permission(self, scope: dict, consumer) -> bool:
        raise NotImplementedError


class IsAuthenticatedWs(WsPermission):
    async def has_permission(self, scope: dict, consumer) -> bool:
        user = scope.get("user")
        return bool(user and getattr(user, "is_authenticated", False))


class IsSuperuserWs(WsPermission):
    async def has_permission(self, scope: dict, consumer) -> bool:
        user = scope.get("user")
        # Guard on is_authenticated first — is_superuser alone is not sufficient.
        return bool(
            user
            and getattr(user, "is_authenticated", False)
            and getattr(user, "is_superuser", False)
        )


class IsObjectOwnerWs(WsPermission):
    """Delegate to consumer-implemented hooks.

    The consumer must provide:
    - ``async def get_object(self)`` — returns the target object or None
    - ``async def check_object_owner(self, obj) -> bool``
    """

    async def has_permission(self, scope: dict, consumer) -> bool:
        user = scope.get("user")
        if not (user and getattr(user, "is_authenticated", False)):
            return False
        obj = await consumer.get_object()
        if obj is None:
            return False
        return await consumer.check_object_owner(obj)


class BaseSecureConsumer:
    """Mixin that enforces ``permission_classes_ws`` on every connect().

    Class-level attributes (override in consumer subclass):
    - ``permission_classes_ws``: sequence of WsPermission subclass *types*
    - ``allowed_for_anonymous``: set True only for genuinely public routes

    Subclasses must NOT override ``connect()`` — that would bypass the
    permission check. Put setup-after-accept work in ``post_connect()``.

    Close codes:
    - 4401 — not authenticated (anonymous connection on a protected route)
    - 4403 — authenticated but permission denied
    - 1011 — server error in post_connect() after the connection was accepted
    """

    permission_classes_ws: Sequence[type[WsPermission]] = ()
    allowed_for_anonymous: bool = False

    async def connect(self) -> None:
        user = self.scope.get("user")  # type: ignore[attr-defined]
        is_auth = bool(user and getattr(user, "is_authenticated", False))

        if not self.allowed_for_anonymous and not is_auth:
            await self.close(code=4401)  # type: ignore[attr-defined]
            return

        for perm_class in self.permission_classes_ws:
            perm = perm_class()
            try:
                ok = await perm.has_permission(self.scope, self)  # type: ignore[attr-defined]
            except Exception:
                logger.exception(
                    "WS permission %s raised on %s; treating as deny",
                    perm_class.__name__,
                    type(self).__name__,
                )
                ok = False
            if not ok:
                await self.close(code=4403)  # type: ignore[attr-defined]
                return

        await self.accept()  # type: ignore[attr-defined]
        try:
            await self.post_connect()
        except Exception:
            logger.exception(
                "post_connect() raised on %s; closing accepted connection",
                type(self).__name__,
            )
            await self.close(code=1011)  # type: ignore[attr-defined]

    async def post_connect(self) -> None:
        """Override in subclass for setup-after-accept. Default no-op."""


# ---------------------------------------------------------------------------
# Inventory helper — exported here so app tests import cleanly
# ---------------------------------------------------------------------------

def assert_all_consumers_secure(module_paths: list[str]) -> list[str]:
    """Return a list of violation strings (empty = all secure).

    For each module, every class whose name ends in 'Consumer' must either:
    1. Inherit from ``BaseSecureConsumer``, OR
    2. Declare a class-level ``_WS_AUDIT_EXEMPT = "<non-empty reason string>"``
       attribute.

    Pass the result to ``assert violations == []`` in your test.
    """
    violations: list[str] = []
    for module_path in module_paths:
        mod = importlib.import_module(module_path)
        for name, cls in inspect.getmembers(mod, inspect.isclass):
            if not name.endswith("Consumer"):
                continue
            if cls.__module__ != module_path:
                continue  # skip re-exports / imports from other modules
            if issubclass(cls, BaseSecureConsumer):
                continue
            exempt_reason = getattr(cls, "_WS_AUDIT_EXEMPT", None)
            if isinstance(exempt_reason, str) and exempt_reason.strip():
                continue
            violations.append(
                f"{module_path}.{name}: missing BaseSecureConsumer and no _WS_AUDIT_EXEMPT"
            )
    return violations
