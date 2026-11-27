from django.db import migrations


def convert_grants(apps, schema_editor):
    Chat = apps.get_model("dcm_assistant", "AssistantChat")
    chats = Chat.objects.using(schema_editor.connection.alias)
    for chat in chats.exclude(group_grants__isnull=True).iterator():
        value = chat.group_grants
        chat.group_grants = list(value) if isinstance(value, dict) else value if isinstance(value, list) else []
        chat.save(using=schema_editor.connection.alias, update_fields=["group_grants"])


def fail_closed_grants(apps, schema_editor):
    # Rolling back to 3.44.0 code: it expects per-group dicts, so a converted list becomes "no grants" (deny).
    Chat = apps.get_model("dcm_assistant", "AssistantChat")
    chats = Chat.objects.using(schema_editor.connection.alias)
    for chat in chats.exclude(group_grants__isnull=True).iterator():
        if isinstance(chat.group_grants, list):
            chat.group_grants = {}
            chat.save(using=schema_editor.connection.alias, update_fields=["group_grants"])


class Migration(migrations.Migration):
    dependencies = [("dcm_assistant", "0012_catalogue_baseline")]
    operations = [
        migrations.RunPython(convert_grants, fail_closed_grants),
        migrations.DeleteModel(name="AssistantCatalogueDeployment"),
        migrations.DeleteModel(name="AssistantCatalogueState"),
    ]
