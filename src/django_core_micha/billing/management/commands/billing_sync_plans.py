from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Synchronize declared billing plans to immutable Stripe Products and Prices."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        from django_core_micha.billing.stripe_service import sync_plans
        try:
            for key, result in sync_plans(dry_run=options["dry_run"]):
                self.stdout.write(f"{key}: {result}")
        except Exception as exc:
            raise CommandError(f"billing sync failed ({type(exc).__name__})") from None
