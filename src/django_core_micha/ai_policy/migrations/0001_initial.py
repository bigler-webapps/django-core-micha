from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [migrations.CreateModel(
        name="AIModelPolicy",
        fields=[
            ("id", models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
            ("app_id", models.CharField(max_length=128)),
            ("environment", models.CharField(max_length=128)),
            ("revision", models.PositiveBigIntegerField()),
            ("allowed", models.JSONField(default=dict)),
            ("standard_source_id", models.CharField(max_length=192)),
            ("standard_model_id", models.CharField(max_length=192)),
            ("applied_at", models.DateTimeField(auto_now=True)),
            ("last_refresh_at", models.DateTimeField(blank=True, null=True)),
        ],
    )]
