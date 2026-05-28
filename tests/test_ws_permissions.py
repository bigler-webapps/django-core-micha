"""S112 — Unit tests for django_core_micha.auth.ws_permissions.

Tests run without channels infrastructure — consumers are minimal mocks
that track which methods were called.
"""
import pytest

from django_core_micha.auth.ws_permissions import (
    BaseSecureConsumer,
    IsAuthenticatedWs,
    IsObjectOwnerWs,
    IsSuperuserWs,
    WsPermission,
    assert_all_consumers_secure,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _AnonUser:
    is_authenticated = False
    is_superuser = False


class _AuthUser:
    is_authenticated = True
    is_superuser = False


class _SuperUser:
    is_authenticated = True
    is_superuser = True


def _scope(user=None, kwargs=None):
    return {"user": user, "url_route": {"kwargs": kwargs or {}}}


class _MockConsumer(BaseSecureConsumer):
    """Minimal concrete consumer that records calls."""

    closed_code: int | None = None
    accepted: bool = False
    post_connect_called: bool = False

    def __init__(self, scope: dict, perm_classes=()):
        self.scope = scope
        self.permission_classes_ws = perm_classes
        self.closed_code = None
        self.accepted = False
        self.post_connect_called = False

    async def close(self, code=1000):
        self.closed_code = code

    async def accept(self):
        self.accepted = True

    async def post_connect(self):
        self.post_connect_called = True


# ---------------------------------------------------------------------------
# IsAuthenticatedWs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_is_authenticated_anon_fails():
    perm = IsAuthenticatedWs()
    assert not await perm.has_permission(_scope(_AnonUser()), None)


@pytest.mark.asyncio
async def test_is_authenticated_no_user_fails():
    perm = IsAuthenticatedWs()
    assert not await perm.has_permission(_scope(None), None)


@pytest.mark.asyncio
async def test_is_authenticated_auth_passes():
    perm = IsAuthenticatedWs()
    assert await perm.has_permission(_scope(_AuthUser()), None)


# ---------------------------------------------------------------------------
# IsSuperuserWs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_is_superuser_regular_fails():
    perm = IsSuperuserWs()
    assert not await perm.has_permission(_scope(_AuthUser()), None)


@pytest.mark.asyncio
async def test_is_superuser_superuser_passes():
    perm = IsSuperuserWs()
    assert await perm.has_permission(_scope(_SuperUser()), None)


@pytest.mark.asyncio
async def test_is_superuser_anon_with_is_superuser_true_fails():
    """is_superuser=True on an unauthenticated user must not pass (S1 fix)."""
    class _AnonSuperUser:
        is_authenticated = False
        is_superuser = True

    perm = IsSuperuserWs()
    assert not await perm.has_permission(_scope(_AnonSuperUser()), None)


# ---------------------------------------------------------------------------
# IsObjectOwnerWs
# ---------------------------------------------------------------------------

class _FakeObject:
    pass


@pytest.mark.asyncio
async def test_is_object_owner_anon_fails():
    perm = IsObjectOwnerWs()

    class C:
        scope = _scope(_AnonUser())
        async def get_object(self): return _FakeObject()
        async def check_object_owner(self, obj): return True

    assert not await perm.has_permission(C.scope, C())


@pytest.mark.asyncio
async def test_is_object_owner_none_object_fails():
    perm = IsObjectOwnerWs()

    class C:
        scope = _scope(_AuthUser())
        async def get_object(self): return None
        async def check_object_owner(self, obj): return True

    assert not await perm.has_permission(C.scope, C())


@pytest.mark.asyncio
async def test_is_object_owner_owner_check_false_fails():
    perm = IsObjectOwnerWs()

    class C:
        scope = _scope(_AuthUser())
        async def get_object(self): return _FakeObject()
        async def check_object_owner(self, obj): return False

    assert not await perm.has_permission(C.scope, C())


@pytest.mark.asyncio
async def test_is_object_owner_passes():
    perm = IsObjectOwnerWs()

    class C:
        scope = _scope(_AuthUser())
        async def get_object(self): return _FakeObject()
        async def check_object_owner(self, obj): return True

    assert await perm.has_permission(C.scope, C())


# ---------------------------------------------------------------------------
# BaseSecureConsumer — anonymous enforcement
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_base_anon_rejected_with_4401():
    c = _MockConsumer(_scope(_AnonUser()))
    await c.connect()
    assert c.closed_code == 4401
    assert not c.accepted


@pytest.mark.asyncio
async def test_base_anon_allowed_for_anonymous_route():
    c = _MockConsumer(_scope(_AnonUser()))
    c.allowed_for_anonymous = True
    await c.connect()
    assert c.accepted
    assert c.closed_code is None


@pytest.mark.asyncio
async def test_base_no_user_rejected_with_4401():
    c = _MockConsumer(_scope(None))
    await c.connect()
    assert c.closed_code == 4401


# ---------------------------------------------------------------------------
# BaseSecureConsumer — permission chain
# ---------------------------------------------------------------------------

class _AlwaysDenyWs(WsPermission):
    async def has_permission(self, scope, consumer) -> bool:
        return False


class _AlwaysAllowWs(WsPermission):
    async def has_permission(self, scope, consumer) -> bool:
        return True


class _RaisingWs(WsPermission):
    async def has_permission(self, scope, consumer) -> bool:
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_base_perm_deny_gives_4403():
    c = _MockConsumer(_scope(_AuthUser()), perm_classes=(_AlwaysDenyWs,))
    await c.connect()
    assert c.closed_code == 4403
    assert not c.accepted


@pytest.mark.asyncio
async def test_base_perm_exception_gives_4403():
    c = _MockConsumer(_scope(_AuthUser()), perm_classes=(_RaisingWs,))
    await c.connect()
    assert c.closed_code == 4403


@pytest.mark.asyncio
async def test_base_perm_allow_accepts_and_calls_post_connect():
    c = _MockConsumer(_scope(_AuthUser()), perm_classes=(_AlwaysAllowWs,))
    await c.connect()
    assert c.accepted
    assert c.post_connect_called
    assert c.closed_code is None


@pytest.mark.asyncio
async def test_base_no_perms_auth_user_accepts():
    c = _MockConsumer(_scope(_AuthUser()), perm_classes=())
    await c.connect()
    assert c.accepted


@pytest.mark.asyncio
async def test_base_post_connect_exception_closes_with_1011():
    """post_connect() raising must close the accepted connection (S2 fix)."""
    class _BrokenConsumer(BaseSecureConsumer):
        closed_code: int | None = None
        accepted: bool = False

        def __init__(self, scope):
            self.scope = scope
            self.permission_classes_ws = ()

        async def close(self, code=1000):
            self.closed_code = code

        async def accept(self):
            self.accepted = True

        async def post_connect(self):
            raise RuntimeError("group_add failed")

    c = _BrokenConsumer(_scope(_AuthUser()))
    await c.connect()
    assert c.accepted
    assert c.closed_code == 1011


@pytest.mark.asyncio
async def test_base_chain_first_deny_blocks_second():
    """Second permission should not run if first denied."""
    second_called = []

    class _Second(WsPermission):
        async def has_permission(self, scope, consumer) -> bool:
            second_called.append(True)
            return True

    c = _MockConsumer(_scope(_AuthUser()), perm_classes=(_AlwaysDenyWs, _Second))
    await c.connect()
    assert c.closed_code == 4403
    assert second_called == []


# ---------------------------------------------------------------------------
# assert_all_consumers_secure
# ---------------------------------------------------------------------------

def test_inventory_empty_list():
    assert assert_all_consumers_secure([]) == []


def test_inventory_detects_unprotected_consumer():
    # Simulate by passing dcm's own test module path — but there are no
    # *Consumer classes there. We test the detection via a runtime injection.
    from django_core_micha.auth import ws_permissions as mod

    class _UnprotectedConsumer:
        pass

    _UnprotectedConsumer.__module__ = mod.__name__
    original_name = "_UnprotectedConsumer"
    setattr(mod, original_name, _UnprotectedConsumer)
    try:
        violations = assert_all_consumers_secure([mod.__name__])
        assert any("_UnprotectedConsumer" in v for v in violations)
    finally:
        delattr(mod, original_name)


def test_inventory_exempt_passes():
    from django_core_micha.auth import ws_permissions as mod

    class _ExemptConsumer:
        _WS_AUDIT_EXEMPT = "test exemption"

    _ExemptConsumer.__module__ = mod.__name__
    setattr(mod, "_ExemptConsumer", _ExemptConsumer)
    try:
        violations = assert_all_consumers_secure([mod.__name__])
        assert not any("_ExemptConsumer" in v for v in violations)
    finally:
        delattr(mod, "_ExemptConsumer")


def test_inventory_falsy_exempt_does_not_pass():
    """_WS_AUDIT_EXEMPT = '' or False must NOT count as exempt (S4 fix)."""
    from django_core_micha.auth import ws_permissions as mod

    attr_name = "_FalsyExemptTestConsumer"
    for falsy_val in ("", False, 0, None):
        class _FalsyExemptTestConsumer:
            pass
        _FalsyExemptTestConsumer._WS_AUDIT_EXEMPT = falsy_val
        _FalsyExemptTestConsumer.__module__ = mod.__name__
        setattr(mod, attr_name, _FalsyExemptTestConsumer)
        try:
            violations = assert_all_consumers_secure([mod.__name__])
            assert any(attr_name in v for v in violations), (
                f"falsy _WS_AUDIT_EXEMPT={falsy_val!r} should have produced a violation"
            )
        finally:
            delattr(mod, attr_name)


def test_inventory_base_secure_consumer_subclass_passes():
    from django_core_micha.auth import ws_permissions as mod

    class _SecureConsumer(BaseSecureConsumer):
        pass

    _SecureConsumer.__module__ = mod.__name__
    setattr(mod, "_SecureConsumer", _SecureConsumer)
    try:
        violations = assert_all_consumers_secure([mod.__name__])
        assert not any("_SecureConsumer" in v for v in violations)
    finally:
        delattr(mod, "_SecureConsumer")
