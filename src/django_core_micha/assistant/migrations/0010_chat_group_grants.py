from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("dcm_assistant", "0009_stable_family_selection")]

    operations = [
        migrations.AddField(model_name="assistantchat", name="group_grants",
                            field=models.JSONField(blank=True, default=None, null=True)),
        migrations.AddField(model_name="assistantchat", name="grant_revision",
                            field=models.PositiveBigIntegerField(default=0)),
    ]
