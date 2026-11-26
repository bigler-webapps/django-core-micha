from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [("dcm_billing", "0007_object_billing")]
    operations = [
        migrations.AddField(model_name="objectcheckoutattempt", name="stripe_customer_id", field=models.CharField(blank=True, max_length=255)),
        migrations.AddField(model_name="objectsubscription", name="stripe_customer_id", field=models.CharField(blank=True, max_length=255)),
        migrations.AddConstraint(model_name="objectcheckoutattempt", constraint=models.UniqueConstraint(fields=("stripe_customer_id",), condition=~Q(stripe_customer_id=""), name="dcm_bill_obj_customer_unique")),
    ]
