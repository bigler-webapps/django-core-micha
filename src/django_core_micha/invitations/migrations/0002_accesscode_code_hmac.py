# Generated for S18 (constant-time access-code lookup).
import hashlib
import hmac

from django.conf import settings
from django.db import migrations, models


def _compute_code_hmac(code: str) -> str:
    secret = settings.SECRET_KEY
    if isinstance(secret, str):
        secret = secret.encode("utf-8")
    return hmac.new(secret, (code or "").encode("utf-8"), hashlib.sha256).hexdigest()


def backfill_code_hmac(apps, schema_editor):
    AccessCode = apps.get_model("django_core_micha_invitations", "AccessCode")
    for row in AccessCode.objects.all().only("id", "code"):
        AccessCode.objects.filter(pk=row.pk).update(
            code_hmac=_compute_code_hmac(row.code or "")
        )


def noop_reverse(apps, schema_editor):
    # Reverse: just clear the hmac. The field itself is removed by the reverse
    # schema migration; this exists so the data migration can be rolled back
    # without errors.
    AccessCode = apps.get_model("django_core_micha_invitations", "AccessCode")
    AccessCode.objects.update(code_hmac="")


class Migration(migrations.Migration):

    dependencies = [
        ("django_core_micha_invitations", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="accesscode",
            name="code_hmac",
            field=models.CharField(blank=True, db_index=True, default="", max_length=64),
        ),
        migrations.RunPython(backfill_code_hmac, reverse_code=noop_reverse),
    ]
