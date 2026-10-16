import pytest
from django.contrib.auth import get_user_model
from django_core_micha.assistant.models import AssistantChat, AssistantTurn
from django_core_micha.assistant import turns
from django_core_micha.billing.models import AICostRecord

@pytest.mark.django_db
def test_turn_cost_recorded_once_and_without_billing_hook_is_neutral(monkeypatch):
    user=get_user_model().objects.create_user(username="turn-cost",email="turn@example.com")
    chat=AssistantChat.objects.create(owner=user,selected_driver="openai_api",selected_model="gpt-4o-mini")
    turn=AssistantTurn.objects.create(chat=chat,sender=user,sequence=1,user_text="hello",driver="openai_api",model="gpt-4o-mini")
    assert turns._finish(turn.pk,"ok","completed",usage={"input_tokens":8,"output_tokens":4})
    assert AICostRecord.objects.filter(purpose="chat",reference=str(turn.pk),input_tokens=8).count()==1
    other=AssistantTurn.objects.create(chat=chat,sender=user,sequence=2,user_text="hello",driver="openai_api",model="gpt-4o-mini")
    from django.apps import apps
    monkeypatch.setattr(apps,"is_installed",lambda name: False)
    assert turns._finish(other.pk,"ok","completed",usage={"input_tokens":3,"output_tokens":2})
    assert AICostRecord.objects.filter(purpose="chat").count()==1
