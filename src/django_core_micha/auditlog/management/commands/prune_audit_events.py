from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Delete AuditEvent rows older than AUDITLOG_RETENTION_DAYS (default 730)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=None,
            help="Override retention window in days.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print count without deleting.",
        )

    def handle(self, *args, **options):
        from django_core_micha.auditlog.models import AuditEvent

        days = options["days"]
        if days is None:
            days = getattr(settings, "AUDITLOG_RETENTION_DAYS", 730)

        cutoff = timezone.now() - timezone.timedelta(days=days)
        qs = AuditEvent.objects.filter(created_at__lt=cutoff)

        if options["dry_run"]:
            self.stdout.write(f"Would delete {qs.count()} AuditEvent rows older than {days} days.")
            return

        deleted, _ = qs.delete()
        self.stdout.write(self.style.SUCCESS(f"Deleted {deleted} AuditEvent rows older than {days} days."))
