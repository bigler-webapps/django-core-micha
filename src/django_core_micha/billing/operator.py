import json
import re
from collections import defaultdict
from functools import wraps
from datetime import datetime, timezone as utc_timezone

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, Sum, Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.cache import cache_control
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_http_methods

from django_core_micha.auth.permissions import can_view_users_admin
from .models import AICostRecord, FreeAccess, Purchase, Subscription, TeamPackage, TeamSeat, TokenEntry
from .services import access_state, models_q_expiry, token_balance


def _response(data, status=200):
    response=JsonResponse(data,status=status)
    response["Cache-Control"]="no-store"
    return response


def _guard(request):
    if not request.user.is_authenticated: return _response({"detail":"authentication_required"},401)
    if not can_view_users_admin(request.user,request=request): return _response({"detail":"forbidden"},403)
    return None


def _operator(*methods):
    """Operator guard FIRST (401/403 for every method), then the method check (405)."""
    def decorate(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            denied=_guard(request)
            if denied: return denied
            if request.method not in methods:
                response=_response({"detail":"method_not_allowed"},405); response["Allow"]=", ".join(methods); return response
            return view(request,*args,**kwargs)
        return wrapped
    return decorate


def _month(value):
    month=value or datetime.now(utc_timezone.utc).strftime("%Y-%m")
    if len(month)!=7 or not re.fullmatch(r"\d{4}-\d{2}",month): return None
    try:
        start=datetime.strptime(month,"%Y-%m").replace(tzinfo=utc_timezone.utc)
        return month,start,datetime(start.year+int(start.month==12),start.month%12+1,1,tzinfo=utc_timezone.utc)
    except (ValueError,OverflowError): return None  # includes 9999-12, whose next month does not exist


@_operator("GET")
def overview(request):
    parsed=_month(request.GET.get("month"))
    if not parsed: return _response({"detail":"invalid_month"},400)
    month,start,end=parsed; now=timezone.now(); app=getattr(settings,"DCM_BILLING_APP","")
    # Build current access winners in sets; preserve access_state's precedence and end-date rules.
    free_ids=set(FreeAccess.objects.filter(revoked_at__isnull=True).values_list("user_id",flat=True))
    candidates={}
    for sub in Subscription.objects.filter(status__in=("active","trialing","past_due"),current_period_end__gt=now).values("user_id","plan_key","current_period_end"):
        candidates.setdefault(sub["user_id"],[]).append((sub["current_period_end"],"subscription",sub["plan_key"]))
    seat_rows=TeamSeat.objects.filter(active=True,package__starts_at__lte=now,package__ends_at__gt=now).values("user_id","package__ends_at","package__plan_key")
    for seat in seat_rows: candidates.setdefault(seat["user_id"],[]).append((seat["package__ends_at"],"team",seat["package__plan_key"]))
    by_source={"subscription":defaultdict(int),"team_seat":0,"free_access":len(free_ids)}
    active=len(free_ids)
    for user_id,rows in candidates.items():
        if user_id in free_ids: continue
        end_at,source,plan=max(rows,key=lambda r:r[0]); active+=1
        if source=="subscription": by_source["subscription"][plan]+=1
        else: by_source["team_seat"]+=1
    by_source["subscription"]=dict(by_source["subscription"])
    purchases=Purchase.objects.filter(purchased_at__gte=start,purchased_at__lt=end)
    pcounts={r["kind"]:r["n"] for r in purchases.values("kind").annotate(n=Count("id"))}
    token_packages=purchases.filter(kind="token_package").aggregate(sold=Count("id"),tokens=Sum("tokens_granted"))
    team_sold=pcounts.get("team_package",0)
    subs_new=pcounts.get("subscription",0)
    subscriptions_ended=Subscription.objects.filter(ended_at__gte=start,ended_at__lt=end).count()
    packages=TeamPackage.objects.filter(starts_at__lte=now,ends_at__gt=now)
    team_data=packages.aggregate(packages_active=Count("id"),seats_total=Sum("seats"))
    seats_used=TeamSeat.objects.filter(package__in=packages,active=True).count()
    ledger=TokenEntry.objects.filter(created_at__gte=start,created_at__lt=end).filter(Q(app=app)|Q(app=""))  # grants and purchases carry no app value; entries tagged with another app are not ours
    token_totals={r["kind"]:r["value"] or 0 for r in ledger.values("kind").annotate(value=Sum("amount"))}
    expired=-(ledger.filter(kind="expiry").exclude(reference__startswith="withdrawal:").aggregate(v=Sum("amount"))["v"] or 0)
    ai=AICostRecord.objects.filter(app=app,created_at__gte=start,created_at__lt=end)
    ai_total=ai.aggregate(calls=Count("id"),cost=Sum("cost_usd_micros"))
    by_model=[{"provider":r["provider"],"model":r["model"],"calls":r["calls"],"usd_micros":r["cost"] or 0,"input_tokens":r["inp"] or 0,"output_tokens":r["out"] or 0,"cache_tokens":r["cache"] or 0} for r in ai.values("provider","model").annotate(calls=Count("id"),cost=Sum("cost_usd_micros"),inp=Sum("input_tokens"),out=Sum("output_tokens"),cache=Sum("cache_tokens")).order_by("-cost","provider","model")]
    used=-token_totals.get("debit",0); cost=ai_total["cost"] or 0
    per_token=(2*cost+used)//(2*used) if used else None
    return _response({"app":app,"month":month,"accounts":{"active":active,"by_source":by_source},"subscriptions":{"new":subs_new,"ended":subscriptions_ended},"team":{"packages_active":team_data["packages_active"] or 0,"packages_sold":team_sold,"seats_total":team_data["seats_total"] or 0,"seats_used":seats_used},"token_packages":{"sold":token_packages["sold"] or 0,"tokens":token_packages["tokens"] or 0},"tokens":{"granted":token_totals.get("grant",0)+token_totals.get("purchase",0),"used":used,"expired":expired},"ai_cost":{"calls":ai_total["calls"] or 0,"usd_micros":cost,"by_model":by_model,"usd_micros_per_used_token":per_token}})


def _state_data(state):
    return {"status":state.status,"source":state.source,"ends_at":state.ends_at.isoformat() if state.ends_at else None}


@_operator("GET")
def accounts(request):
    q=request.GET.get("q","")
    if len(q)>100: return _response({"detail":"invalid_query"},400)
    try:
        page=int(request.GET.get("page","1")); page_size=int(request.GET.get("page_size","25"))
        if page<1 or page>10000 or page_size<1 or page_size>100: raise ValueError
    except (TypeError,ValueError): return _response({"detail":"invalid_page"},400)
    User=get_user_model(); users=User.objects.all()
    if q: users=users.filter(Q(email__icontains=q)|Q(first_name__icontains=q)|Q(last_name__icontains=q)|Q(username__icontains=q))
    count=users.count(); rows=list(users.order_by("email","pk")[(page-1)*page_size:page*page_size]); ids=[u.pk for u in rows]
    if not ids: return _response({"count":count,"page":page,"page_size":page_size,"results":[]})
    now=timezone.now(); subs=defaultdict(list)
    for x in Subscription.objects.filter(user_id__in=ids): subs[x.user_id].append(x)
    seats=defaultdict(list)
    for x in TeamSeat.objects.filter(user_id__in=ids).select_related("package"): seats[x.user_id].append(x)
    free=defaultdict(list)
    for x in FreeAccess.objects.filter(user_id__in=ids).order_by("-granted_at","-pk"): free[x.user_id].append(x)
    balances=defaultdict(int)
    for x in TokenEntry.objects.filter(account__user_id__in=ids,kind__in=("grant","purchase","adjustment"),remaining__gt=0).filter(models_q_expiry(now)).values("account__user_id").annotate(total=Sum("remaining")): balances[x["account__user_id"]]=x["total"] or 0
    results=[]
    for user in rows:
        candidates=[]; ends=[]
        for x in subs[user.pk]:
            e=x.current_period_end
            if e and x.status in ("active","trialing","past_due") and e>now: candidates.append((e,"subscription",x.plan_key))
            elif x.ended_at: ends.append(x.ended_at)
            elif e and e<=now and x.status in ("active","trialing","past_due","canceled","unpaid","incomplete_expired"): ends.append(e)
        for x in seats[user.pk]:
            p=x.package
            if x.active and p.starts_at<=now<p.ends_at: candidates.append((p.ends_at,"team",p.plan_key))
            elif not x.active and x.removed_at: ends.append(x.removed_at)
            elif x.active and p.ends_at<=now: ends.append(p.ends_at)
            elif not x.active: ends.append(p.ends_at if p.ends_at<=now else now)
        open_free=next((x for x in free[user.pk] if x.revoked_at is None),None)
        revoked=max((x.revoked_at for x in free[user.pk] if x.revoked_at),default=None)
        if revoked: ends.append(revoked)
        if open_free: state={"status":"active","source":"free","ends_at":None}; plan=term=None
        elif candidates:
            term,source,plan=max(candidates,key=lambda r:r[0]); state={"status":"active","source":source,"ends_at":term.isoformat()}
        else: state={"status":"read_only","source":None,"ends_at":max(ends,default=None).isoformat() if ends else None}; plan=term=None
        results.append({"user_id":user.pk,"email":user.email,"name":user.get_full_name() or "","access":state,"plan_key":plan,"term_ends_at":term.isoformat() if term else None,"token_balance":balances[user.pk],"free_access":{"reason":open_free.reason,"granted_by":open_free.granted_by_label,"granted_at":open_free.granted_at.isoformat()} if open_free else None})
    return _response({"count":count,"page":page,"page_size":page_size,"results":results})


def _account_free(user):
    return FreeAccess.objects.filter(user=user,revoked_at__isnull=True).order_by("-granted_at","-pk").first()


def _switch_response(user, row, revoked=None, status=200):
    state=access_state(user)
    return _response({"user_id":user.pk,"free_access":{"reason":row.reason,"granted_by":row.granted_by_label,"granted_at":row.granted_at.isoformat()} if row else None,"access":_state_data(state),**({"revoked":revoked} if revoked is not None else {})},status)


@_operator("POST","DELETE")
@csrf_protect  # inside the guard: an anonymous request is a 401, not a CSRF 403
def free_access(request,user_id):
    User=get_user_model()
    try: user=User.objects.get(pk=user_id)
    except User.DoesNotExist: return _response({"detail":"user_not_found"},404)
    from django_core_micha.auditlog.audit_context import set_current_actor,reset_current_actor
    now=timezone.now(); actor_token=set_current_actor(request.user)
    try:
        with transaction.atomic():
            list(User.objects.select_for_update().filter(pk=user.pk).values_list("pk",flat=True))  # serialises concurrent switches for this person
            if request.method=="POST":
                try: data=json.loads(request.body or b"{}")
                except (ValueError,UnicodeDecodeError): data={}
                reason=data.get("reason") if isinstance(data,dict) else None
                if not isinstance(reason,str) or not reason.strip() or len(reason)>1000: return _response({"detail":"reason_required"},400)
                row=_account_free(user)
                if row: return _switch_response(user,row)
                label=(request.user.email or request.user.get_username())[:200]
                row=FreeAccess.objects.create(user=user,granted_by_label=label,reason=reason,granted_at=now)
                return _switch_response(user,row,status=201)
            open_rows=list(FreeAccess.objects.select_for_update().filter(user=user,revoked_at__isnull=True))
            for row in open_rows:
                row.revoked_at=now; row.save(update_fields=("revoked_at",))  # save(), not update(): the audit signals must fire
            return _switch_response(user,_account_free(user),revoked=bool(open_rows))
    finally: reset_current_actor(actor_token)
