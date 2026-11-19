import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("dcm_test_management", "0001_initial")]

    operations = [
        migrations.CreateModel(
            name="TestMailMessage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("handle", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("kind", models.CharField(default="password_reset", max_length=32)),
                ("link", models.TextField(blank=True)),
                ("status", models.CharField(choices=[("captured", "Captured"), ("claimed", "Claimed"), ("discarded", "Discarded")], default="captured", max_length=16)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("account", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="mail_messages", to="dcm_test_management.testaccount")),
                ("run", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="mail_messages", to="dcm_test_management.testrun")),
            ],
        ),
    ]
