from django.core.management.base import BaseCommand
from django_core_micha.assistant.turns import reconcile_interrupted

class Command(BaseCommand):
    help = "Mark stale assistant turns interrupted."
    def handle(self, *args, **options):
        count = reconcile_interrupted(everything=False)
        self.stdout.write(str(count))
