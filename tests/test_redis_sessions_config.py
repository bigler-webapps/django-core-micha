import ast
import pathlib

import django_core_micha


def _settings_base_tree():
    base = pathlib.Path(django_core_micha.__file__).parent / "settings" / "settings_base.py"
    return ast.parse(base.read_text(encoding="utf-8"))


def test_session_engine_cached_db():
    tree = _settings_base_tree()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "SESSION_ENGINE":
                    assert ast.literal_eval(node.value) == "django.contrib.sessions.backends.cached_db"
                    return
    raise AssertionError("SESSION_ENGINE not found in settings_base.py")


def test_caches_default_backend_is_redis():
    tree = _settings_base_tree()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "CACHES":
                    for key, val in zip(node.value.keys, node.value.values):
                        if ast.literal_eval(key) == "default":
                            for k, v in zip(val.keys, val.values):
                                if ast.literal_eval(k) == "BACKEND":
                                    assert ast.literal_eval(v) == "django.core.cache.backends.redis.RedisCache"
                                    return
    raise AssertionError("CACHES['default']['BACKEND'] not found in settings_base.py")
