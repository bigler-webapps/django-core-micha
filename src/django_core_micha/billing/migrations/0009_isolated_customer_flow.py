from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("dcm_billing", "0008_object_customer")]
    operations = [
        migrations.AddField(
            model_name="objectcheckoutattempt",
            name="isolated_customer_flow",
            field=models.BooleanField(default=False),
        ),
    ]
