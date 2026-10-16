"""Standing consent and idempotent import for receipt recognition."""
from django.db import transaction
from django.utils import timezone
from .models import ReceiptConsent

def has_consent(user):
    if not getattr(user,"is_authenticated",False):return False
    c=ReceiptConsent.objects.filter(user=user).first()
    return bool(c and c.granted_at and (not c.revoked_at or c.revoked_at<c.granted_at))

def grant(user,source="user"):
    with transaction.atomic():
        c=ReceiptConsent.objects.select_for_update().filter(user=user).first()
        now=timezone.now()
        if c is None:c=ReceiptConsent(user=user,granted_at=now,source=source)
        else:c.granted_at=now;c.revoked_at=None;c.source=source
        c.save();return c

def revoke(user):
    with transaction.atomic():
        c=ReceiptConsent.objects.select_for_update().filter(user=user).first()
        if c is None:c=ReceiptConsent(user=user)
        c.revoked_at=timezone.now();c.save();return c

def state(user):
    c=ReceiptConsent.objects.filter(user=user).first();active=bool(c and c.granted_at and (not c.revoked_at or c.revoked_at<c.granted_at))
    return {"granted":active,"granted_at":c.granted_at.isoformat() if c and c.granted_at else None,"revoked_at":c.revoked_at.isoformat() if c and c.revoked_at else None}

def import_consents(pairs,*,source="import"):
    counts={"created":0,"skipped":0}
    for user,at in pairs:
        with transaction.atomic():
            c=ReceiptConsent.objects.select_for_update().filter(user=user).first()
            if c is None:ReceiptConsent.objects.create(user=user,granted_at=at,source=source);counts["created"]+=1
            elif c.revoked_at and c.revoked_at>=at:counts["skipped"]+=1
            elif c.granted_at and c.granted_at>=at:counts["skipped"]+=1
            else:c.granted_at=at;c.source=source;c.save(update_fields=["granted_at","source","updated_at"]);counts["skipped"]+=1
    return counts

