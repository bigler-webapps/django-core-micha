from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone

from django_core_micha.notifications.delivery import EmailOutcome
from django_core_micha.notifications.models import Notification, NotificationDelivery, NotificationPreference, NotificationRecipient
from django_core_micha.notifications.todo import digests, registry
from django_core_micha.notifications.todo.registry import TodoSeed, TodoTypeConfig
from django_core_micha.notifications.types import NotificationType, _REGISTRY, register_notification_type
from tests.testapp.models import Widget


def _recording_transport(sent, outcome=EmailOutcome.ACCEPTED):
    """Fake mail transport: records the call and reports ``outcome`` for every recipient."""
    def transport(**kwargs):
        sent.append(kwargs)
        return {user.id: outcome for user in kwargs["users"]}
    return transport


@pytest.fixture(autouse=True)
def clear_registries():
    registry._PROVIDERS.clear(); registry._CONFIGS.clear(); registry._CANDIDATE_USERS.clear(); _REGISTRY.clear()
    yield
    registry._PROVIDERS.clear(); registry._CONFIGS.clear(); registry._CANDIDATE_USERS.clear(); _REGISTRY.clear()


@pytest.mark.django_db
def test_digest_records_thresholds_once_and_sends_again_only_for_t2(monkeypatch):
    user = get_user_model().objects.create_user(username="digest-user", email="digest@example.test", password="password")
    NotificationPreference.objects.create(user=user, email_opt_in=True)
    widget = Widget.objects.create(name="Digest scope")
    first_now = timezone.now()
    due = first_now + timedelta(days=3)
    register_notification_type(NotificationType(
        key="demo_todo", category="todo", mode="provider", resolution="state-resolved",
    ))
    registry.register_todo_provider(
        "demo_todo",
        lambda user, now: [TodoSeed("demo_todo", user, {"title": "Digest task"}, widget, due_base_resolver=lambda base: due)],
        config=TodoTypeConfig(type_key="demo_todo", due="start", always_visible=True),
        candidate_users_fn=lambda now: [user],
    )
    sent = []
    monkeypatch.setattr(digests, "_send_email", _recording_transport(sent))

    first = digests.send_todo_digests(first_now)
    second = digests.send_todo_digests(first_now)
    third = digests.send_todo_digests(due - timedelta(hours=12))

    assert first.digests_sent == first.threshold_records_created == 1
    assert second.digests_sent == second.threshold_records_created == 0
    assert third.digests_sent == third.threshold_records_created == 1
    assert len(sent) == 2
    assert set(NotificationDelivery.objects.values_list("digest_threshold", flat=True)) == {"t1", "t2"}
    assert set(NotificationDelivery.objects.values_list("status", flat=True)) == {"sent"}


@pytest.mark.django_db
def test_send_failure_marks_delivery_failed_not_falsely_sent(monkeypatch):
    user = get_user_model().objects.create_user(username="flaky-mail", email="flaky@example.test", password="password")
    NotificationPreference.objects.create(user=user, email_opt_in=True)
    now = timezone.now()
    register_notification_type(NotificationType(
        key="demo_todo", category="todo", mode="provider", resolution="state-resolved",
    ))
    registry.register_todo_provider(
        "demo_todo",
        lambda user, now: [TodoSeed("demo_todo", user, {"title": "Digest task"}, Widget.objects.create(name="scope"), due_base_resolver=lambda base: now)],
        config=TodoTypeConfig(type_key="demo_todo", due="start", always_visible=True),
        candidate_users_fn=lambda now: [user],
    )

    monkeypatch.setattr(digests, "_send_email", _recording_transport([], EmailOutcome.FAILED))

    result = digests.send_todo_digests(now)
    assert result.digests_sent == 0
    assert result.threshold_records_created == 2
    assert set(NotificationDelivery.objects.values_list("status", flat=True)) == {"failed"}

    # A helper that raises instead of reporting an outcome is uncertain, never "sent" or retried blindly.
    NotificationDelivery.objects.all().delete()

    def _boom(**kwargs):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(digests, "_send_email", _boom)
    assert digests.send_todo_digests(now).digests_sent == 0
    assert set(NotificationDelivery.objects.values_list("status", flat=True)) == {"uncertain"}


@pytest.mark.django_db
def test_not_opted_in_user_records_nothing_and_later_opt_in_catches_up(monkeypatch):
    # Regression for a real reviewed bug: a threshold must never be recorded as
    # "sent" for a recipient who was never actually emailed, or a later opt-in
    # would silently never receive that reminder.
    user = get_user_model().objects.create_user(username="not-opted-in", email="lurker@example.test", password="password")
    now = timezone.now()
    register_notification_type(NotificationType(
        key="demo_todo", category="todo", mode="provider", resolution="state-resolved",
    ))
    registry.register_todo_provider(
        "demo_todo",
        lambda user, now: [TodoSeed("demo_todo", user, {"title": "Digest task"}, Widget.objects.create(name="scope"), due_base_resolver=lambda base: now)],
        config=TodoTypeConfig(type_key="demo_todo", due="start", always_visible=True),
        candidate_users_fn=lambda now: [user],
    )
    sent = []
    monkeypatch.setattr(digests, "_send_email", _recording_transport(sent))

    result = digests.send_todo_digests(now)
    assert result.digests_sent == result.threshold_records_created == 0
    assert not sent
    assert NotificationDelivery.objects.count() == 0

    NotificationPreference.objects.create(user=user, email_opt_in=True)
    caught_up = digests.send_todo_digests(now)
    # The seed is due right now, so both t1 (became visible) and t2 (within the
    # pre-due lead window) cross in the same run -- one email, two thresholds.
    assert caught_up.digests_sent == 1
    assert caught_up.threshold_records_created == 2
    assert len(sent) == 1


