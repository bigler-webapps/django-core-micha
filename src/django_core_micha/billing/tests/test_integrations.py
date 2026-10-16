import pytest
from django.contrib.auth import get_user_model
from django_core_micha.billing.services import record_ai_call
from django_core_micha.billing.models import AICostRecord
from django_core_micha.extraction.types import ExtractionRequest, ExtractionResult, TokenUsage

def user(): return get_user_model().objects.create_user(username="integrate",email="integrate@example.com")

@pytest.mark.django_db(transaction=True)
def test_completion_integration(monkeypatch):
    from django_core_micha.assistant import completion
    class Driver:
        async def stream(self,**kwargs):
            yield {"type":"usage","usage":{"input_tokens":2,"output_tokens":3}}
    monkeypatch.setattr(completion,"entries",lambda:[{"driver":"openai_api","model":"gpt-4o-mini","efforts":["medium"]}])
    monkeypatch.setattr(completion,"validate",lambda *a:None)
    monkeypatch.setattr(completion,"resolve_credential",lambda *a:("key",None))
    monkeypatch.setattr(completion,"_make_driver",lambda *a:Driver())
    import asyncio
    asyncio.run(completion.acomplete(user(),prompt="hi",driver="openai_api",model="gpt-4o-mini"))
    assert AICostRecord.objects.filter(purpose="completion").count()==1

@pytest.mark.django_db
def test_extraction_integration(monkeypatch):
    from django_core_micha.extraction import service
    u=user()
    monkeypatch.setattr(service,"reserve_for_extraction",lambda **k:None)
    monkeypatch.setattr(service,"charge_user_for_response",lambda **k:None)
    monkeypatch.setattr(service.openai_driver,"extract",lambda *a,**k:ExtractionResult("ok",{},TokenUsage(4,5,9)))
    request=ExtractionRequest("sys","prompt",{},20,"medium",{})
    service.extract_document(request,provider="openai",api_key="x",model="gpt-4o-mini",user_id=u.pk)
    assert AICostRecord.objects.filter(purpose="extraction",user=u,input_tokens=4).exists()
