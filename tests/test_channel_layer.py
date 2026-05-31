"""Regression guard for the WS channel layer backend (2.17.3).

The BRPOP-polling `RedisChannelLayer` raises periodic `redis.exceptions.
TimeoutError` on idle WS connections with current redis-py / Python 3.14,
crashing consumers in a WSDISCONNECT loop. We pin the pub/sub layer instead.

Reads CHANNEL_LAYERS straight from the settings_base source via AST (importing
the module would trip its production env guards) and asserts the backend is the
pub/sub layer and that it is importable.
"""
from __future__ import annotations

import ast
import importlib
import pathlib

import django_core_micha

EXPECTED_BACKEND = "channels_redis.pubsub.RedisPubSubChannelLayer"


def _channel_layers_from_source() -> dict:
    base = pathlib.Path(django_core_micha.__file__).parent / "settings" / "settings_base.py"
    tree = ast.parse(base.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "CHANNEL_LAYERS":
                    # The CONFIG value contains an env() call → not a pure
                    # literal. Extract just the BACKEND string per layer.
                    backends = {}
                    for key, val in zip(node.value.keys, node.value.values):
                        layer = ast.literal_eval(key)
                        for k, v in zip(val.keys, val.values):
                            if ast.literal_eval(k) == "BACKEND":
                                backends[layer] = ast.literal_eval(v)
                    return backends
    raise AssertionError("CHANNEL_LAYERS not found in settings_base.py")


def test_default_backend_is_pubsub():
    backends = _channel_layers_from_source()
    assert backends.get("default") == EXPECTED_BACKEND, (
        f"default channel layer must be {EXPECTED_BACKEND!r} (BRPOP-based "
        f"RedisChannelLayer crashes on idle WS with current redis-py/py3.14)."
    )


def test_pubsub_backend_is_importable():
    module_path, cls_name = EXPECTED_BACKEND.rsplit(".", 1)
    module = importlib.import_module(module_path)
    assert hasattr(module, cls_name)


# Keys the pub/sub layer rejects with a TypeError at consumer startup.
_LEGACY_CONFIG_KEYS = {"expiry", "group_expiry", "capacity", "channel_capacity"}


def _default_config_keys() -> set[str]:
    """Key names of CHANNEL_LAYERS['default']['CONFIG'] (values may be env() calls)."""
    base = pathlib.Path(django_core_micha.__file__).parent / "settings" / "settings_base.py"
    tree = ast.parse(base.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "CHANNEL_LAYERS" for t in node.targets
        ):
            for key, layer in zip(node.value.keys, node.value.values):
                if ast.literal_eval(key) != "default":
                    continue
                for k, v in zip(layer.keys, layer.values):
                    if ast.literal_eval(k) == "CONFIG":
                        return {ast.literal_eval(ck) for ck in v.keys}
    raise AssertionError("CHANNEL_LAYERS['default']['CONFIG'] not found")


def test_config_has_hosts_and_no_legacy_keys():
    keys = _default_config_keys()
    assert "hosts" in keys
    leaked = keys & _LEGACY_CONFIG_KEYS
    assert not leaked, f"pub/sub layer rejects legacy CONFIG keys: {leaked}"