@pytest.mark.django_db
def test_digest_reconciles_only_non_emitted_todo_overlays_and_preserves_emitted_statuses():
    user = get_user_model().objects.create_user(username="reconcile-user", email="reconcile@example.test", password="password")
    now = timezone.now()
    widgets = {
        "stale": Widget.objects.create(name="Stale"),
        "dismissed": Widget.objects.create(name="Dismissed"),
        "done": Widget.objects.create(name="Done"),
    }
    emissions = {"stale", "dismissed", "done"}
    register_notification_type(NotificationType(
        key="demo_todo", category="todo", mode="provider", resolution="state-resolved",
    ))

    def provider(user, now):
        return [TodoSeed("demo_todo", user, {"title": title}, widgets[title]) for title in emissions]

    registry.register_todo_provider(
        "demo_todo",
        provider,
        config=TodoTypeConfig(type_key="demo_todo", always_visible=True),
        candidate_users_fn=lambda now: [user],
    )
    digests.send_todo_digests(now)
    recipients = {
        recipient.notification.content["title"]: recipient
        for recipient in NotificationRecipient.objects.filter(user=user, notification__category="todo").select_related("notification")
    }
    NotificationRecipient.objects.filter(pk=recipients["dismissed"].pk).update(dismissed_at=now)
    NotificationRecipient.objects.filter(pk=recipients["done"].pk).update(done_at=now)
    dismissed_id = recipients["dismissed"].notification_id
    done_id = recipients["done"].notification_id
    stale_id = recipients["stale"].notification_id

    emissions.remove("stale")
    digests.send_todo_digests(now)

    assert not Notification.objects.filter(pk=stale_id).exists()
    assert Notification.objects.filter(pk=dismissed_id, category="todo").exists()
    assert Notification.objects.filter(pk=done_id, category="todo").exists()
    dismissed = NotificationRecipient.objects.get(notification_id=dismissed_id, user=user)
    done = NotificationRecipient.objects.get(notification_id=done_id, user=user)
    assert dismissed.dismissed_at is not None
    assert done.done_at is not None


def test_management_command_reports_digest_summary(monkeypatch, capsys):
    monkeypatch.setattr(
        "django_core_micha.notifications.management.commands.send_todo_digests.send_todo_digests",
        lambda: digests.DigestRunSummary(2, 1, 3),
    )
    call_command("send_todo_digests")
    assert "Scanned 2 users; sent 1 digests; recorded 3 thresholds." in capsys.readouterr().out


@pytest.mark.django_db(transaction=True)
def test_dcm_mail_2_claims_retry_and_recover_only_confirmed_dead_workers(monkeypatch):
    from django_core_micha.notifications.delivery import EmailOutcome
    user = get_user_model().objects.create_user(username="claim-user", email="claim@example.test")
    pref = NotificationPreference.objects.create(user=user, email_opt_in=True)
    now = timezone.now()
    widget = Widget.objects.create(name="claim scope")
    register_notification_type(NotificationType(key="claim_todo", category="todo", mode="provider", resolution="state-resolved"))
    registry.register_todo_provider("claim_todo", lambda user, now: [TodoSeed("claim_todo", user, {"title": "Task"}, widget, due_base_resolver=lambda base: now + timedelta(days=3))], config=TodoTypeConfig(type_key="claim_todo", due="start", always_visible=True), candidate_users_fn=lambda now: [user])
    outcomes = [EmailOutcome.FAILED, EmailOutcome.ACCEPTED]
    calls = []
    def send(**kwargs):
        calls.append(1)
        return {user.id: outcomes.pop(0)}
    monkeypatch.setattr(digests, "_send_email", send)
    digests.send_todo_digests(now)
    delivery = NotificationDelivery.objects.get()
    assert delivery.status == "failed"
    pref.email_opt_in = False
    pref.save()
    digests.send_todo_digests(now)
    assert len(calls) == 1
    pref.email_opt_in = True
    pref.save()
    digests.send_todo_digests(now)
    digests.send_todo_digests(now)
    delivery.refresh_from_db()
    assert delivery.status == "sent" and len(calls) == 2
    recipient = delivery.recipient
    claim, acquired = digests._get_digest_delivery_with_retry(recipient=recipient, threshold="t2")
    assert acquired
    assert not digests._get_digest_delivery_with_retry(recipient=recipient, threshold="t2")[1]
    assert digests.recover_digest_claim(claim.pk, claim.claim_token) == "not_attempted"
    claim, acquired = digests._get_digest_delivery_with_retry(recipient=recipient, threshold="t2")
    assert acquired and digests._transition_claim(claim, "claimed", "sending")
    assert digests.recover_digest_claim(claim.pk, claim.claim_token) == "uncertain"
    assert not digests._get_digest_delivery_with_retry(recipient=recipient, threshold="t2")[1]

