from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("django_core_micha_notifications", "0010_notification_feed_query_indexes")]

    operations = [
        migrations.AddField(
            model_name="notificationdelivery",
            name="claim_token",
            field=models.UUIDField(blank=True, null=True),
        ),
    ]
