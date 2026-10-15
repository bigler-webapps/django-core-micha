import asyncio
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.core.checks import Error
from django.db import IntegrityError, transaction
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from django_core_micha.assistant.catalogue import catalogue_check, validate
from django_core_micha.assistant.consumers import ChatConsumer
from django_core_micha.assistant.credentials import resolve_credential
from django_core_micha.assistant.drivers.base import DriverError
from django_core_micha.assistant.drivers.claude_cli import ClaudeCliDriver, safe_environment
from django_core_micha.assistant.models import AssistantChat, AssistantTurn
from django_core_micha.assistant.turns import _finish, reconcile_interrupted

pytestmark = pytest.mark.django_db

@pytest.fixture(autouse=True)
def catalogue_settings():
    with override_settings(DCM_ASSISTANT_CATALOGUE=[{"driver":"openai_api","model":"gpt-4o-mini","name":"Mini",
        "efforts":["low","medium","high","max"],"billing":"subscription","default":True}]):
        yield

@pytest.fixture
def user(): return get_user_model().objects.create_user(username="assistant-user", password="secret")

@pytest.fixture
def chat(user):
    return AssistantChat.objects.create(owner=user, selected_driver="openai_api", selected_model="gpt-4o-mini", selected_effort="medium")

def test_one_generating_turn_per_chat(user, chat):
    AssistantTurn.objects.create(chat=chat, sequence=1, user_text="a")
    with pytest.raises(IntegrityError), transaction.atomic(): AssistantTurn.objects.create(chat=chat, sequence=2, user_text="b")

def test_stale_reconciliation_and_stop_finish(chat):
    stale=AssistantTurn.objects.create(chat=chat,sequence=1,user_text="stale",heartbeat_at=timezone.now()-timezone.timedelta(days=1))
    assert reconcile_interrupted(chat)==1; stale.refresh_from_db(); assert stale.state=="interrupted"
    running=AssistantTurn.objects.create(chat=chat,sequence=2,user_text="stop")
    _finish(running.pk,"partial","cancelled","cancelled")
    running.refresh_from_db(); assert running.state=="cancelled" and running.error_code=="cancelled"

def test_automatic_title_never_overwrites_user_title(chat):
    row=AssistantTurn.objects.create(chat=chat,sequence=1,user_text="a useful first question")
    chat.title="Renamed"; chat.title_source="user"; chat.save()
    _finish(row.pk,"answer","completed")
    chat.refresh_from_db(); assert chat.title=="Renamed"

def test_auto_title_is_created_from_first_completed_message(chat):
    row=AssistantTurn.objects.create(chat=chat,sequence=1,user_text="  How do I build this?  ")
    _finish(row.pk,"answer","completed")
    chat.refresh_from_db(); assert chat.title=="How do I build this?"

def test_history_rebuild_uses_database_after_driver_switch(chat):
    from django_core_micha.assistant.history import build_history
    AssistantTurn.objects.create(chat=chat,sequence=1,user_text="question",assistant_text="answer",state="completed")
    chat.session_driver="claude_cli"; chat.session_reference="session"; chat.selected_driver="anthropic_api"; chat.save()
    assert build_history(chat)==[{"role":"user","content":"question"},{"role":"assistant","content":"answer"}]

def _run_turn_with_driver(turns, monkeypatch, driver, chat, user, frames):
    monkeypatch.setattr(turns,"resolve_credential",lambda u,d:("credential","deployment"))
    monkeypatch.setattr(turns,"build_system_prompt",lambda c:"system")
    monkeypatch.setattr(turns,"tool_registry",lambda:[])
    monkeypatch.setattr(turns,"_make_driver",lambda *a:driver)
    async def collect(frame): frames.append(frame)
    async def run(): return await turns.run_turn(chat,user,"question",collect)
    return async_to_sync(run)

def test_long_single_word_first_message_still_titles_the_chat(chat):
    row=AssistantTurn.objects.create(chat=chat,sequence=1,user_text="x"*80)
    _finish(row.pk,"answer","completed")
    chat.refresh_from_db(); assert chat.title=="x"*60

def test_failed_turn_emits_final_turn_frame_before_error(user,chat,monkeypatch):
    from django_core_micha.assistant import turns
    class Failing:
        async def stream(self,**kwargs):
            yield {"type":"delta","text":"partial"}
            raise DriverError("provider_error")
    frames=[]
    with pytest.raises(DriverError): _run_turn_with_driver(turns,monkeypatch,Failing(),chat,user,frames)()
    assert frames[-1]["type"]=="turn" and frames[-1]["turn"]["state"]=="failed" and frames[-1]["turn"]["assistant_text"]=="partial"

