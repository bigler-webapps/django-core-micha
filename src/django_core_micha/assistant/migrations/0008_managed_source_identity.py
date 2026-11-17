from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("dcm_assistant", "0007_assistantturn_execution_results")]
    operations = [
        migrations.AddField("assistantchat", "selected_source_id", models.CharField(blank=True, default="", max_length=192)),
        migrations.AddField("assistantchat", "session_source_id", models.CharField(blank=True, default="", max_length=192)),
        migrations.AddField("assistantturn", "source_id", models.CharField(blank=True, default="", max_length=192)),
    ]
