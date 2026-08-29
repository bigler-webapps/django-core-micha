from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("django_core_micha_notifications", "0009_notification_resolved_at"),
    ]

    operations = [
        migrations.AlterField(
            model_name="notification",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True, db_index=True),
        ),
        migrations.AddIndex(
            model_name="notificationrecipient",
            index=models.Index(
                fields=["user", "seen_at", "dismissed_at"],
                name="django_core_user_id_c960f3_idx",
            ),
        ),
    ]