def test_history_rebuild_happens_after_driver_switch_through_a_turn(user,chat,monkeypatch):
    from django_core_micha.assistant import turns
    AssistantTurn.objects.create(chat=chat,sequence=1,user_text="earlier",assistant_text="reply",state="completed")
    chat.session_driver="claude_cli"; chat.session_reference="old-session"; chat.save()
    seen={}
    class Capture:
        async def stream(self,**kwargs):
            seen.update(kwargs); yield {"type":"delta","text":"ok"}
    _run_turn_with_driver(turns,monkeypatch,Capture(),chat,user,[])()
    assert seen["session_id"] is None
    assert seen["messages"][:2]==[{"role":"user","content":"earlier"},{"role":"assistant","content":"reply"}]
    chat.refresh_from_db(); assert chat.session_reference==""

def test_concurrent_start_over_the_user_limit_is_rejected_after_commit(user):
    from django_core_micha.assistant.turns import _over_limit
    first=AssistantChat.objects.create(owner=user,selected_driver="openai_api",selected_model="gpt-4o-mini")
    second=AssistantChat.objects.create(owner=user,selected_driver="openai_api",selected_model="gpt-4o-mini")
    a=AssistantTurn.objects.create(chat=first,sequence=1,user_text="a",heartbeat_at=timezone.now())
    b=AssistantTurn.objects.create(chat=second,sequence=1,user_text="b",heartbeat_at=timezone.now())
    with override_settings(DCM_ASSISTANT_LIMITS={"max_turns_per_user":1}):
        assert _over_limit(a,user.pk) is False and _over_limit(b,user.pk) is True

def test_catalogue_selection_effort_and_rate_table():
    rows=[{"driver":"openai_api","model":"gpt-4o-mini","name":"Mini","efforts":["low"],"billing":"pay_per_use","default":True}]
    with override_settings(DCM_ASSISTANT_CATALOGUE=rows,AI_MODEL_COST_TABLE_CENTS_PER_1K={}):
        with pytest.raises(ValueError): validate("openai_api","other","low")
        with pytest.raises(ValueError): validate("openai_api","gpt-4o-mini","high")
        errors=catalogue_check(None)
        assert errors and isinstance(errors[0],Error) and errors[0].id=="dcm_assistant.E001"

def test_credential_resolution_deployment_billing(user):
    with override_settings(OPENAI_API_KEY="sk-deploy",CLAUDE_CODE_OAUTH_TOKEN="oauth-deploy"):
        assert resolve_credential(user,"openai_api")==("sk-deploy","deployment")
        assert resolve_credential(user,"claude_cli")==("oauth-deploy","deployment")

def test_tool_handler_receives_cloud_trust_and_turn_sources_are_stored(user, chat, monkeypatch):
    from django_core_micha.assistant import turns
    seen=[]
    class FakeDriver:
        async def stream(self, **kwargs):
            seen.append("provider")
            yield {"type":"tool","phase":"end","name":"search","label":"Search","sources":[{"label":"Doc","reference":"ref-1"}]}
            yield {"type":"delta","text":"answer"}
            yield {"type":"usage","usage":{"input_tokens":2,"output_tokens":1}}
    resolver=Mock(return_value=("credential","deployment")); monkeypatch.setattr(turns,"resolve_credential",resolver)
    monkeypatch.setattr(turns,"build_system_prompt",lambda c:"system")
    monkeypatch.setattr(turns,"tool_registry",lambda:[])
    monkeypatch.setattr(turns,"_make_driver",lambda *a:FakeDriver())
    frames=[]
    async def run(): return await turns.run_turn(chat,user,"question",lambda frame: collect(frames,frame))
    async def unused(): pass
    async def collect(frames,frame): frames.append(frame)
    result=async_to_sync(run)()
    assert resolver.call_count==1 and result.billing_source=="deployment"
    assert frames[-1]["type"]=="turn" and frames[-1]["turn"]["sources"]==[{"label":"Doc","reference":"ref-1"}]

