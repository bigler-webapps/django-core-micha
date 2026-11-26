import copy
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import override_settings

from django_core_micha.assistant import group_grants, turns
from django_core_micha.assistant.catalogue_activation import activate_catalogue
from django_core_micha.assistant.drivers import openai_api, anthropic_api
from django_core_micha.assistant.drivers.base import DriverError
from django_core_micha.assistant.hooks import ToolSpec
from django_core_micha.assistant.models import AssistantChat, AssistantTurn

pytestmark = pytest.mark.django_db(transaction=True)
MARKER = 'withdrawn-private-marker'
GROUPS = [{'id': 'g', 'label': 'Group', 'kind': 'read', 'members': [
    {'id': 'read', 'label': 'Read', 'available': True, 'tools': ['read'], 'selectors': ['data']}
]}]


@pytest.mark.parametrize('provider', ['openai', 'anthropic'])
@pytest.mark.parametrize('deny_after_tool', [False, True])
def test_authorization_boundaries_without_execution(monkeypatch, provider, deny_after_tool):
    requests, invocations, authorizations = [], [], []
    tool = ToolSpec('read', 'Read', 'Read', {}, lambda *_: ('unused', []))

    async def invoke(*args):
        invocations.append('returned')
        return MARKER, []

    async def authorize():
        allowed = deny_after_tool and not invocations
        authorizations.append((len(invocations), allowed))
        return allowed

    class Responses:
        async def create(self, **request):
            requests.append(copy.deepcopy(request))
            async def events():
                call = NS(type='function_call', id='item', call_id='call', name='read', arguments='{}')
                yield NS(type='response.completed', response=NS(usage=NS(input_tokens=1, output_tokens=1), output=[call]))
            return events()

    class Stream:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        text_stream = property(lambda self: self.__aiter__())
        def __aiter__(self):
            async def events():
                if False: yield None
            return events()
        async def get_final_message(self):
            return NS(usage=NS(input_tokens=1, output_tokens=1), stop_reason='tool_use',
                      content=[NS(type='tool_use', id='call', name='read', input={})])

    class Messages:
        def stream(self, **request):
            requests.append(copy.deepcopy(request))
            return Stream()

    module = openai_api if provider == 'openai' else anthropic_api
    driver = (openai_api.OpenAIDriver(NS(responses=Responses())) if provider == 'openai'
              else anthropic_api.AnthropicDriver(NS(messages=Messages())))
    monkeypatch.setattr(module, 'invoke_tool', invoke)
    monkeypatch.setattr(openai_api, '_reserve', lambda *args: object())
    monkeypatch.setattr('django_core_micha.assistant.drivers.base._reserve', lambda *args: object())
    monkeypatch.setattr('django_core_micha.extraction.cost_guard.settle_reservation', lambda **kwargs: None)
    monkeypatch.setattr('django_core_micha.extraction.cost_guard.release_reservation', lambda **kwargs: None)
    monkeypatch.setattr(openai_api, 'release_reservation', lambda **kwargs: None)

    async def run():
        return [event async for event in driver.stream(model='m', messages=[{'role': 'user', 'content': 'question'}],
                                                       tools=[tool], user=NS(pk=1), chat=None, authorize=authorize)]

    with pytest.raises(DriverError) as error:
        async_to_sync(run)()
    assert error.value.code == 'access_lost'
    assert len(requests) == (1 if deny_after_tool else 0)
    assert invocations == (['returned'] if deny_after_tool else [])
    assert authorizations[-1] == (1 if deny_after_tool else 0, False)
    assert MARKER not in str(requests)


def test_provider_continuation_discards_tool_result_revoked_during_execution(monkeypatch):
    user = get_user_model().objects.create_user(username='continuation-user')
    chat = AssistantChat.objects.create(owner=user)
    requests, calls = [], []

    def handler(user, chat, arguments, trust):
        calls.append(True)
        AssistantChat.objects.filter(pk=chat.pk).update(group_grants={}, grant_revision=1)
        return MARKER, []

    tool = ToolSpec('read', 'Read', 'Read', {}, handler)
    class Responses:
        async def create(self, **request):
            requests.append(copy.deepcopy(request))
            async def events():
                call = NS(type='function_call', id='item', call_id='call', name='read', arguments='{}')
                yield NS(type='response.completed', response=NS(usage=NS(input_tokens=1, output_tokens=1), output=[call]))
            return events()
    driver = openai_api.OpenAIDriver(NS(responses=Responses()))
    monkeypatch.setattr(openai_api, '_reserve', lambda *args: object())
    monkeypatch.setattr('django_core_micha.extraction.cost_guard.settle_reservation', lambda **kwargs: None)

    async def run():
        return [event async for event in driver.stream(model='m', messages=[{'role': 'user', 'content': 'question'}],
                                                       tools=[tool], user=user, chat=chat)]
    activate_catalogue(GROUPS, deployment='provider', expected_revision=0)
    with override_settings(DCM_ASSISTANT_GROUP_AUTHORITATIVE_CATALOGUE=lambda: GROUPS,
                           DCM_ASSISTANT_GROUP_DEPLOYMENT='provider',
                           DCM_ASSISTANT_GROUP_CATALOGUE=lambda *_: GROUPS,
                           DCM_ASSISTANT_TOOL_REGISTRY=[tool]):
        group_grants.save_selection(chat, group_grants.catalogue(chat, user), ['g'])
        with pytest.raises(DriverError) as error:
            async_to_sync(run)()
    assert error.value.code == 'access_lost'
    assert calls == [True] and len(requests) == 1
    assert MARKER not in str(requests)


