# Generated for S51 (constant-time recovery-token lookup).
import hashlib
import hmac

from django.conf import settings
from django.db import migrations, models


def _compute_token_hmac(token: str) -> str:
    secret = settings.SECRET_KEY
    if isinstance(secret, str):
        secret = secret.encode("utf-8")
    return hmac.new(secret, (token or "").encode("utf-8"), hashlib.sha256).hexdigest()


def backfill_token_hmac(apps, schema_editor):
    RecoveryRequest = apps.get_model("django_core_micha_auth", "RecoveryRequest")
    for row in RecoveryRequest.objects.all().only("id", "token"):
        RecoveryRequest.objects.filter(pk=row.pk).update(
            token_hmac=_compute_token_hmac(row.token or "")
        )


def noop_reverse(apps, schema_editor):
    RecoveryRequest = apps.get_model("django_core_micha_auth", "RecoveryRequest")
    RecoveryRequest.objects.update(token_hmac="")


class Migration(migrations.Migration):

    dependencies = [
        ("django_core_micha_auth", "0002_recoveryrequest_support_note"),
    ]

    operations = [
        migrations.AddField(
            model_name="recoveryrequest",
            name="token_hmac",
            field=models.CharField(blank=True, db_index=True, default="", max_length=64),
        ),
        migrations.RunPython(backfill_token_hmac, reverse_code=noop_reverse),
    ]
