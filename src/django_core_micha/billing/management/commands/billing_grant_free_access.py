from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from django_core_micha.billing.models import FreeAccess
class Command(BaseCommand):
    def add_arguments(self,p): p.add_argument("user"); p.add_argument("--reason",required=True)
    def handle(self,*a,**o):
        U=get_user_model(); v=o["user"]
        try: user=U.objects.get(pk=v) if v.isdigit() else U.objects.get(email__iexact=v)
        except U.DoesNotExist: raise CommandError("user not found")
        FreeAccess.objects.create(user=user,granted_by_label="operator",reason=o["reason"]); self.stdout.write("granted")
