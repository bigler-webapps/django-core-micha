from django.core.management.base import BaseCommand
from django_core_micha.billing.services import expire_tokens
class Command(BaseCommand):
    def handle(self,*a,**o): self.stdout.write(str(expire_tokens()))
