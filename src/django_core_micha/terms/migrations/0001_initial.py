import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.utils import timezone


class Migration(migrations.Migration):
    initial = True
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [migrations.CreateModel(name="TermsAcceptance", fields=[
        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
        ("app", models.CharField(max_length=80)), ("version", models.CharField(max_length=64)),
        ("document_url", models.URLField(max_length=500)), ("accepted_at", models.DateTimeField(default=timezone.now)),
        ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="terms_acceptances", to=settings.AUTH_USER_MODEL)),
    ], options={"constraints": [models.UniqueConstraint(fields=("user", "app", "version"), name="dcm_terms_acceptance_unique")]})]
