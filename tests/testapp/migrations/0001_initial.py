"""Initial tables of the test app.

The test app has foreign keys to the user model. PostgreSQL enforces them when the tables are
created, so the tables must be created by a migration that depends on the user model's app
(unmigrated apps are created before any migrated app and would reference a missing table).
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Widget",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100)),
                ("secret", models.CharField(blank=True, max_length=100)),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="Gadget",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=100)),
            ],
        ),
        migrations.CreateModel(
            name="SignupQrTokenStub",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("token_hash", models.CharField(db_index=True, max_length=64, unique=True)),
                ("mode", models.CharField(max_length=32)),
                ("registration_context", models.JSONField(blank=True, default=dict)),
                ("expires_at", models.DateTimeField()),
                ("max_redemptions", models.PositiveSmallIntegerField(default=1)),
                ("use_count", models.PositiveSmallIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["-created_at"], "abstract": False},
        ),
        migrations.CreateModel(
            name="RegistrationBindingStub",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("reference_hash", models.CharField(max_length=64, unique=True)),
                ("email", models.EmailField(max_length=254)),
                ("consumed", models.BooleanField(default=False)),
            ],
        ),
        migrations.CreateModel(
            name="RegistrationGrantStub",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "user",
                    models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL),
                ),
            ],
        ),
        migrations.CreateModel(
            name="AuthPolicyStub",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("allow_admin_invite", models.BooleanField(default=True)),
                ("allow_self_signup_access_code", models.BooleanField(default=False)),
                ("allow_self_signup_open", models.BooleanField(default=False)),
                ("allow_self_signup_email_domain", models.BooleanField(default=False)),
                ("allow_self_signup_qr", models.BooleanField(default=False)),
                ("allowed_email_domains", models.JSONField(default=list)),
                ("required_auth_factor_count", models.IntegerField(default=1)),
                ("admin_required_auth_factor_count", models.IntegerField(default=1)),
                ("signup_qr_expiry_days", models.IntegerField(default=90)),
                ("access_code_single_use", models.BooleanField(default=False)),
            ],
        ),
        migrations.CreateModel(
            name="AuthPolicyStubAlt",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("allow_admin_invite", models.BooleanField(default=True)),
                ("allow_self_signup_access_code", models.BooleanField(default=False)),
                ("allow_self_signup_open", models.BooleanField(default=False)),
                ("allow_self_signup_email_domain", models.BooleanField(default=False)),
                ("allow_self_signup_qr", models.BooleanField(default=False)),
                ("allowed_email_domains", models.JSONField(default=list)),
                ("required_auth_factor_count", models.IntegerField(default=1)),
                ("admin_required_auth_factor_count", models.IntegerField(default=1)),
                ("signup_qr_expiry_days", models.IntegerField(default=90)),
                ("access_code_single_use", models.BooleanField(default=False)),
            ],
        ),
    ]
