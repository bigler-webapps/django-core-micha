from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [migrations.CreateModel(name="ReceiptConsent", fields=[
        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
        ("granted_at", models.DateTimeField(blank=True, null=True)),
        ("revoked_at", models.DateTimeField(blank=True, null=True)),
        ("source", models.CharField(blank=True, max_length=40)),
        ("updated_at", models.DateTimeField(auto_now=True)),
        ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="receipt_consent", to=settings.AUTH_USER_MODEL)),
    ])]

