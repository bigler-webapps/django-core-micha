import pytest

from django_core_micha.notifications.todo import registry
from django_core_micha.notifications.todo.registry import TodoSeed, TodoTypeConfig
from django_core_micha.notifications.types import NotificationType


@pytest.fixture(autouse=True)
def clear_todo_registry():
    registry._PROVIDERS.clear()
    registry._CONFIGS.clear()
    registry._CANDIDATE_USERS.clear()
    yield
    registry._PROVIDERS.clear()
    registry._CONFIGS.clear()
    registry._CANDIDATE_USERS.clear()


def test_registry_lookup_unknown_and_reregistration_replacement():
    def first(user, now):
        return []

    def replacement(user, now):
        return []

    config = TodoTypeConfig(type_key="demo_todo")
    registry.register_todo_provider("demo_todo", first, config=config)
    assert registry.get_todo_provider("demo_todo") is first
    assert registry.get_todo_config("demo_todo") == config
    with pytest.raises(LookupError):
        registry.get_todo_provider("missing")
    with pytest.raises(LookupError):
        registry.get_todo_config("missing")

    registry.register_todo_provider("demo_todo", replacement, config=config)
    assert registry.get_todo_provider("demo_todo") is replacement
    assert tuple(registry.iter_registered_todo_types()) == ("demo_todo",)


def test_provider_is_a_valid_notification_type_mode():
    assert NotificationType(
        key="demo_todo",
        category="todo",
        mode="provider",
        resolution="state-resolved",
    ).mode == "provider"


def test_unknown_notification_mode_is_rejected():
    with pytest.raises(ValueError):
        NotificationType(
            key="demo_todo",
            category="todo",
            mode="bogus",
            resolution="state-resolved",
        )
