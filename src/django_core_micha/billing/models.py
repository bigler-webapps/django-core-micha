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


def _reference():
    import secrets
    return secrets.token_urlsafe(9)


class Contract(models.Model):
    KINDS=[(x,x) for x in ("subscription","team_package","token_package")]
    POLICIES=[(x,x) for x in ("full","prorated")]
    CONSUMER_STATUSES=[(x,x) for x in ("consumer","business","unknown")]
    PHASES=[(x,x) for x in ("initial","continuation","none")]
    STATUSES=[(x,x) for x in ("active","cancellation_scheduled","ended","withdrawn")]
    SCHEDULE_STATUSES=[(x,x) for x in ("not_needed","pending","created","failed","released","cancelled")]
    user=models.ForeignKey(User,on_delete=models.CASCADE,related_name="billing_contracts")
    app=models.CharField(max_length=80,db_index=True)
    kind=models.CharField(max_length=24,choices=KINDS)
    plan_key=models.CharField(max_length=40)
    purchase=models.OneToOneField(Purchase,null=True,blank=True,on_delete=models.SET_NULL,related_name="contract")
    stripe_object_id=models.CharField(max_length=255,db_index=True)
    reference=models.CharField(max_length=32,unique=True,default=_reference)
    terms_version=models.CharField(max_length=80,blank=True,default="")
    seller=models.CharField(max_length=255,blank=True,default="")
    withdrawal_policy=models.CharField(max_length=16,choices=POLICIES,default="prorated")
    consumer_status=models.CharField(max_length=16,choices=CONSUMER_STATUSES,default="unknown")
    billing_country=models.CharField(max_length=2,blank=True,default="")
    started_at=models.DateTimeField(default=timezone.now)
    info_delivered_at=models.DateTimeField(null=True,blank=True)
    initial_term_end=models.DateTimeField(null=True,blank=True)
    continuation_plan_key=models.CharField(max_length=40,blank=True,default="")
    continuation_stripe_price_id=models.CharField(max_length=255,blank=True,default="")
    continuation_amount_cents=models.PositiveBigIntegerField(null=True,blank=True)
    continuation_token_allowance=models.PositiveBigIntegerField(null=True,blank=True)
    continuation_interval=models.CharField(max_length=16,blank=True,default="")
    transition_guard_days=models.PositiveSmallIntegerField(default=7)
    phase=models.CharField(max_length=16,choices=PHASES,default="none")
    status=models.CharField(max_length=32,choices=STATUSES,default="active")
    cancel_effective_at=models.DateTimeField(null=True,blank=True)
    stripe_schedule_id=models.CharField(max_length=255,blank=True,default="")
    schedule_status=models.CharField(max_length=16,choices=SCHEDULE_STATUSES,default="not_needed")
    needs_review=models.BooleanField(default=False)
    review_reason=models.CharField(max_length=120,blank=True,default="")
    created_at=models.DateTimeField(default=timezone.now)
    updated_at=models.DateTimeField(auto_now=True)


class ContractPayment(models.Model):
    REASONS=[(x,x) for x in ("initial","renewal","continuation")]
    contract=models.ForeignKey(Contract,on_delete=models.CASCADE,related_name="payments")
    stripe_invoice_id=models.CharField(max_length=255,blank=True,default="")
    stripe_payment_intent_id=models.CharField(max_length=255,blank=True,default="")
    stripe_charge_id=models.CharField(max_length=255,blank=True,default="")
    amount_paid_cents=models.PositiveBigIntegerField()
    refunded_cents=models.PositiveBigIntegerField(default=0)
    currency=models.CharField(max_length=8)
    paid_at=models.DateTimeField(default=timezone.now)
    billing_reason=models.CharField(max_length=16,choices=REASONS,default="initial")
    class Meta:
        constraints=[models.UniqueConstraint(fields=("stripe_invoice_id",),condition=~Q(stripe_invoice_id=""),name="dcm_bill_invoice_unique")]


class Declaration(models.Model):
    KINDS=[(x,x) for x in ("cancellation","withdrawal")]
    CHANNELS=[(x,x) for x in ("public","account","operator")]
    STATUSES=[(x,x) for x in ("received","awaiting_confirmation","needs_review","accepted","scheduled","effective","rejected")]
    ACK=[(x,x) for x in ("pending","sent","failed")]
    reference=models.CharField(max_length=32,unique=True,default=_reference)
    app=models.CharField(max_length=80,db_index=True)
    kind=models.CharField(max_length=16,choices=KINDS)
    channel=models.CharField(max_length=16,choices=CHANNELS)
    received_at=models.DateTimeField(default=timezone.now,editable=False)
    name=models.CharField(max_length=200,blank=True,default="")
    email=models.EmailField(max_length=254,blank=True,default="")
    contract_hint=models.CharField(max_length=200,blank=True,default="")
    cancellation_type=models.CharField(max_length=16,blank=True,default="")
    requested_end_date=models.DateField(null=True,blank=True)
    reason=models.CharField(max_length=2000,blank=True,default="")
    message=models.CharField(max_length=2000,blank=True,default="")
    user=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL,related_name="billing_declarations")
    contract=models.ForeignKey(Contract,null=True,blank=True,on_delete=models.SET_NULL,related_name="declarations")
    status=models.CharField(max_length=32,choices=STATUSES,default="received")
    effective_at=models.DateTimeField(null=True,blank=True)
    decision_note=models.TextField(blank=True,default="")
    actor_label=models.CharField(max_length=200,blank=True,default="")
    decision_refund_cents=models.PositiveBigIntegerField(null=True,blank=True)
    ack_email_status=models.CharField(max_length=12,choices=ACK,default="pending")
    ack_attempts=models.PositiveIntegerField(default=0)
    ack_sent_at=models.DateTimeField(null=True,blank=True)
    confirm_email_status=models.CharField(max_length=12,choices=ACK,default="pending")
    confirm_attempts=models.PositiveIntegerField(default=0)
    confirm_sent_at=models.DateTimeField(null=True,blank=True)
    confirm_token_hash=models.CharField(max_length=64,blank=True,default="")
    confirm_expires_at=models.DateTimeField(null=True,blank=True)
    confirmed_at=models.DateTimeField(null=True,blank=True)

    def save(self,*args,**kwargs):
        if self.pk:
            original=type(self).objects.filter(pk=self.pk).values_list("received_at",flat=True).first()
            if original is not None: self.received_at=original
        super().save(*args,**kwargs)


class Refund(models.Model):
    STATUSES=[(x,x) for x in ("pending","requested","succeeded","failed","requires_action","cancelled")]
    contract=models.ForeignKey(Contract,on_delete=models.CASCADE,related_name="refunds")
    payment=models.ForeignKey(ContractPayment,on_delete=models.CASCADE,related_name="refunds")
    declaration=models.ForeignKey(Declaration,null=True,blank=True,on_delete=models.SET_NULL,related_name="refunds")
    amount_cents=models.PositiveBigIntegerField()
    currency=models.CharField(max_length=8)
    status=models.CharField(max_length=20,choices=STATUSES,default="pending")
    stripe_refund_id=models.CharField(max_length=255,blank=True,default="")
    idempotency_key=models.CharField(max_length=180,unique=True)
    due_by=models.DateTimeField()
    attempts=models.PositiveIntegerField(default=0)
    last_error_code=models.CharField(max_length=120,blank=True,default="")
    created_at=models.DateTimeField(default=timezone.now)
    updated_at=models.DateTimeField(auto_now=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=("payment",),condition=Q(status__in=("pending","requested","requires_action")),name="dcm_bill_refund_open_payment")]
