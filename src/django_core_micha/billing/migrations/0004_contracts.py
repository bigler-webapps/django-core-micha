import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import Q
from django.utils import timezone
from django_core_micha.billing.models import _reference


def backfill_contracts(apps, schema_editor):
    Purchase=apps.get_model("dcm_billing","Purchase")
    Contract=apps.get_model("dcm_billing","Contract")
    Payment=apps.get_model("dcm_billing","ContractPayment")
    for purchase in Purchase.objects.all().iterator():
        contract,created=Contract.objects.get_or_create(purchase_id=purchase.pk,defaults={
            "user_id":purchase.user_id,"app":getattr(settings,"DCM_BILLING_APP",""),"kind":purchase.kind,"plan_key":purchase.plan_key,
            "stripe_object_id":purchase.stripe_object_id,"reference":_reference(),"terms_version":"","seller":"",
            "withdrawal_policy":"prorated","consumer_status":"unknown","billing_country":purchase.billing_country,
            "started_at":purchase.purchased_at,"info_delivered_at":None,"initial_term_end":purchase.term_end,
            "phase":"none","status":"withdrawn" if purchase.status=="withdrawn" else "active",
            "schedule_status":"not_needed","created_at":timezone.now(),"updated_at":timezone.now()})
        if created:
            defaults={"contract_id":contract.pk,"stripe_payment_intent_id":purchase.stripe_payment_intent_id,
                "amount_paid_cents":purchase.amount_paid_cents,"currency":purchase.currency,
                "paid_at":purchase.purchased_at,"billing_reason":"initial"}
            if purchase.stripe_invoice_id:
                Payment.objects.get_or_create(stripe_invoice_id=purchase.stripe_invoice_id,defaults=defaults)
            else:
                Payment.objects.create(stripe_invoice_id="",**defaults)


