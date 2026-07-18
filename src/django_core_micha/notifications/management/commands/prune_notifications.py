"""Remove expired and aged-out canonical notifications."""
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone


class Command(BaseCommand):
    help = "Delete expired notifications and rows older than NOTIFICATIONS_RETENTION_DAYS (default 90)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print the number of notifications that would be deleted.",
        )

    def handle(self, *args, **options):
        from django_core_micha.notifications.models import Notification

        days = getattr(settings, "NOTIFICATIONS_RETENTION_DAYS", 90)
        now = timezone.now()
        cutoff = now - timezone.timedelta(days=days)
        notifications = Notification.objects.filter(
            Q(expires_at__lt=now) | Q(created_at__lt=cutoff)
        )

        if options["dry_run"]:
            self.stdout.write(
                f"Would delete {notifications.count()} expired or aged-out Notification rows."
            )
            return

        deleted, _ = notifications.delete()
        self.stdout.write(self.style.SUCCESS(f"Deleted {deleted} expired or aged-out Notification rows."))
