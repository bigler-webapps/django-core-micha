"""Digest scanning for provider-derived todos."""
import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db import IntegrityError, transaction
from django.db.models import Count
from django.utils import timezone

from ..delivery import EmailOutcome, _send_email
from ..models import Notification, NotificationDelivery, NotificationPreference, NotificationRecipient
from .registry import get_todo_config, iter_candidate_users_fns, iter_registered_todo_types
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
    """Acquire a unique claim; only failed or explicitly released rows can retry.

    Claimed and sending rows never expire automatically: a live worker must not
    overlap a retry. Use recover_digest_claim only after the worker is known dead.
    """
    token = uuid.uuid4()
    try:
        with transaction.atomic():
            delivery, created = NotificationDelivery.objects.get_or_create(
                recipient=recipient, channel="email", digest_threshold=threshold,
                defaults={"status": "claimed", "claim_token": token},
            )
    except IntegrityError:
        delivery = NotificationDelivery.objects.get(recipient=recipient, channel="email", digest_threshold=threshold)
        created = False
    if created:
        return delivery, True
    acquired = NotificationDelivery.objects.filter(pk=delivery.pk, status__in=["failed", "not_attempted"], claim_token__isnull=True).update(status="claimed", claim_token=token)
    if acquired:
        delivery.status, delivery.claim_token = "claimed", token
    return delivery, bool(acquired)


def recover_digest_claim(delivery_id, claim_token):
    """Recover a confirmed-dead worker: pre-attempt retries; in-flight is uncertain.

    Legacy pending rows have unknown transport history and require reconciliation.
    Never call this merely because a worker is slow.
    """
    rows = NotificationDelivery.objects.filter(pk=delivery_id, digest_threshold__isnull=False, claim_token=claim_token)
    if rows.filter(status="claimed").update(status="not_attempted", claim_token=None):
        return "not_attempted"
    if rows.filter(status="sending").update(status="uncertain", claim_token=None):
        return "uncertain"
    return None


def _transition_claim(delivery, previous, status, *, sent_at=None):
    fields = {"status": status}
    if status not in ("claimed", "sending"):
        fields["claim_token"] = None
    if sent_at is not None:
        fields["sent_at"] = sent_at
    return NotificationDelivery.objects.filter(pk=delivery.pk, claim_token=delivery.claim_token, status=previous).update(**fields)


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
    retained_types = [
        type_key for type_key in iter_registered_todo_types()
        if get_todo_config(type_key).retain_stale_overlays
    ]
    stale_recipients = stale_recipients.exclude(notification__notification_type__in=retained_types)
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
                    recipient.deliveries.filter(channel="email").exclude(status__in=["failed", "not_attempted"]).exclude(digest_threshold__isnull=True).values_list(
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

            # Recheck both the account and occurrence after acquiring claims; the body
            # lists only the occurrences that are still eligible.
            active = []
            active_groups = []
            for delivery, group in zip(claimed_deliveries, newly_recorded):
                eligible = _user_opted_into_email(user) and NotificationRecipient.objects.filter(
                    pk=delivery.recipient_id, dismissed_at__isnull=True, done_at__isnull=True,
                    notification__resolved_at__isnull=True,
                ).exists()
                if not eligible:
                    _transition_claim(delivery, "claimed", "not_attempted")
                elif _transition_claim(delivery, "claimed", "sending"):
                    active.append(delivery)
                    active_groups.append(group)
            if not active:
                continue
            body = "\n".join(
                f"- {group[0].content.get('title', group[0].notification_type)}" for group in active_groups
            )
            try:
                outcome = _send_email(title="Todo reminders", body=body, url=None, users=[user]).get(
                    user.id, EmailOutcome.NOT_ATTEMPTED
                )
            except Exception:
                logger.exception("Todo digest email outcome uncertain for user %s", user.pk)
                outcome = EmailOutcome.UNCERTAIN
            status = {EmailOutcome.ACCEPTED: "sent", EmailOutcome.FAILED: "failed",
                      EmailOutcome.NOT_ATTEMPTED: "not_attempted", EmailOutcome.UNCERTAIN: "uncertain"}[outcome]
            for delivery in active:
                _transition_claim(delivery, "sending", status, sent_at=resolved_now if status == "sent" else None)
            if status == "sent":
                digests_sent += 1
        except Exception:
            logger.exception("Todo digest scan failed for user %s", user.pk)
    return DigestRunSummary(len(users), digests_sent, threshold_records_created)
