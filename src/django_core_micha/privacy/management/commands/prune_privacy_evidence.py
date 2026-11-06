from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from django_core_micha.privacy.manifest import current_manifest
from django_core_micha.privacy.models import PrivacyChoice, PrivacyDecision, PrivacyReceipt


DEFAULT_RETENTION_DAYS = 365


class Command(BaseCommand):
    help = "Prune expired privacy choices and old decision evidence."

    def add_arguments(self, parser):
        parser.add_argument(
            "--older-than-days",
            type=int,
            default=None,
            help="Override DCM_PRIVACY evidence retention for this run.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print the number of rows that would be deleted without deleting.",
        )

    def handle(self, *args, **options):
        older_than_days = options["older_than_days"]
        if older_than_days is None:
            manifest = current_manifest()
            older_than_days = (
                manifest["evidence_retention_days"] if manifest is not None else DEFAULT_RETENTION_DAYS
            )
        if not 1 <= older_than_days <= 1095:
            raise CommandError("--older-than-days must be between 1 and 1095.")

        cutoff = timezone.now() - timezone.timedelta(days=older_than_days)
        old_decisions = PrivacyDecision.objects.filter(decided_at__lt=cutoff)
        # Bounded from the event (effective_at), not from expiry: evidence never outlives the window.
        expired_choices = PrivacyChoice.objects.filter(effective_at__lt=cutoff)
        receipts = PrivacyReceipt.objects.filter(created_at__lt=cutoff)

        if options["dry_run"]:
            decisions_count = old_decisions.count()
            choices_count = expired_choices.count()
            receipts_count = sum(
                not receipt.decisions.exclude(decided_at__lt=cutoff).exists()
                and not receipt.choices.exclude(effective_at__lt=cutoff).exists()
                for receipt in receipts.iterator()
            )
            self.stdout.write(
                f"Would delete {decisions_count} privacy decisions, {choices_count} expired choices, "
                f"and {receipts_count} empty receipts older than {older_than_days} days."
            )
            return

        decisions_count, _ = old_decisions.delete()
        choices_count, _ = expired_choices.delete()
        receipt_ids = []
        for receipt in receipts.iterator():
            if not receipt.decisions.exists() and not receipt.choices.exists():
                receipt_ids.append(receipt.pk)
        receipts_count = 0
        if receipt_ids:
            receipts_count, _ = PrivacyReceipt.objects.filter(pk__in=receipt_ids).delete()
        self.stdout.write(
            self.style.SUCCESS(
                f"Deleted {decisions_count} privacy decisions, {choices_count} expired choices, "
                f"and {receipts_count} empty receipts older than {older_than_days} days."
            )
        )