def test_anthropic_does_not_continue_after_handler_revokes(monkeypatch):
    user = get_user_model().objects.create_user(username='anthropic-continuation')
    chat = AssistantChat.objects.create(owner=user)
    requests, calls = [], []
    def handler(actor, current, arguments, trust):
        calls.append(True)
        AssistantChat.objects.filter(pk=current.pk).update(group_grants={}, grant_revision=2)
        return MARKER, []
    tool = ToolSpec('read', 'Read', 'Read', {}, handler)
    class Stream:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        text_stream = property(lambda self: self.__aiter__())
        def __aiter__(self):
            async def events():
                if False: yield None
            return events()
        async def get_final_message(self):
            return NS(usage=NS(input_tokens=1, output_tokens=1), stop_reason='tool_use',
                      content=[NS(type='tool_use', id='call', name='read', input={})])
    class Messages:
        def stream(self, **request):
            requests.append(copy.deepcopy(request))
            return Stream()
    driver = anthropic_api.AnthropicDriver(NS(messages=Messages()))
    monkeypatch.setattr('django_core_micha.assistant.drivers.base._reserve', lambda *args: object())
    monkeypatch.setattr('django_core_micha.extraction.cost_guard.settle_reservation', lambda **kwargs: None)
    async def run():
        return [event async for event in driver.stream(model='m', messages=[{'role': 'user', 'content': 'question'}],
                                                       tools=[tool], user=user, chat=chat)]
    activate_catalogue(GROUPS, deployment='provider', expected_revision=0)
    with override_settings(DCM_ASSISTANT_GROUP_AUTHORITATIVE_CATALOGUE=lambda: GROUPS,
                           DCM_ASSISTANT_GROUP_DEPLOYMENT='provider',
                           DCM_ASSISTANT_GROUP_CATALOGUE=lambda *_: GROUPS,
                           DCM_ASSISTANT_TOOL_REGISTRY=[tool]):
        group_grants.save_selection(chat, group_grants.catalogue(chat, user), ['g'])
        with pytest.raises(DriverError) as error:
            async_to_sync(run)()
    assert error.value.code == 'access_lost'
    assert calls == [True] and len(requests) == 1
    assert MARKER not in str(requests)


def test_opted_in_cli_does_not_resume_withdrawn_session(monkeypatch, tmp_path):
    user = get_user_model().objects.create_user(username='resume-user')
    chat = AssistantChat.objects.create(owner=user, selected_driver='claude_cli', selected_model='cli-test',
        selected_effort='medium', session_driver='claude_cli', session_reference='old-session', group_grants={}, grant_revision=1)
    AssistantTurn.objects.create(chat=chat, sequence=1, user_text=MARKER, assistant_text=MARKER, state='completed')
    project = tmp_path / str(chat.pk) / 'config' / 'projects' / 'project-a'
    project.mkdir(parents=True)
    (project / 'old-session.jsonl').write_text(MARKER, encoding='utf-8')
    seen = []
    class Driver:
        async def stream(self, **kwargs):
            seen.append(kwargs)
            yield {'type': 'delta', 'text': 'ok'}
    monkeypatch.setattr(turns, 'resolve_credential', lambda *_: ('credential', 'deployment'))
    monkeypatch.setattr(turns, 'build_system_prompt', lambda _: 'system')
    monkeypatch.setattr(turns, 'tools_for_chat', lambda _: [])
    monkeypatch.setattr(turns, '_make_driver', lambda *_: Driver())
    catalogue = [{'driver': 'claude_cli', 'model': 'cli-test', 'name': 'CLI',
                  'efforts': ['low', 'medium', 'high', 'max'], 'billing': 'subscription', 'default': True}]
    activate_catalogue(GROUPS, deployment='provider', expected_revision=0)
    with override_settings(DCM_ASSISTANT_GROUP_AUTHORITATIVE_CATALOGUE=lambda: GROUPS,
                           DCM_ASSISTANT_GROUP_DEPLOYMENT='provider',
                           DCM_ASSISTANT_CATALOGUE=catalogue, DCM_ASSISTANT_CLI_ROOT=tmp_path,
                           DCM_ASSISTANT_GROUP_CATALOGUE=lambda *_: GROUPS):
        async_to_sync(turns.run_turn)(chat, user, 'new question', AsyncMock())
    assert len(seen) == 1
    assert seen[0]['session_id'] is None
    assert MARKER not in seen[0]['prompt']
    assert MARKER not in str(seen[0]['messages'])