def test_registered_tool_handler_gets_trust_class(user,chat):
    from django_core_micha.assistant.drivers.base import invoke_tool
    seen=[]
    async def handler(u,c,args,trust): seen.append((u.pk,c.pk,args,trust)); return "data",[{"label":"L","reference":"R"}]
    tool=NS(handler=handler)
    assert async_to_sync(invoke_tool)(tool,user,chat,{"q":"x"},"cloud")==("data",[{"label":"L","reference":"R"}])
    assert seen==[(user.pk,chat.pk,{"q":"x"},"cloud")]

def test_openai_stream_and_tool_round_trip_reserves_each_call(monkeypatch, user):
    from django_core_micha.assistant.drivers import openai_api
    trust=[]
    tool=NS(name="lookup",description="lookup",input_schema={"type":"object"},label="Lookup",
        handler=lambda u,c,a,t:(trust.append(t) or f"found:{a['q']}",[{"label":"Source","reference":"x"}]))
    calls=[]
    class Responses:
        async def create(self,**kwargs):
            calls.append(kwargs)
            async def stream():
                if len(calls)==1:
                    yield NS(type="response.completed",response=NS(usage=NS(input_tokens=3,output_tokens=1),output=[NS(type="function_call",id="item",call_id="call",name="lookup",arguments='{"q":"a"}')]))
                else:
                    yield NS(type="response.output_text.delta",delta="hello")
                    yield NS(type="response.completed",response=NS(usage=NS(input_tokens=4,output_tokens=2),output=[]))
            return stream()
    accounting=[]
    monkeypatch.setattr(openai_api,"_reserve",lambda *a:accounting.append("reserve") or object())
    monkeypatch.setattr(openai_api,"settle_reservation",lambda **kw:accounting.append("settle"))
    monkeypatch.setattr(openai_api,"release_reservation",lambda **kw:None)
    driver=openai_api.OpenAIDriver(NS(responses=Responses()))
    async def run(): return [e async for e in driver.stream(model="gpt-4o-mini",messages=[],tools=[tool],user=user,chat=None)]
    events=async_to_sync(run)()
    assert trust==["cloud"]
    assert any(r.get("type")=="function_call_output" and r["output"]=="found:a" for r in calls[1]["input"])
    assert len(calls)==2 and any(e.get("text")=="hello" for e in events)
    assert any(e.get("phase")=="end" and e["sources"][0]["reference"]=="x" for e in events)
    assert accounting==["reserve","settle","reserve","settle"]

def test_openai_cost_limit_blocks_before_provider_call(monkeypatch):
    from django_core_micha.assistant.drivers.openai_api import OpenAIDriver
    from django_core_micha.assistant.drivers import base
    from django_core_micha.assistant.drivers import openai_api
    monkeypatch.setattr(openai_api,"_reserve",Mock(side_effect=DriverError("quota_exceeded")))
    client=NS(responses=NS(create=AsyncMock()))
    async def run(): return [e async for e in OpenAIDriver(client).stream(model="m",messages=[])]
    with pytest.raises(DriverError,match="quota_exceeded"): async_to_sync(run)()
    client.responses.create.assert_not_called()

def test_anthropic_native_tool_round_trip_and_cost_per_provider_call(monkeypatch,user):
    from django_core_micha.assistant.drivers import base, anthropic_api
    from django_core_micha.extraction import cost_guard
    trust=[]
    tool=NS(name="lookup",description="lookup",input_schema={},label="Lookup",
        handler=lambda u,c,a,t:(trust.append(t) or "answer",[{"label":"Source","reference":"s"}]))
    count=[]
    monkeypatch.setattr(base,"_reserve",lambda *a:count.append("reserve") or object())
    monkeypatch.setattr(cost_guard,"settle_reservation",lambda **k:count.append("settle"))
    monkeypatch.setattr(cost_guard,"release_reservation",lambda **k:None)
    class Stream:
        @property
        def text_stream(self):
            async def words():
                if not self.tool_call: yield "final answer"
            return words()
        def __init__(self,tool_call): self.tool_call=tool_call
        async def __aenter__(self): return self
        async def __aexit__(self,*a): pass
        async def get_final_message(self):
            return NS(usage=NS(input_tokens=2,output_tokens=1),content=[NS(type="tool_use",id="id",name="lookup",input={"q":"a"})] if self.tool_call else [])
    calls=[]
    def create(**kwargs): calls.append(kwargs); return Stream(len(calls)==1)
    client=NS(messages=NS(stream=create))
    async def run(): return [e async for e in anthropic_api.AnthropicDriver(client).stream(model="claude-sonnet-5",messages=[],tools=[tool],user=user)]
    events=async_to_sync(run)()
    assert trust==["cloud"]
    assert any(isinstance(r["content"],list) and r["content"][0].get("type")=="tool_result" and r["content"][0]["content"]=="answer"
        for r in calls[1]["messages"])
    assert len(calls)==2 and count==["reserve","settle","reserve","settle"]
    assert [e["text"] for e in events if e.get("type")=="delta"]==["final answer"]
    assert any(e.get("type")=="tool" and e.get("phase")=="end" for e in events)

