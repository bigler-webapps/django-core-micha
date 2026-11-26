from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('dcm_assistant', '0011_catalogue_activation')]
    operations = [
        migrations.AddField(
            model_name='assistantcataloguestate', name='baseline',
            field=models.JSONField(default=dict),
        ),
    ]
