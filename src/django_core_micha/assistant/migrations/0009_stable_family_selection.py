from django.db import migrations, models


def mark_existing_reported_usage(apps, schema_editor):
    AssistantTurn = apps.get_model("dcm_assistant", "AssistantTurn")
    database = schema_editor.connection.alias
    batch = []
    for turn in AssistantTurn.objects.using(database).only("pk", "usage").iterator():
        usage = turn.usage if isinstance(turn.usage, dict) else {}
        if any(type(usage.get(key)) is int and usage.get(key, 0) > 0 for key in
               ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")):
            batch.append(turn.pk)
        if len(batch) >= 500:
            AssistantTurn.objects.using(database).filter(pk__in=batch).update(usage_reported=True)
            batch.clear()
    if batch:
        AssistantTurn.objects.using(database).filter(pk__in=batch).update(usage_reported=True)


class Migration(migrations.Migration):
    dependencies = [("dcm_assistant", "0008_managed_source_identity")]

    operations = [
        migrations.AddField(
            model_name="assistantchat",
            name="selected_provider",
            field=models.CharField(blank=True, default="", max_length=16),
        ),
        migrations.AddField(
            model_name="assistantchat",
            name="selected_family",
            field=models.CharField(blank=True, default="", max_length=16),
        ),
        migrations.AddField(
            model_name="assistantturn",
            name="provider",
            field=models.CharField(blank=True, default="", max_length=16),
        ),
        migrations.AddField(
            model_name="assistantturn",
            name="family",
            field=models.CharField(blank=True, default="", max_length=16),
        ),
        migrations.AddField(
            model_name="assistantturn",
            name="provider_started",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="assistantturn",
            name="usage_reported",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(mark_existing_reported_usage, migrations.RunPython.noop),
    ]
