from unittest.mock import AsyncMock
import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import override_settings
from django_core_micha.assistant.models import AssistantChat, AssistantTurn, AssistantSummary
from django_core_micha.assistant.turns import AccessLost, run_turn

pytestmark = pytest.mark.django_db(transaction=True)

@pytest.mark.parametrize('revoke', [True, False])
def test_context_revocation_and_history(monkeypatch, revoke):
    from django_core_micha.assistant import turns, provider_context
    user = get_user_model().objects.create_user(username='provider-context')
    chat = AssistantChat.objects.create(owner=user, selected_driver='openai_api', selected_model='test', session_reference='old-session')
    AssistantTurn.objects.create(chat=chat, sender=user, sequence=1, state='completed', user_text='withdrawn-marker', assistant_text='withdrawn-marker')
    AssistantSummary.objects.create(chat=chat, up_to_sequence=1, text='withdrawn-marker')
    token = [1]
    calls = []
    monkeypatch.setattr(provider_context, 'context_token', lambda *_: token[0])
    monkeypatch.setattr(turns, 'resolve_credential', lambda *_: ('key', 'deployment'))
    monkeypatch.setattr(turns, 'build_system_prompt', lambda *_: 'system')
    def context(*args, **kwargs):
        if revoke:
            token[0] = 2
            return 'withdrawn-marker'
        return ''
    monkeypatch.setattr(turns, 'build_turn_context', context)
    monkeypatch.setattr(turns, 'tools_for_chat', lambda *_: [])
    class Driver:
        async def stream(self, **kwargs):
            calls.append(kwargs)
            yield {'type': 'delta', 'text': 'ok'}
    monkeypatch.setattr(turns, '_make_driver', lambda *_: Driver())
    config = [{'driver':'openai_api', 'model':'test', 'name':'test', 'efforts':['medium'], 'billing':'pay_per_use', 'default':True}]
    with override_settings(DCM_ASSISTANT_CATALOGUE=config):
        if revoke:
            with pytest.raises(AccessLost):
                async_to_sync(run_turn)(chat, user, 'hello', AsyncMock())
        else:
            async_to_sync(run_turn)(chat, user, 'hello', AsyncMock())
    if revoke:
        assert calls == []
    else:
        assert len(calls) == 1
        assert 'withdrawn-marker' not in str(calls[0]['messages'])
        assert 'withdrawn-marker' not in calls[0]['system']
        assert calls[0]['session_id'] is None
