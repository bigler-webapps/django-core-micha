import time

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from ...policy import policy_enabled, refresh_policy


class Command(BaseCommand):
    help = "Fetch, durably apply and acknowledge the deployment's managed AI policy."

    def add_arguments(self, parser):
        parser.add_argument("--watch", action="store_true", help="Refresh every ten seconds while running.")

    def handle(self, *args, watch, **options):
        if not policy_enabled():
            raise CommandError("Managed AI policy is not enabled.")
        while True:
            try:
                refresh_policy(force=True)
            except Exception:
                if not watch:
                    raise CommandError("Managed AI policy could not be refreshed.") from None
                self.stderr.write("Managed AI policy refresh unavailable.")
            finally:
                close_old_connections()
            if not watch:
                return
            time.sleep(10)
