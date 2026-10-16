import hashlib, hmac, re
from datetime import datetime, timezone as dt_timezone
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Sum, Count
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from .config import load_billing_config
from .models import TeamPackage, TeamSeat, AICostRecord, TokenEntry, Purchase
from .services import access_state, token_balance
from .models import BillingCustomer, StripeEvent
from .stripe_service import checkout_session_params, get_customer, process_stripe_event
from .withdrawal import eligibility, refund_amount, withdraw as execute_withdrawal

def _plans():
    result=[]
    for p in load_billing_config().plans:
        row={"key":p.key,"kind":p.kind,"name":p.name,"amount_cents":p.amount_cents,"token_allowance":p.token_allowance}
        for k in ("interval","term_months","seats","tokens"):
            v=getattr(p,k)
            if v is not None: row[k]=v
        result.append(row)
    return result
@require_GET
def me(request):
    if not request.user.is_authenticated: return JsonResponse({"detail":"authentication_required"},status=401)
    s=access_state(request.user)
    return JsonResponse({"access":{"status":s.status,"source":s.source,"ends_at":s.ends_at.isoformat() if s.ends_at else None},"tokens":{"balance":token_balance(request.user)},"plans":_plans()})
def _target(data):
    value=data.get("user_id") or data.get("email")
    if not value: raise ValueError
    lookup={"pk":value} if data.get("user_id") else {"email__iexact":value}
    return get_user_model().objects.get(**lookup)
@csrf_protect  # explicit: a session-authenticated mutation must not depend on the host app's middleware stack
@require_http_methods(["GET","POST","DELETE"])
def team_seats(request):
    if not request.user.is_authenticated: return JsonResponse({},status=401)
    pkg=TeamPackage.objects.filter(owner=request.user,starts_at__lte=timezone.now(),ends_at__gt=timezone.now()).order_by("ends_at").first()
    if not pkg: return JsonResponse({"detail":"no_active_team"},status=404)
    if request.method=="GET": return JsonResponse({"seats":[{"user_id":s.user_id} for s in pkg.team_seats.filter(active=True)]})
    import json
    try: target=_target(json.loads(request.body or b"{}"))
    except Exception: return JsonResponse({"detail":"invalid_user"},status=400)
    if request.method=="POST":
        with transaction.atomic():
            pkg=TeamPackage.objects.select_for_update().get(pk=pkg.pk)
            if pkg.team_seats.filter(active=True).count()>=pkg.seats and not pkg.team_seats.filter(user=target,active=True).exists(): return JsonResponse({"detail":"seat_limit"},status=409)
            TeamSeat.objects.get_or_create(package=pkg,user=target,active=True)
        return JsonResponse({"user_id":target.pk},status=201)
    updated=pkg.team_seats.filter(user=target,active=True).update(active=False,removed_at=timezone.now())
    return JsonResponse({"removed":bool(updated)})
@require_GET
def cost_report(request):
    tokens=request.headers.get("Authorization","")
    if not tokens.startswith("Bearer "): return JsonResponse({},status=401)
    digest=hashlib.sha256(tokens[7:].encode()).hexdigest()
    allowed=getattr(settings,"DCM_BILLING_REPORT_TOKEN_SHA256",[])
    matches=[hmac.compare_digest(digest,x) for x in allowed]  # no short circuit: the time does not depend on the position of the match
    if not any(matches): return JsonResponse({},status=401)
    month=request.GET.get("month") or datetime.now(dt_timezone.utc).strftime("%Y-%m")
    if not re.fullmatch(r"\d{4}-\d{2}",month): return JsonResponse({"detail":"invalid_month"},status=400)
    try: start=datetime.strptime(month,"%Y-%m").replace(tzinfo=dt_timezone.utc); end=datetime(start.year+int(start.month==12),start.month%12+1,1,tzinfo=dt_timezone.utc)
    except ValueError: return JsonResponse({"detail":"invalid_month"},status=400)
    app=getattr(settings,"DCM_BILLING_APP","")
    rows=AICostRecord.objects.filter(app=app,created_at__gte=start,created_at__lt=end)
    debit=TokenEntry.objects.filter(kind="debit",app=app,created_at__gte=start,created_at__lt=end).aggregate(v=Sum("amount"))["v"] or 0
    agg=rows.aggregate(calls=Count("id"),cost=Sum("cost_usd_micros"),inp=Sum("input_tokens"),out=Sum("output_tokens"),cache=Sum("cache_tokens"))
    by=[{"provider":r["provider"],"model":r["model"],"calls":r["calls"],"cost_usd_micros":r["cost"] or 0} for r in rows.values("provider","model").annotate(calls=Count("id"),cost=Sum("cost_usd_micros")).order_by("provider","model")]
    return JsonResponse({"app":getattr(settings,"DCM_BILLING_APP",""),"month":month,"calls":agg["calls"] or 0,"ai_cost_usd_micros":agg["cost"] or 0,"input_tokens":agg["inp"] or 0,"output_tokens":agg["out"] or 0,"cache_tokens":agg["cache"] or 0,"tokens_debited":-debit,"by_model":by})

@csrf_protect
@require_http_methods(["POST"])
def checkout(request):
    if not request.user.is_authenticated:
        return JsonResponse({"detail":"authentication_required"},status=401)
    import json
    try:
        data=json.loads(request.body or b"{}")
        key=data.get("plan_key") if isinstance(data,dict) else None
        plan=next(p for p in load_billing_config().plans if p.key==key)
        from .stripe_service import _latest_price, _stripe
        price=_latest_price(plan.key)
        customer=get_customer(request.user)
        session=_stripe().checkout.Session.create(**checkout_session_params(plan=plan,price=price,user=request.user,customer_id=customer.stripe_customer_id))
    except (StopIteration,LookupError):
        return JsonResponse({"detail":"unknown_plan"},status=400)
    except Exception:
        return JsonResponse({"detail":"checkout_unavailable"},status=502)
    return JsonResponse({"url":session.url})

