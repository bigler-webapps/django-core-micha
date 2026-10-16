from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("dcm_assistant", "0004_turn_cost_context")]
    operations = [
        migrations.CreateModel(
            name="AssistantSummary",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("up_to_sequence", models.PositiveIntegerField()),
                ("text", models.TextField()),
                ("usage", models.JSONField(blank=True, default=dict)),
                ("cost_usd_micros", models.BigIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("chat", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="summaries", to="dcm_assistant.assistantchat")),
            ],
            options={"constraints": [models.UniqueConstraint(fields=("chat", "up_to_sequence"), name="dcm_asst_summary_range_unique")],
                     "indexes": [models.Index(fields=["chat", "-up_to_sequence"], name="dcm_asst_summary_latest_idx")]},
        ),
    ]
