"""Delete activity buckets older than the retention window.

Ported from jg-ferien's events/management/commands/cleanup_activity_buckets.py,
adapted to dcm's settings-driven-default + --dry-run convention (matching
notifications/management/commands/prune_notifications.py) rather than a
CLI-only flag. Retention matters more here than it did for jg alone: storage
moved from 4-hour to 1-hour buckets (ACT-1 scope B), which is four times the
row volume for the same coverage.
"""

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from django_core_micha.activity.models import ActivityBucket

DEFAULT_RETENTION_DAYS = 365


class Command(BaseCommand):
    help = "Delete ActivityBucket rows older than the retention window."

    def add_arguments(self, parser):
        parser.add_argument(
            "--older-than-days",
            type=int,
            default=None,
            help="Override ACTIVITY_RETENTION_DAYS (default 365) for this run.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print the number of rows that would be deleted without deleting.",
        )

    def handle(self, *args, **options):
        older_than_days = options["older_than_days"]
        if older_than_days is None:
            older_than_days = getattr(settings, "ACTIVITY_RETENTION_DAYS", DEFAULT_RETENTION_DAYS)
        cutoff = timezone.now() - timezone.timedelta(days=older_than_days)
        queryset = ActivityBucket.objects.filter(bucket_start__lt=cutoff)

        if options["dry_run"]:
            self.stdout.write(
                f"Would delete {queryset.count()} activity bucket rows older than {older_than_days} days."
            )
            return

        deleted_count, _details = queryset.delete()
        self.stdout.write(
            self.style.SUCCESS(
                f"Deleted {deleted_count} activity bucket rows older than {older_than_days} days."
            )
        )