class Migration(migrations.Migration):
    dependencies=[("dcm_billing","0003_checkoutattempt"),migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations=[
        migrations.CreateModel(name="Contract",fields=[
            ("id",models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name="ID")),
            ("app",models.CharField(db_index=True,max_length=80)),("kind",models.CharField(choices=[("subscription","subscription"),("team_package","team_package"),("token_package","token_package")],max_length=24)),
            ("plan_key",models.CharField(max_length=40)),("stripe_object_id",models.CharField(db_index=True,max_length=255)),("reference",models.CharField(default=_reference,max_length=32,unique=True)),
            ("terms_version",models.CharField(blank=True,default="",max_length=80)),("seller",models.CharField(blank=True,default="",max_length=255)),
            ("withdrawal_policy",models.CharField(choices=[("full","full"),("prorated","prorated")],default="prorated",max_length=16)),
            ("consumer_status",models.CharField(choices=[("consumer","consumer"),("business","business"),("unknown","unknown")],default="unknown",max_length=16)),
            ("billing_country",models.CharField(blank=True,default="",max_length=2)),("started_at",models.DateTimeField(default=timezone.now)),
            ("info_delivered_at",models.DateTimeField(blank=True,null=True)),("initial_term_end",models.DateTimeField(blank=True,null=True)),
            ("continuation_plan_key",models.CharField(blank=True,default="",max_length=40)),("continuation_stripe_price_id",models.CharField(blank=True,default="",max_length=255)),("continuation_amount_cents",models.PositiveBigIntegerField(blank=True,null=True)),
            ("continuation_token_allowance",models.PositiveBigIntegerField(blank=True,null=True)),("continuation_interval",models.CharField(blank=True,default="",max_length=16)),
            ("transition_guard_days",models.PositiveSmallIntegerField(default=7)),
            ("phase",models.CharField(choices=[("initial","initial"),("continuation","continuation"),("none","none")],default="none",max_length=16)),
            ("status",models.CharField(choices=[("active","active"),("cancellation_scheduled","cancellation_scheduled"),("ended","ended"),("withdrawn","withdrawn")],default="active",max_length=32)),
            ("cancel_effective_at",models.DateTimeField(blank=True,null=True)),("stripe_schedule_id",models.CharField(blank=True,default="",max_length=255)),
            ("schedule_status",models.CharField(choices=[("not_needed","not_needed"),("pending","pending"),("created","created"),("failed","failed"),("released","released"),("cancelled","cancelled")],default="not_needed",max_length=16)),
            ("needs_review",models.BooleanField(default=False)),("review_reason",models.CharField(blank=True,default="",max_length=120)),
            ("created_at",models.DateTimeField(default=timezone.now)),("updated_at",models.DateTimeField(auto_now=True)),
            ("purchase",models.OneToOneField(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,related_name="contract",to="dcm_billing.purchase")),
            ("user",models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name="billing_contracts",to=settings.AUTH_USER_MODEL)),
        ]),
        migrations.CreateModel(name="Declaration",fields=[
            ("id",models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name="ID")),("reference",models.CharField(default=_reference,max_length=32,unique=True)),
            ("app",models.CharField(db_index=True,max_length=80)),("kind",models.CharField(choices=[("cancellation","cancellation"),("withdrawal","withdrawal")],max_length=16)),
            ("channel",models.CharField(choices=[("public","public"),("account","account"),("operator","operator")],max_length=16)),("received_at",models.DateTimeField(default=timezone.now,editable=False)),
            ("name",models.CharField(blank=True,default="",max_length=200)),("email",models.EmailField(blank=True,default="",max_length=254)),
            ("contract_hint",models.CharField(blank=True,default="",max_length=200)),("cancellation_type",models.CharField(blank=True,default="",max_length=16)),
            ("requested_end_date",models.DateField(blank=True,null=True)),("reason",models.CharField(blank=True,default="",max_length=2000)),("message",models.CharField(blank=True,default="",max_length=2000)),
            ("status",models.CharField(choices=[("received","received"),("awaiting_confirmation","awaiting_confirmation"),("needs_review","needs_review"),("accepted","accepted"),("scheduled","scheduled"),("effective","effective"),("rejected","rejected")],default="received",max_length=32)),
            ("effective_at",models.DateTimeField(blank=True,null=True)),("decision_note",models.TextField(blank=True,default="")),
            ("actor_label",models.CharField(blank=True,default="",max_length=200)),("decision_refund_cents",models.PositiveBigIntegerField(blank=True,null=True)),
            ("ack_email_status",models.CharField(choices=[("pending","pending"),("sent","sent"),("failed","failed")],default="pending",max_length=12)),("ack_attempts",models.PositiveIntegerField(default=0)),("ack_sent_at",models.DateTimeField(blank=True,null=True)),
            ("confirm_email_status",models.CharField(choices=[("pending","pending"),("sent","sent"),("failed","failed")],default="pending",max_length=12)),("confirm_attempts",models.PositiveIntegerField(default=0)),("confirm_sent_at",models.DateTimeField(blank=True,null=True)),
            ("confirm_token_hash",models.CharField(blank=True,default="",max_length=64)),("confirm_expires_at",models.DateTimeField(blank=True,null=True)),("confirmed_at",models.DateTimeField(blank=True,null=True)),
            ("contract",models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,related_name="declarations",to="dcm_billing.contract")),
            ("user",models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,related_name="billing_declarations",to=settings.AUTH_USER_MODEL)),
        ]),
        migrations.CreateModel(name="ContractPayment",fields=[
            ("id",models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name="ID")),("stripe_invoice_id",models.CharField(blank=True,default="",max_length=255)),
            ("stripe_payment_intent_id",models.CharField(blank=True,default="",max_length=255)),("stripe_charge_id",models.CharField(blank=True,default="",max_length=255)),
            ("amount_paid_cents",models.PositiveBigIntegerField()),("refunded_cents",models.PositiveBigIntegerField(default=0)),("currency",models.CharField(max_length=8)),
            ("paid_at",models.DateTimeField(default=timezone.now)),("billing_reason",models.CharField(choices=[("initial","initial"),("renewal","renewal"),("continuation","continuation")],default="initial",max_length=16)),
            ("contract",models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name="payments",to="dcm_billing.contract")),
        ]),
        migrations.CreateModel(name="Refund",fields=[
            ("id",models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name="ID")),("amount_cents",models.PositiveBigIntegerField()),("currency",models.CharField(max_length=8)),
            ("status",models.CharField(choices=[("pending","pending"),("requested","requested"),("succeeded","succeeded"),("failed","failed"),("requires_action","requires_action"),("cancelled","cancelled")],default="pending",max_length=20)),
            ("stripe_refund_id",models.CharField(blank=True,default="",max_length=255)),("idempotency_key",models.CharField(max_length=180,unique=True)),("due_by",models.DateTimeField()),
            ("attempts",models.PositiveIntegerField(default=0)),("last_error_code",models.CharField(blank=True,default="",max_length=120)),("created_at",models.DateTimeField(default=timezone.now)),("updated_at",models.DateTimeField(auto_now=True)),
            ("contract",models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name="refunds",to="dcm_billing.contract")),
            ("declaration",models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,related_name="refunds",to="dcm_billing.declaration")),
            ("payment",models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name="refunds",to="dcm_billing.contractpayment")),
        ]),
        migrations.AddConstraint(model_name="contractpayment",constraint=models.UniqueConstraint(condition=~Q(stripe_invoice_id=""),fields=("stripe_invoice_id",),name="dcm_bill_invoice_unique")),
        migrations.AddConstraint(model_name="refund",constraint=models.UniqueConstraint(condition=Q(status__in=("pending","requested","requires_action")),fields=("payment",),name="dcm_bill_refund_open_payment")),
        migrations.RunPython(backfill_contracts,migrations.RunPython.noop),
    ]
