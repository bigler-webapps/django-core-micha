"""Send digest reminders for registered provider-derived todo types."""
from django.core.management.base import BaseCommand

from django_core_micha.notifications.todo.digests import send_todo_digests


class Command(BaseCommand):
    help = "Send digest reminders for registered provider-derived todos."

    def handle(self, *args, **options):
        summary = send_todo_digests()
        self.stdout.write(
            self.style.SUCCESS(
                f"Scanned {summary.users_scanned} users; sent {summary.digests_sent} digests; "
                f"recorded {summary.threshold_records_created} thresholds."
            )
        )
