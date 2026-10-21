from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

User = settings.AUTH_USER_MODEL
class BillingCustomer(models.Model):
    user=models.OneToOneField(User,on_delete=models.CASCADE,related_name="billing_customer")
    stripe_customer_id=models.CharField(max_length=255,unique=True)
class StripePrice(models.Model):
    app=models.CharField(max_length=80); plan_key=models.CharField(max_length=40); version=models.PositiveIntegerField()
    lookup_key=models.CharField(max_length=180,unique=True); fingerprint=models.CharField(max_length=64)
    stripe_product_id=models.CharField(max_length=255); stripe_price_id=models.CharField(max_length=255)
class Subscription(models.Model):
    user=models.ForeignKey(User,on_delete=models.CASCADE,related_name="billing_subscriptions")
    plan_key=models.CharField(max_length=40); stripe_subscription_id=models.CharField(max_length=255,unique=True)
    status=models.CharField(max_length=32); current_period_start=models.DateTimeField(null=True,blank=True); current_period_end=models.DateTimeField(null=True,blank=True)
    cancel_at_period_end=models.BooleanField(default=False); ended_at=models.DateTimeField(null=True,blank=True)
class TeamPackage(models.Model):
    owner=models.ForeignKey(User,on_delete=models.CASCADE,related_name="billing_team_packages"); plan_key=models.CharField(max_length=40)
    seats=models.PositiveIntegerField(); starts_at=models.DateTimeField(); ends_at=models.DateTimeField(); stripe_ref=models.CharField(max_length=255,unique=True)
class TeamSeat(models.Model):
    package=models.ForeignKey(TeamPackage,on_delete=models.CASCADE,related_name="team_seats"); user=models.ForeignKey(User,on_delete=models.CASCADE,related_name="billing_team_seats")
    active=models.BooleanField(default=True); created_at=models.DateTimeField(default=timezone.now); removed_at=models.DateTimeField(null=True,blank=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=("package","user"),condition=Q(active=True),name="dcm_bill_active_seat_unique")]
class FreeAccess(models.Model):
    user=models.ForeignKey(User,on_delete=models.CASCADE,related_name="billing_free_access"); granted_by_label=models.CharField(max_length=200); reason=models.TextField()
    granted_at=models.DateTimeField(default=timezone.now); revoked_at=models.DateTimeField(null=True,blank=True)
class TokenAccount(models.Model):
    user=models.ForeignKey(User,null=True,blank=True,on_delete=models.CASCADE,related_name="billing_token_accounts")
    team_package=models.ForeignKey(TeamPackage,null=True,blank=True,on_delete=models.CASCADE,related_name="billing_token_accounts")
    class Meta:
        constraints=[models.CheckConstraint(condition=(Q(user__isnull=False,team_package__isnull=True)|Q(user__isnull=True,team_package__isnull=False)),name="dcm_bill_account_one_owner"),models.UniqueConstraint(fields=("user",),condition=Q(user__isnull=False),name="dcm_bill_account_user_unique"),models.UniqueConstraint(fields=("team_package",),condition=Q(team_package__isnull=False),name="dcm_bill_account_team_unique")]
class TokenEntry(models.Model):
    KINDS=[(x,x) for x in ("grant","purchase","debit","expiry","adjustment")]
    account=models.ForeignKey(TokenAccount,on_delete=models.CASCADE,related_name="entries"); kind=models.CharField(max_length=16,choices=KINDS); amount=models.BigIntegerField()
    expires_at=models.DateTimeField(null=True,blank=True); remaining=models.BigIntegerField(null=True,blank=True); purpose=models.CharField(max_length=120,blank=True); reference=models.CharField(max_length=255,blank=True); app=models.CharField(max_length=80,blank=True,default=""); created_at=models.DateTimeField(default=timezone.now)
    class Meta:
        constraints=[models.UniqueConstraint(fields=("account","kind","reference"),condition=~Q(reference=""),name="dcm_bill_ledger_ref_unique")]
class StripeEvent(models.Model):
    event_id=models.CharField(max_length=255,unique=True); type=models.CharField(max_length=120); status=models.CharField(max_length=24); received_at=models.DateTimeField(default=timezone.now); processed_at=models.DateTimeField(null=True,blank=True); error_text=models.CharField(max_length=120,blank=True)
class AICostRecord(models.Model):
    provider=models.CharField(max_length=80); model=models.CharField(max_length=120); user=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)
    input_tokens=models.PositiveBigIntegerField(default=0); output_tokens=models.PositiveBigIntegerField(default=0); cache_tokens=models.PositiveBigIntegerField(default=0); cost_usd_micros=models.PositiveBigIntegerField()
    purpose=models.CharField(max_length=120); reference=models.CharField(max_length=255,blank=True); app=models.CharField(max_length=80,blank=True,default="",db_index=True); created_at=models.DateTimeField(default=timezone.now)

class Purchase(models.Model):
    KINDS=[(x,x) for x in ("subscription","team_package","token_package")]
    STATUSES=[(x,x) for x in ("active","withdrawn")]
    user=models.ForeignKey(User,on_delete=models.CASCADE,related_name="billing_purchases")
    kind=models.CharField(max_length=24,choices=KINDS); plan_key=models.CharField(max_length=40)
    stripe_object_id=models.CharField(max_length=255,unique=True); stripe_payment_intent_id=models.CharField(max_length=255,blank=True)
    stripe_invoice_id=models.CharField(max_length=255,blank=True); amount_paid_cents=models.PositiveBigIntegerField(); currency=models.CharField(max_length=8)
    billing_country=models.CharField(max_length=2,blank=True); has_business_tax_id=models.BooleanField(default=False)
    purchased_at=models.DateTimeField(); term_start=models.DateTimeField(null=True,blank=True); term_end=models.DateTimeField(null=True,blank=True)
    grant_references=models.JSONField(default=list); tokens_granted=models.PositiveBigIntegerField(default=0)
    status=models.CharField(max_length=16,choices=STATUSES,default="active"); withdrawn_at=models.DateTimeField(null=True,blank=True)
    refund_cents=models.PositiveBigIntegerField(null=True,blank=True); stripe_refund_id=models.CharField(max_length=255,blank=True)
    withdrawal_note=models.TextField(blank=True); withdrawal_email_sent=models.BooleanField(default=False)

class CheckoutAttempt(models.Model):
    STATUSES=[(x,x) for x in ("open","processing","paid","failed","expired")]
    reference=models.CharField(max_length=64,unique=True)
    user=models.ForeignKey(User,on_delete=models.CASCADE,related_name="billing_checkout_attempts")
    app=models.CharField(max_length=80); plan_key=models.CharField(max_length=40); context=models.CharField(max_length=40)
    idempotency_key=models.CharField(max_length=64); status=models.CharField(max_length=16,choices=STATUSES,default="open")
    stripe_price_id=models.CharField(max_length=255); stripe_session_id=models.CharField(max_length=255,blank=True)
    stripe_checkout_url=models.TextField(blank=True); expires_at=models.DateTimeField(null=True)
    created_at=models.DateTimeField(default=timezone.now); updated_at=models.DateTimeField(auto_now=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=("user","app","idempotency_key"),name="dcm_bill_attempt_key_unique"),
                     models.UniqueConstraint(fields=("stripe_session_id",),condition=~Q(stripe_session_id=""),name="dcm_bill_attempt_session_unique")]
