from django.core.management.base import BaseCommand, CommandError
class Command(BaseCommand):
    def handle(self,*args,**options):
        from django_core_micha.billing.config import load_billing_config
        try: load_billing_config()
        except Exception as exc: raise CommandError(str(exc)) from None
        self.stdout.write("billing.yaml is valid")
