import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone

from django_core_micha.notifications.models import (
    Notification,
    NotificationDelivery,
    NotificationRecipient,
)


def _notification(*, key, expires_at=None):
    return Notification.objects.create(
        notification_type="retention_test",
        category="system",
        content={"title_key": "Retention test"},
        dedup_key=key,
        expires_at=expires_at,
    )


@pytest.mark.django_db
def test_prune_notifications_removes_expired_and_aged_rows_with_cascades_and_is_idempotent():
    user = get_user_model().objects.create_user(
        username="prune-notifications",
        email="prune-notifications@example.test",
        password="password",
    )
    now = timezone.now()
    expired = _notification(key="expired", expires_at=now - timezone.timedelta(seconds=1))
    aged = _notification(key="aged")
    fresh = _notification(key="fresh", expires_at=now + timezone.timedelta(days=1))
    expired_recipient = NotificationRecipient.objects.create(notification=expired, user=user)
    expired_delivery = NotificationDelivery.objects.create(recipient=expired_recipient, channel="chip")
    Notification.objects.filter(pk=aged.pk).update(created_at=now - timezone.timedelta(days=91))

    call_command("prune_notifications", verbosity=0)

    assert not Notification.objects.filter(pk=expired.pk).exists()
    assert not Notification.objects.filter(pk=aged.pk).exists()
    assert Notification.objects.filter(pk=fresh.pk).exists()
    assert not NotificationRecipient.objects.filter(pk=expired_recipient.pk).exists()
    assert not NotificationDelivery.objects.filter(pk=expired_delivery.pk).exists()

    call_command("prune_notifications", verbosity=0)
    assert Notification.objects.filter(pk=fresh.pk).count() == 1


@pytest.mark.django_db
def test_prune_notifications_dry_run_keeps_matching_rows():
    expired = _notification(
        key="dry-run-expired",
        expires_at=timezone.now() - timezone.timedelta(seconds=1),
    )

    call_command("prune_notifications", "--dry-run", verbosity=0)

    assert Notification.objects.filter(pk=expired.pk).exists()
