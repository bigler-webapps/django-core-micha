from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from django.utils import timezone
from django_core_micha.billing.models import FreeAccess
class Command(BaseCommand):
    def add_arguments(self,p): p.add_argument("user")
    def handle(self,*a,**o):
        U=get_user_model(); v=o["user"]
        try: user=U.objects.get(pk=v) if v.isdigit() else U.objects.get(email__iexact=v)
        except U.DoesNotExist: raise CommandError("user not found")
        row=FreeAccess.objects.filter(user=user,revoked_at__isnull=True).order_by("-granted_at").first()
        if row: row.revoked_at=timezone.now(); row.save(update_fields=("revoked_at",))
        self.stdout.write("revoked" if row else "not granted")
