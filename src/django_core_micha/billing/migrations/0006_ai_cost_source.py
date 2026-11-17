from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [("dcm_billing", "0005_annual_term_reminder")]

    operations = [
        migrations.AddField(
            model_name="aicostrecord",
            name="source_id",
            field=models.CharField(blank=True, db_index=True, default="", max_length=192),
        ),
        migrations.AddField(
            model_name="aicostrecord",
            name="cache_read_tokens",
            field=models.PositiveBigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="aicostrecord",
            name="cache_write_tokens",
            field=models.PositiveBigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="aicostrecord",
            name="usage_status",
            field=models.CharField(choices=[("reported", "reported"), ("unresolved", "unresolved")],
                                   default="reported", max_length=16),
        ),
        migrations.AddField(
            model_name="aicostrecord",
            name="settlement_status",
            field=models.CharField(choices=[("pending", "pending"), ("settled", "settled"),
                                            ("not_required", "not_required")],
                                   default="not_required", max_length=16),
        ),
        migrations.AlterField(
            model_name="aicostrecord",
            name="cost_usd_micros",
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="aicostrecord",
            name="operation_reference",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddConstraint(
            model_name="aicostrecord",
            constraint=models.UniqueConstraint(condition=Q(operation_reference__isnull=False), fields=("app", "purpose", "operation_reference"),
                                               name="dcm_bill_ai_cost_ref_unique"),
        ),
    ]
