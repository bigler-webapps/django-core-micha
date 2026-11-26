from django.conf import settings
from django.db import migrations, models
from django.db.models import Q
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [("dcm_billing", "0006_ai_cost_source"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]

    operations = [
        migrations.CreateModel(name="ObjectBillingSubject", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("app", models.CharField(max_length=80)),
            ("object_key", models.CharField(max_length=255)),
        ], options={"constraints": [
            models.UniqueConstraint(fields=("app", "object_key"), name="dcm_bill_object_subject_unique"),
            models.CheckConstraint(condition=~Q(app="") & ~Q(object_key=""), name="dcm_bill_object_subject_nonempty"),
        ]}),
        migrations.CreateModel(name="ObjectSubscription", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("plan_key", models.CharField(max_length=40)),
            ("stripe_subscription_id", models.CharField(max_length=255, unique=True)),
            ("status", models.CharField(max_length=32)),
            ("current_period_start", models.DateTimeField(blank=True, null=True)),
            ("current_period_end", models.DateTimeField(blank=True, null=True)),
            ("cancel_at_period_end", models.BooleanField(default=False)),
            ("ended_at", models.DateTimeField(blank=True, null=True)),
            ("blocks_purchase", models.BooleanField(default=True)),
            ("payer", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="billing_object_subscriptions", to=settings.AUTH_USER_MODEL)),
            ("subject", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="subscriptions", to="dcm_billing.objectbillingsubject")),
        ], options={"constraints": [models.UniqueConstraint(fields=("subject",), condition=Q(blocks_purchase=True), name="dcm_bill_object_sub_block_unique")]}),
        migrations.CreateModel(name="ObjectCheckoutAttempt", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("reference", models.CharField(max_length=64, unique=True)),
            ("plan_key", models.CharField(max_length=40)),
            ("idempotency_key", models.CharField(max_length=64)),
            ("status", models.CharField(choices=[(x, x) for x in ("open", "processing", "paid", "completed", "failed", "expired", "creation_failed")], default="open", max_length=16)),
            ("stripe_price_id", models.CharField(max_length=255)),
            ("stripe_session_id", models.CharField(blank=True, max_length=255)),
            ("stripe_checkout_url", models.TextField(blank=True)),
            ("expires_at", models.DateTimeField(blank=True, null=True)),
            ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
            ("updated_at", models.DateTimeField(auto_now=True)),
            ("payer", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="billing_object_attempts", to=settings.AUTH_USER_MODEL)),
            ("subject", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="attempts", to="dcm_billing.objectbillingsubject")),
        ], options={"constraints": [
            models.UniqueConstraint(fields=("subject",), condition=Q(status__in=("open", "processing", "paid")), name="dcm_bill_obj_attempt_block_unique"),
            models.UniqueConstraint(fields=("payer", "subject", "idempotency_key"), name="dcm_bill_obj_attempt_key_unique"),
            models.UniqueConstraint(fields=("stripe_session_id",), condition=~Q(stripe_session_id=""), name="dcm_bill_obj_attempt_session_unique"),
        ]}),
        migrations.CreateModel(name="ObjectAccessGrant", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("reason", models.TextField()),
            ("granted_at", models.DateTimeField(default=django.utils.timezone.now)),
            ("revoked_at", models.DateTimeField(blank=True, null=True)),
            ("revocation_reason", models.TextField(blank=True)),
            ("granted_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="billing_object_grants", to=settings.AUTH_USER_MODEL)),
            ("revoked_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="billing_object_revocations", to=settings.AUTH_USER_MODEL)),
            ("subject", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="grants", to="dcm_billing.objectbillingsubject")),
        ], options={"constraints": [
            models.UniqueConstraint(fields=("subject",), condition=Q(revoked_at__isnull=True), name="dcm_bill_obj_grant_active_unique"),
            models.CheckConstraint(condition=~Q(reason=""), name="dcm_bill_obj_grant_reason_nonempty"),
            models.CheckConstraint(condition=(Q(revoked_at__isnull=True, revoked_by__isnull=True, revocation_reason="") | (Q(revoked_at__isnull=False, revoked_by__isnull=False) & ~Q(revocation_reason=""))), name="dcm_bill_obj_revoke_audit"),
        ]}),
    ]
