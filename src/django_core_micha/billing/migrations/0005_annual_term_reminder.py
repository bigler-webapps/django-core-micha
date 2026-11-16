import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("dcm_billing", "0004_contracts")]

    operations = [
        migrations.CreateModel(
            name="AnnualTermReminder",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[(x, x) for x in ("pending", "sending", "failed", "ambiguous", "sent", "unresolved", "cancelled")], db_index=True, default="pending", max_length=16)),
                ("idempotency_key", models.CharField(max_length=180, unique=True)),
                ("payload", models.JSONField(default=dict)),
                ("due_at", models.DateTimeField()),
                ("first_attempt_at", models.DateTimeField(blank=True, null=True)),
                ("last_attempt_at", models.DateTimeField(blank=True, null=True)),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                ("attempts", models.PositiveIntegerField(default=0)),
                ("late", models.BooleanField(default=False)),
                ("provider_message_id", models.CharField(blank=True, default="", max_length=255)),
                ("last_error_code", models.CharField(blank=True, default="", max_length=120)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("contract", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="annual_term_reminder", to="dcm_billing.contract")),
            ],
        ),
        migrations.AddIndex(model_name="annualtermreminder", index=models.Index(fields=["status", "due_at"], name="dcm_bill_reminder_due")),
    ]