def test_cli_environment_and_stdin_prompt_and_init_fail_closed(monkeypatch,tmp_path):
    import django_core_micha.assistant.drivers.claude_cli as cli
    monkeypatch.setattr(cli.shutil,"which",lambda name:"claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY","must-not-pass"); monkeypatch.setenv("UNRELATED_SECRET","no")
    env=safe_environment("oauth",tmp_path)
    assert env["CLAUDE_CODE_OAUTH_TOKEN"]=="oauth" and "ANTHROPIC_API_KEY" not in env and "UNRELATED_SECRET" not in env
    class Stdin:
        value=b""
        def write(self,data): self.value+=data
        async def drain(self): pass
        def close(self): pass
        async def wait_closed(self): pass
    class Stdout:
        def __init__(self,rows): self.rows=iter(rows)
        async def readline(self): return next(self.rows,b"")
    class Stderr:
        async def read(self): return b"private diagnostic"
    class Process:
        pid=123; returncode=None
        async def wait(self): self.returncode=0; return 0
    proc=Process(); proc.stdin=Stdin(); proc.stdout=Stdout([b'{"type":"system","subtype":"init","tools":["unexpected"],"mcp_servers":[]}\n']); proc.stderr=Stderr()
    killed=[]
    async def factory(*args,**kwargs):
        assert "--tools" in args and args[args.index("--tools")+1]==""
        assert "CLAUDE_CODE_OAUTH_TOKEN" in kwargs["env"] and "ANTHROPIC_API_KEY" not in kwargs["env"]
        return proc
    driver=ClaudeCliDriver(process_factory=factory,kill_tree=lambda p:killed.append(p))
    chat=NS(pk="chat-test")
    async def run(): return [e async for e in driver.stream(prompt="stdin-secret",tools=[],credential="oauth",effort="low",chat=chat)]
    with override_settings(DCM_ASSISTANT_CLI_ROOT=tmp_path):
        with pytest.raises(DriverError) as error: async_to_sync(run)()
    assert error.value.code=="security_violation" and proc.stdin.value==b"stdin-secret" and killed

def test_cli_quota_detection(monkeypatch,tmp_path):
    import django_core_micha.assistant.drivers.claude_cli as cli
    monkeypatch.setattr(cli.shutil,"which",lambda name:"claude")
    class Input:
        def write(self,data): self.value=data
        async def drain(self): pass
        def close(self): pass
        async def wait_closed(self): pass
    class Output:
        def __init__(self): self.lines=iter([b'{"type":"system","subtype":"init","tools":[],"mcp_servers":[]}\n',b'{"type":"rate_limit_event","rate_limit_info":{"status":"rejected"}}\n'])
        async def readline(self): return next(self.lines,b"")
    class Proc:
        pid=1; returncode=None
        def __init__(self): self.stdin=Input(); self.stdout=Output(); self.stderr=NS(read=AsyncMock(return_value=b""))
        async def wait(self): self.returncode=0; return 0
    proc=Proc(); killed=[]
    async def factory(*a,**k): return proc
    driver=ClaudeCliDriver(process_factory=factory,kill_tree=lambda p:killed.append(p))
    async def run(): return [e async for e in driver.stream(prompt="p",model="m",tools=[],credential="x",chat=NS(pk="quota"))]
    with override_settings(DCM_ASSISTANT_CLI_ROOT=tmp_path):
        with pytest.raises(DriverError) as exc: async_to_sync(run)()
    assert exc.value.code=="quota_exceeded" and killed

