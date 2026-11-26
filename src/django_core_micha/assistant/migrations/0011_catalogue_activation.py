from django.db import migrations, models


def create_state(apps, schema_editor):
    apps.get_model('dcm_assistant', 'AssistantCatalogueState').objects.using(
        schema_editor.connection.alias).create(pk=1)


class Migration(migrations.Migration):
    dependencies = [('dcm_assistant', '0010_chat_group_grants')]
    operations = [
        migrations.CreateModel(name='AssistantCatalogueState', fields=[
            ('id', models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False, serialize=False)),
            ('revision', models.PositiveBigIntegerField(default=0)),
            ('deployment', models.CharField(max_length=128, blank=True)),
            ('catalogue_hash', models.CharField(max_length=64, blank=True)),
            ('members', models.JSONField(default=dict)),
        ]),
        migrations.CreateModel(name='AssistantCatalogueDeployment', fields=[
            ('deployment', models.CharField(max_length=128, primary_key=True, serialize=False)),
        ]),
        migrations.RunPython(create_state, migrations.RunPython.noop),
    ]
