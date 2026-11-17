from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("dcm_ai_policy", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="aimodelpolicy",
            name="verified_execution",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="aimodelpolicy",
            name="families",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="aimodelpolicy",
            name="default_provider",
            field=models.CharField(blank=True, default="", max_length=16),
        ),
        migrations.AddField(
            model_name="aimodelpolicy",
            name="default_family",
            field=models.CharField(blank=True, default="", max_length=16),
        ),
    ]