def test_cli_timeout_kills_process_tree(monkeypatch,tmp_path):
    import django_core_micha.assistant.drivers.claude_cli as cli
    monkeypatch.setattr(cli.shutil,"which",lambda name:"claude")
    class Input:
        def write(self,data): pass
        async def drain(self): pass
        def close(self): pass
        async def wait_closed(self): pass
    class Output:
        async def readline(self): await asyncio.Event().wait()
    class Proc:
        pid=1; returncode=None
        def __init__(self): self.stdin=Input(); self.stdout=Output(); self.stderr=NS(read=AsyncMock(return_value=b""))
        async def wait(self): self.returncode=0; return 0
    proc=Proc(); killed=[]
    async def factory(*a,**k): return proc
    driver=ClaudeCliDriver(process_factory=factory,kill_tree=lambda p:killed.append(p))
    async def run(): return [e async for e in driver.stream(prompt="p",model="m",tools=[],credential="x",chat=NS(pk="timeout"))]
    with override_settings(DCM_ASSISTANT_CLI_ROOT=tmp_path,DCM_ASSISTANT_LIMITS={"provider_timeout":1}):
        with pytest.raises(DriverError) as exc: async_to_sync(run)()
    assert exc.value.code=="timeout" and killed

def test_rest_foreign_owner_and_archived_write_refused(user,chat):
    from django_core_micha.assistant.views import ChatDetailView
    other=get_user_model().objects.create_user(username="other")
    factory=APIRequestFactory(); request=factory.get("/"); force_authenticate(request,user=other)
    response=ChatDetailView.as_view()(request,chat_id=chat.pk); assert response.status_code==404
    chat.archived_at=timezone.now(); chat.save()
    request=factory.patch("/",{"title":"change"},format="json"); force_authenticate(request,user=user)
    response=ChatDetailView.as_view()(request,chat_id=chat.pk); assert response.status_code==409 and response.data["code"]=="archived"

def test_socket_permission_denies_another_users_chat(user,chat):
    from django_core_micha.assistant.consumers import IsChatOwnerWs
    other=get_user_model().objects.create_user(username="ws-other")
    consumer=ChatConsumer(); consumer.scope={"user":other,"url_route":{"kwargs":{"chat_id":str(chat.pk)}}}
    assert async_to_sync(IsChatOwnerWs().has_permission)(consumer.scope,consumer) is False

def test_delete_removes_cli_session_directory(user,chat,tmp_path):
    from django_core_micha.assistant.views import ChatDetailView
    from pathlib import Path
    root=tmp_path/"sessions"; (root/str(chat.pk)).mkdir(parents=True); (root/str(chat.pk)/"session.jsonl").write_text("x",encoding="utf-8")
    request=APIRequestFactory().delete("/"); force_authenticate(request,user=user)
    with override_settings(DCM_ASSISTANT_CLI_ROOT=root): response=ChatDetailView.as_view()(request,chat_id=chat.pk)
    assert response.status_code==204 and not (root/str(chat.pk)).exists()

def test_frame_rate_closes_with_4429(user,chat,monkeypatch):
    from unittest.mock import AsyncMock
    import django_core_micha.assistant.consumers as consumers
    monkeypatch.setattr(consumers.time,"monotonic",lambda:100.0)
    consumer=ChatConsumer(); consumer.scope={"user":user,"url_route":{"kwargs":{"chat_id":str(chat.pk)}}}
    consumer.frames=__import__("collections").deque([100.0]*31); consumer.close=AsyncMock()
    consumer.get_object=AsyncMock(return_value=chat); consumer._session_valid=AsyncMock(return_value=True)
    async_to_sync(consumer.receive)(text_data='{"type":"stop"}')
    consumer.close.assert_awaited_with(code=4429)

def test_consumer_inventory_uses_secure_base():
    from django_core_micha.auth.ws_permissions import assert_all_consumers_secure
    assert assert_all_consumers_secure(["django_core_micha.assistant.consumers"])==[]

def _cli_proc(rows, stdout_hook=None):
    class Input:
        def write(self,data): pass
        async def drain(self): pass
        def close(self): pass
        async def wait_closed(self): pass
    class Output:
        def __init__(self): self.lines=iter(rows)
        async def readline(self):
            if stdout_hook: await stdout_hook()
            return next(self.lines,b"")
    class Proc:
        pid=1; returncode=None
        def __init__(self): self.stdin=Input(); self.stdout=Output(); self.stderr=NS(read=AsyncMock(return_value=b""))
        async def wait(self): self.returncode=0; return 0
    return Proc()

