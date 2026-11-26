"""App-facing object access and audited operator grants.

The app must authorize object existence and access before calling these functions.
Grant/revoke callers must supply their operator authorization decision.
"""
from django.db import transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone

from .config import load_billing_config
from .models import Contract, ObjectAccessGrant, ObjectBillingSubject, Purchase


def object_access_state(*, user, object_key, authorize):
    if not getattr(user, "is_authenticated", False) or not callable(authorize) or authorize(user, object_key) is not True:
        raise PermissionError("object_not_authorized")
    cfg = load_billing_config()
    subject = ObjectBillingSubject.objects.filter(app=cfg.app, object_key=object_key).first()
    sub = None
    if subject:
        subscriptions = subject.subscriptions.annotate(
            withdrawn_purchase=Exists(Purchase.objects.filter(
                stripe_object_id=OuterRef("stripe_subscription_id"), status="withdrawn")),
            withdrawn_contract=Exists(Contract.objects.filter(
                app=cfg.app, stripe_object_id=OuterRef("stripe_subscription_id"), status="withdrawn")),
        ).order_by("-current_period_end", "-pk")
        sub = subscriptions.filter(withdrawn_purchase=False, withdrawn_contract=False).first() or subscriptions.first()
    grant = subject and subject.grants.filter(revoked_at__isnull=True).exists()
    withdrawn = bool(sub and (sub.withdrawn_purchase or sub.withdrawn_contract))
    paid = bool(sub and not withdrawn and sub.current_period_end and sub.current_period_end > timezone.now())
    plan = next((p for p in cfg.plans if sub and p.key == sub.plan_key and p.kind == "object_subscription"), None)
    return {"source": "operator_grant" if grant else ("subscription" if paid else "none"),
            "plan_key": sub.plan_key if sub else None, "plan_name": plan.name if plan else None,
            "member_limit": None if grant else (plan.member_limit if paid and plan else None),
            "paid_member_limit": plan.member_limit if paid and plan else None,
            "billing_status": "withdrawn" if withdrawn else (sub.status if sub else None),
            "paid_through": sub.current_period_end if sub else None,
            "cancel_at_period_end": sub.cancel_at_period_end if sub else False,
            "payer": sub.payer if sub else None}


def set_object_grant(*, operator, object_key, reason, grant, authorize):
    if not getattr(operator, "is_authenticated", False) or not callable(authorize) or authorize(operator, object_key) is not True:
        raise PermissionError("operator_not_authorized")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("reason_required")
    if not isinstance(object_key, str) or not object_key or len(object_key) > 255:
        raise ValueError("invalid_object_key")
    cfg = load_billing_config()
    subject, _ = ObjectBillingSubject.objects.get_or_create(app=cfg.app, object_key=object_key)
    with transaction.atomic():
        subject = ObjectBillingSubject.objects.select_for_update().get(pk=subject.pk)
        row = subject.grants.filter(revoked_at__isnull=True).first()
        if grant and not row:
            row = ObjectAccessGrant.objects.create(subject=subject, granted_by=operator, reason=reason.strip())
        elif not grant and row:
            row.revoked_at = timezone.now()
            row.revoked_by = operator
            row.revocation_reason = reason.strip()
            row.save(update_fields=("revoked_at", "revoked_by", "revocation_reason"))
        return row
