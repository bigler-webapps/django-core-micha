import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [("dcm_billing", "0001_initial"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [migrations.CreateModel(name="Purchase", fields=[
        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
        ("kind", models.CharField(choices=[("subscription", "subscription"), ("team_package", "team_package"), ("token_package", "token_package")], max_length=24)),
        ("plan_key", models.CharField(max_length=40)), ("stripe_object_id", models.CharField(max_length=255, unique=True)),
        ("stripe_payment_intent_id", models.CharField(blank=True, max_length=255)), ("stripe_invoice_id", models.CharField(blank=True, max_length=255)),
        ("amount_paid_cents", models.PositiveBigIntegerField()), ("currency", models.CharField(max_length=8)),
        ("billing_country", models.CharField(blank=True, max_length=2)), ("has_business_tax_id", models.BooleanField(default=False)),
        ("purchased_at", models.DateTimeField()), ("term_start", models.DateTimeField(blank=True, null=True)), ("term_end", models.DateTimeField(blank=True, null=True)),
        ("grant_references", models.JSONField(default=list)), ("tokens_granted", models.PositiveBigIntegerField(default=0)),
        ("status", models.CharField(choices=[("active", "active"), ("withdrawn", "withdrawn")], default="active", max_length=16)),
        ("withdrawn_at", models.DateTimeField(blank=True, null=True)), ("refund_cents", models.PositiveBigIntegerField(blank=True, null=True)),
        ("stripe_refund_id", models.CharField(blank=True, max_length=255)), ("withdrawal_note", models.TextField(blank=True)),
        ("withdrawal_email_sent", models.BooleanField(default=False)),
        ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="billing_purchases", to=settings.AUTH_USER_MODEL)),
    ])]