@pytest.mark.django_db
def test_retained_occurrences_survive_absence_and_policy_suppression(monkeypatch):
    from django_core_micha.notifications.todo.registry import TodoPolicy
    user = get_user_model().objects.create_user(username="retained", email="retained@example.test")
    NotificationPreference.objects.create(user=user, email_opt_in=True)
    widgets = [Widget.objects.create(name=name) for name in ("one", "two")]
    now = timezone.now()
    state = {"present": True, "enabled": True}
    def provider(user, current):
        if not state["present"]:
            return []
        return [TodoSeed("retained", user, {"title": widget.name}, widget,
                         policy=TodoPolicy(enabled=state["enabled"])) for widget in widgets]
    register_notification_type(NotificationType(key="retained", category="todo", mode="provider", resolution="state-resolved"))
    registry.register_todo_provider("retained", provider, config=TodoTypeConfig(type_key="retained", retain_stale_overlays=True), candidate_users_fn=lambda now: [user])
    sent = []
    monkeypatch.setattr(digests, "_send_email", _recording_transport(sent))
    digests.send_todo_digests(now)
    rows = list(NotificationRecipient.objects.filter(user=user).order_by("notification__object_id"))
    assert len(rows) == 2 and NotificationDelivery.objects.filter(status="sent").count() == 2
    NotificationRecipient.objects.filter(pk=rows[0].pk).update(seen_at=now, dismissed_at=now)
    state["present"] = False
    digests.send_todo_digests(now)
    state.update(present=True, enabled=False)
    digests.send_todo_digests(now)
    state["enabled"] = True
    digests.send_todo_digests(now)
    rows[0].refresh_from_db()
    assert rows[0].seen_at == rows[0].dismissed_at == now
    assert NotificationRecipient.objects.filter(user=user).count() == 2
    assert NotificationDelivery.objects.filter(status="sent").count() == 2
    assert len(sent) == 1


@pytest.mark.django_db
def test_digest_body_lists_only_occurrences_still_eligible_after_claiming(monkeypatch):
    user = get_user_model().objects.create_user(username="body-user", email="body@example.test")
    NotificationPreference.objects.create(user=user, email_opt_in=True)
    widgets = [Widget.objects.create(name=f"body-{index}") for index in range(2)]
    now = timezone.now()
    register_notification_type(NotificationType(key="body_todo", category="todo", mode="provider", resolution="state-resolved"))
    registry.register_todo_provider(
        "body_todo",
        lambda user, current: [TodoSeed("body_todo", user, {"title": f"Task {index}"}, widgets[index],
                                        due_base_resolver=lambda base: now) for index in range(2)],
        config=TodoTypeConfig(type_key="body_todo", due="start", always_visible=True),
        candidate_users_fn=lambda current: [user],
    )
    sent = []
    monkeypatch.setattr(digests, "_send_email", _recording_transport(sent))
    claim = digests._get_digest_delivery_with_retry

    def claim_then_complete_second_task(*, recipient, threshold):
        result = claim(recipient=recipient, threshold=threshold)
        if recipient.notification.content["title"] == "Task 1":
            NotificationRecipient.objects.filter(pk=recipient.pk).update(done_at=now)
        return result

    monkeypatch.setattr(digests, "_get_digest_delivery_with_retry", claim_then_complete_second_task)
    digests.send_todo_digests(now)

    assert len(sent) == 1
    assert "Task 0" in sent[0]["body"] and "Task 1" not in sent[0]["body"]
    statuses = {delivery.recipient.notification.content["title"]: delivery.status
                for delivery in NotificationDelivery.objects.select_related("recipient__notification")}
    assert statuses["Task 1"] == "not_attempted" and statuses["Task 0"] == "sent"


@pytest.mark.django_db
def test_stale_claim_token_cannot_transition_a_reclaimed_delivery():
    user = get_user_model().objects.create_user(username="stale-claim", email="stale@example.test")
    notification = Notification.objects.create(notification_type="claim_todo", category="todo",
                                                content={"title": "Task"}, dedup_key="stale-claim")
    recipient = NotificationRecipient.objects.create(notification=notification, user=user)
    first, acquired = digests._get_digest_delivery_with_retry(recipient=recipient, threshold="t1")
    assert acquired
    assert digests.recover_digest_claim(first.pk, first.claim_token) == "not_attempted"
    second, acquired = digests._get_digest_delivery_with_retry(recipient=recipient, threshold="t1")
    assert acquired and second.claim_token != first.claim_token
    # The first worker is dead and its token is stale: it can neither send nor mark a result.
    assert not digests._transition_claim(first, "claimed", "sending")
    assert not digests._transition_claim(first, "sending", "sent")
    assert digests._transition_claim(second, "claimed", "sending")