def test_cli_reads_assistant_text_from_message_content_and_reports_session(monkeypatch,tmp_path):
    import django_core_micha.assistant.drivers.claude_cli as cli
    monkeypatch.setattr(cli.shutil,"which",lambda name:"claude")
    proc=_cli_proc([b'{"type":"system","subtype":"init","tools":[],"mcp_servers":[]}\n',
        b'{"type":"assistant","message":{"content":[{"type":"text","text":"hello"}]}}\n',
        b'{"type":"result","usage":{"input_tokens":3,"output_tokens":2}}\n'])
    async def factory(*a,**k): return proc
    driver=ClaudeCliDriver(process_factory=factory,kill_tree=lambda p:None)
    async def run(): return [e async for e in driver.stream(prompt="p",model="m",tools=[],credential="x",chat=NS(pk="msg"))]
    with override_settings(DCM_ASSISTANT_CLI_ROOT=tmp_path): events=async_to_sync(run)()
    assert [e["text"] for e in events if e["type"]=="delta"]==["hello"]
    assert any(e["type"]=="usage" and e["usage"]["output_tokens"]==2 for e in events) and events[-1]["type"]=="session"

def test_cli_duplicate_tool_in_init_event_is_a_security_violation_and_bridge_secret_stays_private(monkeypatch,tmp_path):
    import django_core_micha.assistant.drivers.claude_cli as cli
    monkeypatch.setattr(cli.shutil,"which",lambda name:"claude")
    tool=NS(name="lookup",description="d",input_schema={"type":"object"},label="Lookup",handler=lambda *a:("x",[]))
    proc=_cli_proc([b'{"type":"system","subtype":"init","tools":["mcp__dcm_tools__lookup","mcp__dcm_tools__lookup"],'
        b'"mcp_servers":[{"name":"dcm_tools","status":"connected"}]}\n'])
    captured={}
    async def factory(*args,**kwargs): captured["args"]=args; captured["env"]=kwargs["env"]; return proc
    driver=ClaudeCliDriver(process_factory=factory,kill_tree=lambda p:None)
    async def run(): return [e async for e in driver.stream(prompt="p",model="m",tools=[tool],credential="x",chat=NS(pk="dup"))]
    with override_settings(DCM_ASSISTANT_CLI_ROOT=tmp_path):
        with pytest.raises(DriverError) as exc: async_to_sync(run)()
    assert exc.value.code=="security_violation"
    config=json.loads((tmp_path/"dup"/"bridge.json").read_text(encoding="utf-8"))
    assert config["nonce"] and config["endpoint"].startswith("tcp:127.0.0.1:")
    assert config["nonce"] not in " ".join(captured["args"]) and config["nonce"] not in "".join(captured["env"].values())

def test_cli_session_directory_symlink_is_refused(tmp_path):
    import os
    from django_core_micha.assistant.drivers.claude_cli import _session_dirs
    root=tmp_path/"root"; outside=tmp_path/"outside"; outside.mkdir(); (root/"c1").mkdir(parents=True)
    try: os.symlink(outside,root/"c1"/"config",target_is_directory=True)
    except (OSError,NotImplementedError): pytest.skip("symlinks unavailable")
    with override_settings(DCM_ASSISTANT_CLI_ROOT=root):
        with pytest.raises(DriverError) as exc: _session_dirs("c1",True)
    assert exc.value.code=="security_violation"


def test_start_refuses_a_turn_over_the_user_limit_and_leaves_no_row(user):
    from django_core_micha.assistant.turns import _start
    first=AssistantChat.objects.create(owner=user,selected_driver="openai_api",selected_model="gpt-4o-mini",selected_effort="medium")
    second=AssistantChat.objects.create(owner=user,selected_driver="openai_api",selected_model="gpt-4o-mini",selected_effort="medium")
    with override_settings(DCM_ASSISTANT_LIMITS={"max_turns_per_user":1}):
        _start(first.pk,user.pk,"one")
        with pytest.raises(DriverError) as exc: _start(second.pk,user.pk,"two")
    assert exc.value.code=="rate_limited" and not second.turns.exists()

def test_frame_rate_below_threshold_does_not_close(user,chat,monkeypatch):
    import django_core_micha.assistant.consumers as consumers
    monkeypatch.setattr(consumers.time,"monotonic",lambda:100.0)
    consumer=ChatConsumer(); consumer.scope={"user":user,"url_route":{"kwargs":{"chat_id":str(chat.pk)}}}
    consumer.frames=__import__("collections").deque([100.0]*5); consumer.close=AsyncMock()
    consumer.get_object=AsyncMock(return_value=chat); consumer._session_valid=AsyncMock(return_value=True)
    consumer.send_json=AsyncMock(); consumer.task=None
    async_to_sync(consumer.receive)(text_data='{"type":"stop"}')
    consumer.close.assert_not_awaited()


