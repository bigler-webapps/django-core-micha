from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("dcm_assistant", "0003_turn_sender")]
    operations = [
        migrations.AddField(model_name="assistantturn", name="cost_usd_micros",
            field=models.BigIntegerField(default=0)),
        migrations.AddField(model_name="assistantturn", name="context_tokens",
            field=models.PositiveIntegerField(default=0)),
    ]
