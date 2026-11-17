from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("dcm_assistant", "0006_assistant_config")]
    operations = [migrations.AddField(
        model_name="assistantturn",
        name="execution_results",
        field=models.JSONField(blank=True, default=list),
    )]
