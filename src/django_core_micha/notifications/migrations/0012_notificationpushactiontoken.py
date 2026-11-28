"""Add recipient-bound browser-push action tokens."""

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("django_core_micha_notifications", "0011_notificationdelivery_claim_token")]

    operations = [
        migrations.CreateModel(
            name="NotificationPushActionToken",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("action", models.CharField(max_length=16)),
                ("token_hash", models.CharField(max_length=64, unique=True)),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("consumed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("delivery", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="action_tokens", to="django_core_micha_notifications.notificationdelivery")),
                ("recipient", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="push_action_tokens", to="django_core_micha_notifications.notificationrecipient")),
            ],
            options={"indexes": [models.Index(fields=["expires_at", "consumed_at"], name="notif_action_expiry_idx")]},
        ),
    ]
