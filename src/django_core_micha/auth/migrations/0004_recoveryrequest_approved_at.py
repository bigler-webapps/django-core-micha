# Generated for DCM-NEW-recovery-ttl-from-creation: anchor the post-approval
# TTL window on the approval moment instead of request creation.
from django.db import migrations, models


def backfill_approved_at(apps, schema_editor):
    """Set `approved_at = resolved_at` for already-APPROVED rows.

    Rationale: before this migration, the approval timestamp lived in
    `resolved_at` (set by `mark_resolved(APPROVED, ...)`). `mark_completed`
    later overwrites `resolved_at` with the completion time, so this backfill
    intentionally targets only rows still in APPROVED state — completed rows
    no longer need an accurate approval timestamp (they are terminal and
    not re-evaluated by `expires_at`).
    """
    RecoveryRequest = apps.get_model("django_core_micha_auth", "RecoveryRequest")
    RecoveryRequest.objects.filter(status="approved", approved_at__isnull=True).update(
        approved_at=models.F("resolved_at"),
    )


class Migration(migrations.Migration):

    dependencies = [
        ("django_core_micha_auth", "0003_recoveryrequest_token_hmac"),
    ]

    operations = [
        migrations.AddField(
            model_name="recoveryrequest",
            name="approved_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(backfill_approved_at, reverse_code=migrations.RunPython.noop),
    ]
