from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('django_core_micha_auth', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='recoveryrequest',
            name='support_note',
            field=models.TextField(blank=True, default='', help_text='Reason provided by the support agent when approving or rejecting.'),
        ),
    ]