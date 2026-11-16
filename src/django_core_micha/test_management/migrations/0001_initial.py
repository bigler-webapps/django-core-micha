from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(
            name="TestInstance",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("instance_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("controller_token_digest", models.CharField(max_length=64)),
                ("boot_id", models.UUIDField(blank=True, null=True)),
                ("status", models.CharField(choices=[("active", "Active"), ("closed", "Closed")], default="active", max_length=16)),
                ("expires_at", models.DateTimeField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.CreateModel(
            name="TestRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("handle", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("idempotency_key", models.CharField(max_length=200)),
                ("baseline", models.JSONField(default=dict)),
                ("adapter_state", models.JSONField(default=dict)),
                ("recovery_reason", models.CharField(blank=True, max_length=32)),
                ("status", models.CharField(choices=[("active", "Active"), ("recoverable", "Recoverable"), ("reset", "Reset"), ("closed", "Closed")], default="active", max_length=16)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("instance", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="runs", to="dcm_test_management.testinstance")),
            ],
            options={"constraints": [models.UniqueConstraint(fields=("instance", "idempotency_key"), name="dcm_tm_run_instance_idempotency")]},
        ),
        migrations.CreateModel(
            name="TestAccount",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("handle", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("idempotency_key", models.CharField(max_length=200)),
                ("template", models.CharField(max_length=100)),
                ("creation_fingerprint", models.CharField(max_length=64)),
                ("requested_configuration", models.JSONField(default=dict)),
                ("synthetic_identity", models.JSONField(default=dict)),
                ("adapter_ref", models.CharField(max_length=255)),
                ("user_ref", models.CharField(blank=True, max_length=255)),
                ("effective_state", models.JSONField(default=dict)),
                ("status", models.CharField(choices=[("active", "Active"), ("deleted", "Deleted"), ("cleanup_failed", "Cleanup failed")], default="active", max_length=20)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("run", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="accounts", to="dcm_test_management.testrun")),
            ],
            options={"constraints": [models.UniqueConstraint(fields=("run", "idempotency_key"), name="dcm_tm_account_run_idempotency")]},
        ),
        migrations.CreateModel(
            name="TestFixture",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("handle", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("idempotency_key", models.CharField(max_length=200)),
                ("name", models.CharField(max_length=100)),
                ("parameters", models.JSONField(default=dict)),
                ("adapter_ref", models.CharField(max_length=255)),
                ("state", models.JSONField(default=dict)),
                ("status", models.CharField(choices=[("active", "Active"), ("deleted", "Deleted"), ("cleanup_failed", "Cleanup failed")], default="active", max_length=20)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("run", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="fixtures", to="dcm_test_management.testrun")),
            ],
            options={"constraints": [models.UniqueConstraint(fields=("run", "idempotency_key"), name="dcm_tm_fixture_run_idempotency")]},
        ),
    ]