def test_oversized_tool_result_is_refused_before_it_reaches_a_provider(user,chat):
    from django_core_micha.assistant.drivers.base import invoke_tool
    tool=NS(handler=lambda u,c,a,t:("x"*50,[]))
    with override_settings(DCM_ASSISTANT_LIMITS={"max_prompt_bytes":10}):
        with pytest.raises(DriverError) as exc: async_to_sync(invoke_tool)(tool,user,chat,{},"cloud")
    assert exc.value.code=="prompt_too_long"

def test_cli_non_dict_server_entry_is_a_security_violation(monkeypatch,tmp_path):
    import django_core_micha.assistant.drivers.claude_cli as cli
    monkeypatch.setattr(cli.shutil,"which",lambda name:"claude")
    tool=NS(name="lookup",description="d",input_schema={"type":"object"},label="Lookup",handler=lambda *a:("x",[]))
    proc=_cli_proc([b'{"type":"system","subtype":"init","tools":["mcp__dcm_tools__lookup"],"mcp_servers":[null]}\n'])
    async def factory(*a,**k): return proc
    driver=ClaudeCliDriver(process_factory=factory,kill_tree=lambda p:None)
    async def run(): return [e async for e in driver.stream(prompt="p",model="m",tools=[tool],credential="x",chat=NS(pk="nd"))]
    with override_settings(DCM_ASSISTANT_CLI_ROOT=tmp_path):
        with pytest.raises(DriverError) as exc: async_to_sync(run)()
    assert exc.value.code=="security_violation"

def test_published_ucm_wire_shapes(user,chat):
    from django_core_micha.assistant.views import CatalogueView, ChatDetailView, AvailabilityView, TurnsView
    from django_core_micha.assistant.urls import urlpatterns
    assert {p.name for p in urlpatterns}>={"assistant-status","assistant-catalogue","assistant-chats","assistant-chat","assistant-turns"}
    factory=APIRequestFactory()
    request=factory.get("/"); force_authenticate(request,user=user)
    data=CatalogueView.as_view()(request).data
    entry=data["entries"][0]
    assert entry["id"]=="openai_api:gpt-4o-mini" and entry["label"]=="Mini" and data["default"]=={"entry":entry["id"],"effort":"low"}
    request=factory.get("/"); force_authenticate(request,user=user)
    state=AvailabilityView.as_view()(request).data; assert state["status"] in {"available","not_configured"} and "providers" in state
    request=factory.patch("/",{"selection":{"entry":entry["id"],"effort":"high"}},format="json"); force_authenticate(request,user=user)
    body=ChatDetailView.as_view()(request,chat_id=chat.pk).data
    assert body["selection"]=={"entry":entry["id"],"effort":"high"} and body["effort"]=="high"
    request=factory.patch("/",{"selection":{"entry":"nope:none","effort":"high"}},format="json"); force_authenticate(request,user=user)
    assert ChatDetailView.as_view()(request,chat_id=chat.pk).data["code"]=="invalid_selection"
    AssistantTurn.objects.create(chat=chat,sequence=1,user_text="q",assistant_text="a",state="completed")
    request=factory.get("/"); force_authenticate(request,user=user)
    page=TurnsView.as_view()(request,chat_id=chat.pk).data
    assert page["next"] is None and page["results"][0]["seq"]==1 and page["results"][0]["assistant_text"]=="a"


def test_stop_frame_cancels_the_running_turn_task(user,chat):
    consumer=ChatConsumer(); consumer.chat=chat; consumer.task=Mock(); consumer.task.done.return_value=False
    async_to_sync(consumer.receive_json)({"type":"stop"})
    consumer.task.cancel.assert_called_once()

def test_malformed_client_frame_answers_invalid_frame(user,chat):
    consumer=ChatConsumer(); consumer.chat=chat; consumer.task=None; consumer.send_json=AsyncMock()
    async_to_sync(consumer.receive_json)({"type":"send"})
    consumer.send_json.assert_awaited_with({"type":"error","code":"invalid_frame"})
