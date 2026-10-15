import json

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import override_settings

from django_core_micha.assistant.models import AssistantChat, AssistantTurn

pytestmark = pytest.mark.django_db

@pytest.fixture(autouse=True)
def catalogue():
    with override_settings(DCM_ASSISTANT_CATALOGUE=[
        {"driver":"claude_cli","model":"claude-test","name":"CLI","efforts":["low","medium","high","max"],"billing":"subscription","default":True},
        {"driver":"openai_api","model":"model","name":"API","efforts":["low","medium","high","max"],"billing":"pay_per_use"},
    ]): yield

@pytest.fixture
def user(): return get_user_model().objects.create_user(username="session-user", password="secret")

@pytest.fixture
def chat(user): return AssistantChat.objects.create(owner=user,selected_driver="claude_cli",selected_model="claude-test")

def _run(monkeypatch,chat,user,driver,frames):
    from django_core_micha.assistant import turns
    monkeypatch.setattr(turns,"resolve_credential",lambda *_:("credential","deployment"))
    monkeypatch.setattr(turns,"build_system_prompt",lambda _:"system")
    monkeypatch.setattr(turns,"tool_registry",lambda:[])
    monkeypatch.setattr(turns,"_make_driver",lambda *_:driver)
    async def emit(frame): frames.append(frame)
    async def run(): return await turns.run_turn(chat,user,"question",emit)
    return async_to_sync(run)

def _session_file(chat,tmp_path,reference="stored"):
    chat.session_driver="claude_cli"; chat.session_reference=reference; chat.save()
    project=tmp_path/str(chat.pk)/"config"/"projects"/"project-a"; project.mkdir(parents=True)
    (project/f"{reference}.jsonl").write_text("{}\n",encoding="utf-8")

@pytest.mark.parametrize("reason",["tokens","version"])
def test_session_threshold_and_prompt_version_force_fresh_session(monkeypatch,chat,user,tmp_path,reason):
    _session_file(chat,tmp_path)
    chat.session_tokens=100; chat.session_prompt_version="old"; chat.save()
    seen=[]
    class Driver:
        async def stream(self,**kwargs):
            seen.append(kwargs); yield {"type":"delta","text":"answer"}; yield {"type":"usage","usage":{"input_tokens":7}}; yield {"type":"session","session_id":"fresh"}
    frames=[]
    limits={"session_rebuild_tokens":10} if reason=="tokens" else {}
    version=None if reason=="tokens" else "new"
    with override_settings(DCM_ASSISTANT_CLI_ROOT=tmp_path,DCM_ASSISTANT_PROMPT_VERSION=version,DCM_ASSISTANT_LIMITS=limits):
        _run(monkeypatch,chat,user,Driver(),frames)()
    assert seen[0]["session_id"] is None
    assert any(f.get("code")=="history_rebuilt" for f in frames)
    chat.refresh_from_db()
    assert chat.session_tokens == 7
    if reason=="version": assert chat.session_prompt_version == "new"

def test_resumed_session_accumulates_input_tokens(monkeypatch,chat,user,tmp_path):
    _session_file(chat,tmp_path); chat.session_tokens=4; chat.save()
    seen=[]
    class Driver:
        async def stream(self,**kwargs): seen.append(kwargs); yield {"type":"usage","usage":{"input_tokens":3}}; yield {"type":"session","session_id":"stored"}
    with override_settings(DCM_ASSISTANT_CLI_ROOT=tmp_path): _run(monkeypatch,chat,user,Driver(),[])()
    assert seen[0]["session_id"] == "stored"
    chat.refresh_from_db(); assert chat.session_tokens == 7

def test_history_budget_trims_oldest_turns_and_emits_notice(monkeypatch,user):
    chat=AssistantChat.objects.create(owner=user,selected_driver="openai_api",selected_model="model")
    for seq in range(1,4):
        AssistantTurn.objects.create(chat=chat,sequence=seq,user_text=("old-question-"+str(seq))*3,assistant_text=("old-answer-"+str(seq))*3,state="completed")
    seen={}
    class Driver:
        async def stream(self,**kwargs): seen.update(kwargs); yield {"type":"delta","text":"ok"}
    frames=[]
    with override_settings(DCM_ASSISTANT_LIMITS={"max_prompt_bytes":250}): _run(monkeypatch,chat,user,Driver(),frames)()
    notice=next(frame for frame in frames if frame.get("code")=="history_trimmed")
    assert notice["left_out"] == 2
    assert seen["messages"][-1]["content"] == "question"
    assert len(seen["messages"]) < 7
