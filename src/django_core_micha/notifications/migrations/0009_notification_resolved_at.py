from django.db import migrations, models
from django.db.models import Count, Exists, Max, OuterRef


def backfill_resolved_at(apps, schema_editor):
    Notification = apps.get_model("django_core_micha_notifications", "Notification")
    NotificationRecipient = apps.get_model("django_core_micha_notifications", "NotificationRecipient")
    open_recipient_exists = Exists(
        NotificationRecipient.objects.filter(notification_id=OuterRef("pk"), done_at__isnull=True)
    )
    closed = (
        Notification.objects.annotate(has_open_recipient=open_recipient_exists)
        .filter(has_open_recipient=False)
        .annotate(n_recipients=Count("recipients"), last_done=Max("recipients__done_at"))
        .filter(n_recipients__gt=0)
    )
    for notification in closed.iterator():
        notification.resolved_at = notification.last_done or notification.created_at
        notification.save(update_fields=["resolved_at"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("django_core_micha_notifications", "0008_notificationcategorysubscription"),
    ]

    operations = [
        migrations.AddField(
            model_name="notification",
            name="resolved_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(backfill_resolved_at, noop),
        migrations.RemoveConstraint(
            model_name="notification",
            name="uniq_notification_dedup_key",
        ),
        migrations.AddConstraint(
            model_name="notification",
            constraint=models.UniqueConstraint(
                condition=models.Q(("resolved_at__isnull", True)),
                fields=("dedup_key",),
                name="uniq_notification_dedup_key_open",
            ),
        ),
    ]
