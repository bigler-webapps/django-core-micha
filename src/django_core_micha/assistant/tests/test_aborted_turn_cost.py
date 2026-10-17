import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings

from django_core_micha.assistant.drivers.anthropic_api import AnthropicDriver

pytestmark = pytest.mark.django_db(transaction=True)


def test_anthropic_reservation_tracks_requested_max_tokens(monkeypatch):
    from django_core_micha.extraction import cost_guard
    user = get_user_model().objects.create_user(username="aborted-max-token", password="secret")
    cache.clear()
    original_reserve = cost_guard.reserve_user_budget
    # Capture the actual admitted amount while preserving real cache admission.
    def reserve(*, user_id, cents):
        captured.append(cents)
        return original_reserve(user_id=user_id, cents=cents)

    captured = []
    monkeypatch.setattr(cost_guard, "reserve_user_budget", reserve)

    class Stream:
        class Texts:
            def __aiter__(self):
                async def empty():
                    if False: yield ""
                return empty()
        text_stream = Texts()
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return None
        async def get_final_message(self):
            from anthropic.types import Message
            from anthropic.types.usage import Usage
            return Message.model_construct(id="m", type="message", role="assistant", content=[],
                model="claude-sonnet-5", stop_reason="end_turn", stop_sequence=None,
                usage=Usage.model_construct(input_tokens=5, output_tokens=1))

    class Messages:
        def __init__(self): self.requested = []
        def stream(self, **kwargs):
            self.requested.append(kwargs["max_tokens"])
            return Stream()

    messages = Messages()
    driver = AnthropicDriver(type("Client", (), {"messages": messages})())

    async def run(max_tokens):
        return [item async for item in driver.stream(model="claude-sonnet-5", messages=[], user=user,
            max_tokens=max_tokens)]

    with override_settings(AI_COST_LIMIT_ENABLED=True, AI_DAILY_COST_LIMIT_CENTS=5000):
        async_to_sync(run)(100)
        async_to_sync(run)(8192)
    assert captured[0] < captured[1]
    assert messages.requested == [100, 8192]
