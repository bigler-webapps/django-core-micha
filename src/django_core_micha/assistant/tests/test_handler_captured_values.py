"""Equivalence of recreated handlers with bounded safe captures."""
from django_core_micha.assistant.drivers.base import _same_handler


def handler(value):
    def run(*args):
        return value
    return run


def test_equal_json_captures_and_changed_values():
    assert _same_handler(handler({"items": [1, {"enabled": True}]}),
                         handler({"items": [1, {"enabled": True}]}))
    assert not _same_handler(handler({"items": [1]}), handler({"items": [2]}))
    assert not _same_handler(handler([True]), handler([1]))


def test_sensitive_opaque_and_unbounded_captures_fail_closed():
    assert not _same_handler(handler({"api_key": "x"}), handler({"api_key": "x"}))
    assert not _same_handler(handler([object()]), handler([object()]))
    assert not _same_handler(handler(list(range(1001))), handler(list(range(1001))))
    left, right = [], []
    left.append(left)
    right.append(right)
    assert not _same_handler(handler(left), handler(right))


def test_distinct_service_instances_are_not_equivalent():
    class Service:
        def __init__(self):
            self.config = {"mode": "read"}
    assert not _same_handler(handler(Service()), handler(Service()))
