import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import Q
from django.utils import timezone


class Migration(migrations.Migration):
    dependencies = [("dcm_billing", "0002_purchase_withdrawal"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [migrations.CreateModel(name="CheckoutAttempt", fields=[
        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
        ("reference", models.CharField(max_length=64, unique=True)), ("app", models.CharField(max_length=80)),
        ("plan_key", models.CharField(max_length=40)), ("context", models.CharField(max_length=40)),
        ("idempotency_key", models.CharField(max_length=64)),
        ("status", models.CharField(choices=[("open", "open"), ("processing", "processing"), ("paid", "paid"), ("failed", "failed"), ("expired", "expired")], default="open", max_length=16)),
        ("stripe_price_id", models.CharField(max_length=255)), ("stripe_session_id", models.CharField(blank=True, max_length=255)),
        ("stripe_checkout_url", models.TextField(blank=True)), ("expires_at", models.DateTimeField(null=True)),
        ("created_at", models.DateTimeField(default=timezone.now)), ("updated_at", models.DateTimeField(auto_now=True)),
        ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="billing_checkout_attempts", to=settings.AUTH_USER_MODEL)),
    ], options={"constraints": [
        models.UniqueConstraint(fields=("user", "app", "idempotency_key"), name="dcm_bill_attempt_key_unique"),
        models.UniqueConstraint(condition=~Q(stripe_session_id=""), fields=("stripe_session_id",), name="dcm_bill_attempt_session_unique"),
    ]})]
