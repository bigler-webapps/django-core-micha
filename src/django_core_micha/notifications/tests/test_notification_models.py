import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from django_core_micha.notifications.models import (
    Notification,
    NotificationDelivery,
    NotificationRecipient,
)


def create_user(username):
    return get_user_model().objects.create_user(
        username=username,
        email=f"{username}@example.test",
        password="password",
    )


@pytest.mark.django_db
def test_notification_notifiable_round_trips():
    user = create_user("notifiable")
    notification = Notification.objects.create(
        notification_type="payment_due",
        category="finance",
        content={"title_key": "Notif.Payment.TITLE", "body_key": "Notif.Payment.BODY"},
        notifiable=user,
        dedup_key="round-trip",
    )

    assert notification.notifiable == user


@pytest.mark.django_db
def test_notification_deduplicates_logical_messages_and_enforces_constraint():
    first_user = create_user("dedup-one")
    second_user = create_user("dedup-two")
    content = {"title_key": "Notif.Payment.TITLE", "body_key": "Notif.Payment.BODY"}

    first, first_created = Notification.objects.get_or_create_by_dedup(
        notification_type="payment_due",
        category="finance",
        notifiable=first_user,
        content=content,
    )
    duplicate, duplicate_created = Notification.objects.get_or_create_by_dedup(
        notification_type="payment_due",
        category="finance",
        notifiable=first_user,
        content=content,
    )
    distinct, distinct_created = Notification.objects.get_or_create_by_dedup(
        notification_type="payment_due",
        category="finance",
        notifiable=second_user,
        content=content,
    )

    assert first_created is True
    assert duplicate_created is False
    assert duplicate == first
    assert distinct_created is True
    assert distinct != first
    assert Notification.objects.count() == 2

    with pytest.raises(IntegrityError), transaction.atomic():
        Notification.objects.create(
            notification_type="payment_due",
            category="finance",
            content=content,
            dedup_key=first.dedup_key,
        )


@pytest.mark.django_db
def test_notification_dedup_key_with_no_notifiable():
    content = {"title_key": "Notif.System.TITLE"}

    first, first_created = Notification.objects.get_or_create_by_dedup(
        notification_type="system_broadcast",
        category="system",
        notifiable=None,
        content=content,
    )
    duplicate, duplicate_created = Notification.objects.get_or_create_by_dedup(
        notification_type="system_broadcast",
        category="system",
        notifiable=None,
        content=content,
    )

    assert first_created is True
    assert duplicate_created is False
    assert duplicate == first
    assert first.content_type is None
    assert first.object_id is None
    assert first.notifiable is None


@pytest.mark.django_db
def test_notification_recipient_is_unique_per_notification_and_user():
    user = create_user("recipient-unique")
    notification = Notification.objects.create(
        notification_type="payment_due",
        category="finance",
        content={"title_key": "Notif.Payment.TITLE"},
        dedup_key="recipient-unique",
    )
    NotificationRecipient.objects.create(notification=notification, user=user)

    with pytest.raises(IntegrityError), transaction.atomic():
        NotificationRecipient.objects.create(notification=notification, user=user)


@pytest.mark.django_db
def test_not_done_recipient_projection_excludes_done_rows():
    user = create_user("projection")
    done_notification = Notification.objects.create(
        notification_type="payment_due",
        category="finance",
        content={"title_key": "Notif.Payment.TITLE"},
        dedup_key="projection-done",
    )
    pending_notification = Notification.objects.create(
        notification_type="payment_overdue",
        category="finance",
        content={"title_key": "Notif.Payment.OVERDUE"},
        dedup_key="projection-pending",
    )
    done_recipient = NotificationRecipient.objects.create(
        notification=done_notification,
        user=user,
        done_at=timezone.now(),
    )
    pending_recipient = NotificationRecipient.objects.create(
        notification=pending_notification,
        user=user,
    )

    not_done = NotificationRecipient.objects.filter(user=user, done_at__isnull=True)

    assert done_recipient not in not_done
    assert pending_recipient in not_done


@pytest.mark.django_db
def test_notification_delivery_is_unique_per_recipient_channel_and_threshold():
    user = create_user("delivery")
    notification = Notification.objects.create(
        notification_type="payment_due",
        category="finance",
        content={"title_key": "Notif.Payment.TITLE"},
        dedup_key="delivery",
    )
    recipient = NotificationRecipient.objects.create(notification=notification, user=user)
    NotificationDelivery.objects.create(recipient=recipient, channel="email", digest_threshold="t1")

    with pytest.raises(IntegrityError), transaction.atomic():
        NotificationDelivery.objects.create(recipient=recipient, channel="email", digest_threshold="t1")
