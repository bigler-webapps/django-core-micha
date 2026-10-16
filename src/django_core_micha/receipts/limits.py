"""Optional per-user calendar-day call count."""
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from .errors import ReceiptError

def check_and_increment(user):
    limit=getattr(settings,"DCM_RECEIPTS_DAILY_LIMIT",None)
    if limit is None:return
    key=f"dcm_receipts_count:{user.pk}:{timezone.localdate().isoformat()}"
    try:
        cache.add(key,0,timeout=60*60*30)
        count=cache.incr(key)  # atomic: concurrent readings cannot both pass the last slot
    except Exception as exc:raise ReceiptError("provider_unavailable") from exc
    if count>int(limit):raise ReceiptError("count_limit_reached")

