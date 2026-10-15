from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("dcm_assistant", "0001_initial")]
    operations = [
        migrations.AddField(
            model_name="assistantchat",
            name="session_tokens",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="assistantchat",
            name="session_prompt_version",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
    ]
