from django_core_micha.auth.ws_permissions import assert_all_consumers_secure
from django_core_micha.messaging.realtime import publish_messaging_event


def test_messaging_adds_no_websocket_consumer():
    assert assert_all_consumers_secure(["django_core_micha.notifications.consumers"]) == []
    assert not hasattr(__import__("django_core_micha.messaging", fromlist=["consumers"]), "consumers")


def test_messaging_frames_have_shared_envelope_and_event_id(monkeypatch):
    sent = []
    conversation = type("Conversation", (), {"id": "c1", "app": type("App", (), {"app_key": "test-app"})()})()
    monkeypatch.setattr("django_core_micha.messaging.realtime.push_to_users", lambda users, frame: sent.append(frame))
    publish_messaging_event(conversation=conversation, users=[object()], event_type="reaction", payload={"message_id": "m1"})
    assert sent[0]["envelope"] == "messaging"
    assert sent[0]["event_id"] and sent[0]["app_key"] == "test-app"


def test_deleted_message_frame_never_loads_message_content(monkeypatch):
    sent = []
    conversation = type("Conversation", (), {"id": "c1", "app": type("App", (), {"app_key": "test-app"})()})()
    monkeypatch.setattr("django_core_micha.messaging.realtime.push_to_users", lambda users, frame: sent.append(frame))
    publish_messaging_event(conversation=conversation, users=[object()], event_type="message_deleted", payload={"message_id": "m1", "deleted_at": "now", "deleted_by": "u1"})
    assert set(sent[0]).isdisjoint({"message", "body", "title", "link_target", "attachments", "reactions"})
