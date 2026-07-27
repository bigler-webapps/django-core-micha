"""Digest scanning for provider-derived todos."""
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db import IntegrityError, transaction
from django.db.models import Count
from django.utils import timezone

from ..delivery import _send_email
from ..models import Notification, NotificationDelivery, NotificationPreference, NotificationRecipient
from .registry import iter_candidate_users_fns
from .service import derive_todos_for_user


logger = logging.getLogger(__name__)
THRESHOLD_T1, THRESHOLD_T2, THRESHOLD_T3 = "t1", "t2", "t3"
PRE_DUE_LEAD_DAYS = 1


@dataclass(frozen=True)
class DigestRunSummary:
    users_scanned: int
    digests_sent: int
    threshold_records_created: int


def _seed_due(notification):
    """Read the already fresh materialized due display value as an aware datetime."""

    value = getattr(notification, "_todo_due_at", None)
    return value


def _crosses_pre_due_threshold(due_at, now) -> bool:
    return due_at is not None and now >= due_at - timedelta(days=PRE_DUE_LEAD_DAYS)


def _new_thresholds(notifications_for_group, already_sent: set[str], now) -> tuple[str, ...]:
    thresholds = []
    if notifications_for_group and THRESHOLD_T1 not in already_sent:
        thresholds.append(THRESHOLD_T1)
    if THRESHOLD_T2 not in already_sent and any(
        _crosses_pre_due_threshold(_seed_due(notification), now) for notification in notifications_for_group
    ):
        thresholds.append(THRESHOLD_T2)
    if THRESHOLD_T3 not in already_sent and any(
        notification.content.get("severity") == "overdue" for notification in notifications_for_group
    ):
        thresholds.append(THRESHOLD_T3)
    return tuple(thresholds)


def _get_digest_delivery_with_retry(*, recipient, threshold):
    """Claim one threshold via the partial unique constraint (the real concurrency guard).

    Recorded as ``pending`` first, mirroring ``api.py``'s notify() delivery pattern, so a
    subsequent send failure leaves a distinguishable status instead of a false "sent".
    """

    try:
        with transaction.atomic():
            return NotificationDelivery.objects.get_or_create(
                recipient=recipient,
                channel="email",
                digest_threshold=threshold,
                defaults={"status": "pending"},
            )
    except IntegrityError:
        return NotificationDelivery.objects.get(
            recipient=recipient,
            channel="email",
            digest_threshold=threshold,
        ), False


def _candidate_users(now):
    users = {}
    for candidate_users_fn in iter_candidate_users_fns():
        for user in candidate_users_fn(now):
            users[(user._meta.label_lower, user.pk)] = user
    return list(users.values())


def _user_opted_into_email(user) -> bool:
    if not getattr(user, "email", ""):
        return False
    return NotificationPreference.objects.filter(user=user, email_opt_in=True).exists()


def _reconcile_todo_overlays(user, emitted_recipients) -> None:
    """Remove this user's stale todo overlays without affecting another recipient."""

    emitted_notification_ids = {recipient.notification_id for recipient in emitted_recipients}
    stale_recipients = NotificationRecipient.objects.filter(
        user=user,
        notification__category="todo",
    ).exclude(notification_id__in=emitted_notification_ids)
    stale_notification_ids = list(stale_recipients.values_list("notification_id", flat=True))
    stale_recipients.delete()
    if stale_notification_ids:
        Notification.objects.filter(pk__in=stale_notification_ids, category="todo").annotate(
            recipient_count=Count("recipients")
        ).filter(recipient_count=0).delete()


def send_todo_digests(now: datetime | None = None) -> DigestRunSummary:
    """Send one combined email per candidate user for newly crossed todo thresholds.

    Delivery rows are recorded *before* sending (racing against a concurrent run via
    the existing partial-unique-constraint retry), and only for a user who is
    actually opted into email — a threshold is never marked "sent" for a recipient
    who never received anything, so a later opt-in still catches up correctly.
    """

    resolved_now = now or timezone.now()
    digests_sent = threshold_records_created = 0
    users = _candidate_users(resolved_now)
    for user in users:
        try:
            emitted_recipients = derive_todos_for_user(user, resolved_now)
            _reconcile_todo_overlays(user, emitted_recipients)
            notifications = [
                recipient.notification
                for recipient in emitted_recipients
                if recipient.dismissed_at is None and recipient.done_at is None
            ]
            groups = defaultdict(list)
            for notification in notifications:
                groups[(notification.content_type_id, notification.object_id, notification.notification_type)].append(notification)
            pending = []
            for notifications_for_group in groups.values():
                recipient = NotificationRecipient.objects.get(notification=notifications_for_group[0], user=user)
                already_sent = set(
                    recipient.deliveries.filter(channel="email").exclude(digest_threshold__isnull=True).values_list(
                        "digest_threshold", flat=True
                    )
                )
                thresholds = _new_thresholds(notifications_for_group, already_sent, resolved_now)
                if thresholds:
                    pending.append((recipient, notifications_for_group, thresholds))
            if not pending or not _user_opted_into_email(user):
                continue

            newly_recorded = []
            claimed_deliveries = []
            for recipient, notifications_for_group, thresholds in pending:
                for threshold in thresholds:
                    delivery, created = _get_digest_delivery_with_retry(recipient=recipient, threshold=threshold)
                    if created:
                        threshold_records_created += 1
                        newly_recorded.append(notifications_for_group)
                        claimed_deliveries.append(delivery)
            if not newly_recorded:
                continue  # every threshold already recorded by a concurrent run

            body = "\n".join(
                f"- {group[0].content.get('title', group[0].notification_type)}" for group in newly_recorded
            )
            try:
                _send_email(title="Todo reminders", body=body, url=None, users=[user])
                digests_sent += 1
                NotificationDelivery.objects.filter(
                    pk__in=[delivery.pk for delivery in claimed_deliveries]
                ).update(status="sent", sent_at=resolved_now)
            except Exception:
                logger.exception("Todo digest email failed for user %s", user.pk)
                NotificationDelivery.objects.filter(
                    pk__in=[delivery.pk for delivery in claimed_deliveries]
                ).update(status="failed")
        except Exception:
            logger.exception("Todo digest scan failed for user %s", user.pk)
    return DigestRunSummary(len(users), digests_sent, threshold_records_created)