@csrf_protect
@require_http_methods(["POST"])
def portal(request):
    if not request.user.is_authenticated:
        return JsonResponse({"detail":"authentication_required"},status=401)
    customer=BillingCustomer.objects.filter(user=request.user).first()
    if not customer:
        return JsonResponse({"detail":"no_customer"},status=404)
    try:
        from .stripe_service import _stripe
        session=_stripe().billing_portal.Session.create(customer=customer.stripe_customer_id,
            return_url=getattr(settings,"DCM_BILLING_PORTAL_RETURN_URL",""))
    except Exception:
        return JsonResponse({"detail":"portal_unavailable"},status=502)
    return JsonResponse({"url":session.url})

@csrf_protect
@require_GET
def withdrawal_list(request):
    if not request.user.is_authenticated: return JsonResponse({"detail":"authentication_required"},status=401)
    now=timezone.now()
    rows=[]
    for p in Purchase.objects.filter(user=request.user).order_by("-purchased_at"):
        result=eligibility(p,now)
        rows.append({"id":p.pk,"kind":p.kind,"plan_key":p.plan_key,"purchased_at":p.purchased_at.isoformat(),"amount_paid_cents":p.amount_paid_cents,"currency":p.currency,"eligible":result.eligible,"reason":result.reason,"withdrawn_at":p.withdrawn_at.isoformat() if p.withdrawn_at else None})
    return JsonResponse({"purchases":rows})

def _purchase_or_404(request, purchase_id):
    from django.shortcuts import get_object_or_404
    return get_object_or_404(Purchase,user=request.user,pk=purchase_id)

@csrf_protect
@require_GET
def withdrawal_preview(request,purchase_id):
    if not request.user.is_authenticated: return JsonResponse({"detail":"authentication_required"},status=401)
    p=_purchase_or_404(request,purchase_id); now=timezone.now(); eligible=eligibility(p,now); calc=refund_amount(p,now)
    line=f"Paid {p.amount_paid_cents} {p.currency}; time share {calc.time_share_cents}; token share {calc.token_share_cents}; amount used {calc.used_cents}; refund {calc.refund_cents} {p.currency}."
    return JsonResponse({"eligible":eligible.eligible,"reason":eligible.reason,"amount_paid_cents":p.amount_paid_cents,"currency":p.currency,"time_share_cents":calc.time_share_cents,"token_share_cents":calc.token_share_cents,"used_cents":calc.used_cents,"refund_cents":calc.refund_cents,"calculation":line})

@csrf_protect
@require_http_methods(["POST"])
def withdrawal_execute(request,purchase_id):
    if not request.user.is_authenticated: return JsonResponse({"detail":"authentication_required"},status=401)
    import json
    p=_purchase_or_404(request,purchase_id)
    if p.status=="withdrawn":
        return JsonResponse({"already_withdrawn":True,"withdrawn_at":p.withdrawn_at.isoformat() if p.withdrawn_at else None,"refund_cents":p.refund_cents or 0,"currency":p.currency,"calculation":p.withdrawal_note})
    try: data=json.loads(request.body or b"{}")
    except (ValueError,UnicodeDecodeError): return JsonResponse({"detail":"invalid_json"},status=400)
    if not isinstance(data,dict) or data.get("confirm") is not True: return JsonResponse({"detail":"confirmation_required"},status=400)
    try: calc,already=execute_withdrawal(p)
    except ValueError as exc: return JsonResponse({"code":str(exc)},status=409)
    except Exception: return JsonResponse({"code":"withdrawal_unavailable"},status=502)
    return JsonResponse({"already_withdrawn":already,"refund_cents":calc.refund_cents,"currency":p.currency,"withdrawn_at":timezone.now().isoformat(),"calculation":p.withdrawal_note})

from django.http import HttpResponse

@csrf_exempt
@require_http_methods(["POST"])
def webhook(request):
    try:
        from .stripe_service import _stripe
        stripe=_stripe()
        event=stripe.Webhook.construct_event(request.body,request.headers.get("Stripe-Signature",""),
            getattr(settings,"STRIPE_WEBHOOK_SECRET",""))
    except Exception:
        return HttpResponse(status=400)
    event_id=getattr(event,"id",None) or (event.get("id") if isinstance(event,dict) else None)
    event_type=getattr(event,"type",None) or (event.get("type") if isinstance(event,dict) else "unknown")
    if not event_id:
        return HttpResponse(status=400)
    try:
        with transaction.atomic():
            row,created=StripeEvent.objects.get_or_create(event_id=event_id,defaults={"type":event_type,"status":"received"})
    except Exception:
        return HttpResponse(status=500)
    if not created and row.status in ("processed", "ignored"):
        return HttpResponse(status=200)
    try:
        with transaction.atomic():
            locked=StripeEvent.objects.select_for_update().get(pk=row.pk)
            if locked.status in ("processed", "ignored"):
                return HttpResponse(status=200)
            status=process_stripe_event(event)
            StripeEvent.objects.filter(pk=locked.pk).update(status=status,processed_at=timezone.now(),error_text="")
    except Exception as exc:
        # Persist only the exception class; never serialize a provider payload or error message.
        StripeEvent.objects.filter(pk=row.pk).update(status="failed",error_text=type(exc).__name__)
        return HttpResponse(status=500)
    return HttpResponse(status=200)
