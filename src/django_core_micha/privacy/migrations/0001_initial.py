import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="PrivacyReceipt",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("receipt_id", models.CharField(max_length=64, unique=True)),
                ("app", models.CharField(max_length=80)),
                ("site", models.CharField(max_length=80)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("seq", models.PositiveIntegerField(default=0)),
            ],
            options={
                "db_table": "dcm_privacy_receipt",
                "indexes": [models.Index(fields=["app", "site"], name="dcm_priv_receipt_scope_idx")],
            },
        ),
        migrations.CreateModel(
            name="PrivacyChoice",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("purpose_id", models.CharField(max_length=41)),
                ("purpose_revision", models.PositiveIntegerField()),
                ("state", models.CharField(choices=[("granted", "Granted"), ("refused", "Refused")], max_length=8)),
                ("effective_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("expires_at", models.DateTimeField()),
                ("receipt", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="choices", to="dcm_privacy.privacyreceipt")),
            ],
            options={
                "db_table": "dcm_privacy_choice",
                "indexes": [models.Index(fields=["expires_at"], name="dcm_priv_choice_expiry_idx")],
            },
        ),
        migrations.CreateModel(
            name="PrivacyDecision",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("purpose_id", models.CharField(max_length=41)),
                ("purpose_revision", models.PositiveIntegerField()),
                ("kind", models.CharField(choices=[("granted", "Granted"), ("refused", "Refused"), ("withdrawn", "Withdrawn")], max_length=9)),
                ("seq", models.PositiveIntegerField()),
                ("decided_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("locale", models.CharField(max_length=5)),
                ("notice_version", models.CharField(max_length=64)),
                ("channel", models.CharField(choices=[("banner", "Banner"), ("settings", "Settings"), ("other", "Other")], max_length=8)),
                ("request_id", models.CharField(max_length=36)),
                ("receipt", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="decisions", to="dcm_privacy.privacyreceipt")),
            ],
            options={
                "db_table": "dcm_privacy_decision",
                "indexes": [models.Index(fields=["decided_at"], name="dcm_priv_decision_time_idx")],
            },
        ),
        migrations.AddConstraint(
            model_name="privacychoice",
            constraint=models.UniqueConstraint(fields=("receipt", "purpose_id"), name="dcm_priv_choice_unique"),
        ),
        migrations.AddConstraint(
            model_name="privacydecision",
            constraint=models.UniqueConstraint(fields=("receipt", "request_id", "purpose_id"), name="dcm_priv_dec_req_uniq"),
        ),
    ]