def test_provider_continuation_is_fenced_by_inflight_activation(monkeypatch):
    user = get_user_model().objects.create_user(username="activation-continuation")
    chat = AssistantChat.objects.create(owner=user)
    requests, calls = [], []

    def handler(*_):
        calls.append(True)
        activate_catalogue(GROUPS, deployment="next", expected_revision=1)
        from django.conf import settings
        settings.DCM_ASSISTANT_GROUP_DEPLOYMENT = "next"
        return MARKER, []

    tool = ToolSpec("read", "Read", "Read", {}, handler)
    class Responses:
        async def create(self, **request):
            requests.append(copy.deepcopy(request))
            async def events():
                call = NS(type="function_call", id="item", call_id="call", name="read", arguments="{}")
                yield NS(type="response.completed", response=NS(usage=NS(input_tokens=1, output_tokens=1), output=[call]))
            return events()

    driver = openai_api.OpenAIDriver(NS(responses=Responses()))
    monkeypatch.setattr(openai_api, "_reserve", lambda *args: object())
    monkeypatch.setattr("django_core_micha.extraction.cost_guard.settle_reservation", lambda **kwargs: None)
    activate_catalogue(GROUPS, deployment="initial", expected_revision=0)
    async def run():
        return [event async for event in driver.stream(model="m", messages=[{"role": "user", "content": "question"}],
                                                       tools=[tool], user=user, chat=chat)]
    with override_settings(DCM_ASSISTANT_GROUP_CATALOGUE=lambda *_: GROUPS,
                           DCM_ASSISTANT_GROUP_AUTHORITATIVE_CATALOGUE=lambda: GROUPS,
                           DCM_ASSISTANT_GROUP_DEPLOYMENT="initial", DCM_ASSISTANT_TOOL_REGISTRY=[tool]):
        group_grants.save_selection(chat, group_grants.catalogue(chat, user), ["g"])
        with pytest.raises(DriverError) as error:
            async_to_sync(run)()
    assert error.value.code == "access_lost"
    assert calls == [True] and len(requests) == 1
    assert MARKER not in str(requests)


def test_provider_continuation_discards_result_after_authorization_generation_changes(monkeypatch):
    user = get_user_model().objects.create_user(username='generation-continuation')
    chat = AssistantChat.objects.create(owner=user)
    requests, calls = [], []
    generation = [0]

    def handler(user, chat, arguments, trust):
        calls.append(True)
        generation[0] += 1
        return MARKER, []

    tool = ToolSpec('read', 'Read', 'Read', {}, handler)
    class Responses:
        async def create(self, **request):
            requests.append(copy.deepcopy(request))
            async def events():
                call = NS(type='function_call', id='item', call_id='call', name='read', arguments='{}')
                yield NS(type='response.completed', response=NS(usage=NS(input_tokens=1, output_tokens=1), output=[call]))
            return events()
    driver = openai_api.OpenAIDriver(NS(responses=Responses()))
    monkeypatch.setattr(openai_api, '_reserve', lambda *args: object())
    monkeypatch.setattr('django_core_micha.extraction.cost_guard.settle_reservation', lambda **kwargs: None)

    async def run():
        return [event async for event in driver.stream(model='m', messages=[{'role': 'user', 'content': 'question'}],
                                                       tools=[tool], user=user, chat=chat)]
    activate_catalogue(GROUPS, deployment='provider', expected_revision=0)
    with override_settings(DCM_ASSISTANT_GROUP_AUTHORITATIVE_CATALOGUE=lambda: GROUPS,
                           DCM_ASSISTANT_GROUP_DEPLOYMENT='provider',
                           DCM_ASSISTANT_GROUP_CATALOGUE=lambda *_: GROUPS,
                           DCM_ASSISTANT_TOOL_REGISTRY=[tool],
                           DCM_ASSISTANT_LOCK_AUTHORIZATION_SNAPSHOT=lambda _: {
                               'current': {'global': 0, 'user': generation[0]},
                               'baseline': None}):
        group_grants.save_selection(chat, group_grants.catalogue(chat, user), ['g'])
        with pytest.raises(DriverError) as error:
            async_to_sync(run)()
    assert error.value.code == 'access_lost'
    assert calls == [True] and len(requests) == 1
    assert MARKER not in str(requests)

