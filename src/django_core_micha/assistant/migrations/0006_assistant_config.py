from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("dcm_assistant", "0005_assistant_summary")]
    operations = [migrations.CreateModel(name="AssistantConfig", fields=[
        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
        ("limit_usd_micros", models.PositiveBigIntegerField(blank=True, null=True)),
        ("disabled_entries", models.JSONField(blank=True, default=list)),
        ("default_entry", models.CharField(blank=True, default="", max_length=192)),
        ("updated_at", models.DateTimeField(auto_now=True)),
    ])]
