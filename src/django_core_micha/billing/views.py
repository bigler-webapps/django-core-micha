import hashlib, hmac, json, re
import secrets
from datetime import datetime, timezone as dt_timezone
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Sum, Count, F
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods
from django.views.decorators.csrf import csrf_exempt, csrf_protect, ensure_csrf_cookie
from .config import load_billing_config
from .models import TeamPackage, TeamSeat, AICostRecord, TokenEntry, Purchase, CheckoutAttempt, Contract, ContractPayment, Declaration, Refund
from .services import access_state, token_balance, models_q_expiry
from .models import BillingCustomer, StripeEvent
from .stripe_service import checkout_session_params, get_customer, process_stripe_event
from .withdrawal import eligibility, refund_amount, withdraw as execute_withdrawal

def _plans():
    config=load_billing_config()
    by_key={p.key:p for p in config.plans}
    result=[]
    for p in config.plans:
        if p.kind == "continuation" or not p.available: continue
        row={"key":p.key,"kind":p.kind,"name":p.name,"amount_cents":p.amount_cents,"token_allowance":p.token_allowance}
        for k in ("interval","term_months","seats","tokens"):
            v=getattr(p,k)
            if v is not None: row[k]=v
        follow=by_key.get(getattr(p,"continuation",None) or "")
        if follow is not None:
            row["continuation"]={"amount_cents":follow.amount_cents,"interval":follow.interval,"token_allowance":follow.token_allowance}
        result.append(row)
    return result

def _plan_names():
    """Names of every plan an account can hold, including plans no longer offered (`available: false`)."""
    names={p["key"]:p["name"] for p in _plans()}
    try: names.update({p.key:p.name for p in load_billing_config().plans if p.key not in names})
    except FileNotFoundError: pass
    return names

def _grant_origin(entry):
    if entry.kind == "purchase": return "package"
    if entry.purpose.startswith("plan:"): return "plan"
    if entry.purpose == "trial": return "trial"
    return "credit"

@require_GET
def me(request):
    if not request.user.is_authenticated: return JsonResponse({"detail":"authentication_required"},status=401)
    s=access_state(request.user)
    from .models import Subscription, TokenAccount
    now=timezone.now(); plans=_plan_names()
    subs=list(Subscription.objects.filter(user=request.user).order_by("-current_period_end","-pk"))
    chosen=next((x for x in subs if x.current_period_end and x.status in ("active","trialing","past_due") and x.current_period_end>now),None)
    chosen=chosen or next((x for x in subs if not x.ended_at),None)
    subscription=None if chosen is None else {"plan_key":chosen.plan_key,"plan_name":plans.get(chosen.plan_key,""),"status":chosen.status,"current_period_start":chosen.current_period_start.isoformat() if chosen.current_period_start else None,"current_period_end":chosen.current_period_end.isoformat() if chosen.current_period_end else None,"renews":bool(chosen.status in ("active","trialing") and not chosen.cancel_at_period_end and chosen.current_period_end and chosen.current_period_end>now),"cancel_at_period_end":chosen.cancel_at_period_end}
    account=TokenAccount.objects.filter(user=request.user).first()
    grants=[]
    if account:
        entries=account.entries.filter(kind__in=("grant","purchase"),remaining__gt=0).filter(models_q_expiry(now)).order_by(F("expires_at").asc(nulls_last=True),"created_at","pk")
        grants=[{"source":"plan" if e.kind=="grant" else "package","origin":_grant_origin(e),"amount":e.amount,"remaining":e.remaining,"expires_at":e.expires_at.isoformat() if e.expires_at else None} for e in entries]
    from .models import TeamPackage, TeamSeat
    owned=TeamPackage.objects.filter(owner=request.user,starts_at__lte=now,ends_at__gt=now).order_by("ends_at","pk").first()
    held=TeamSeat.objects.filter(user=request.user,active=True,package__starts_at__lte=now,package__ends_at__gt=now).select_related("package__owner").order_by("package__ends_at","pk").first()
    pool_package=owned or (held.package if held else None)
    pool=None
    if pool_package:
        pool_rows=list(TokenEntry.objects.filter(account__team_package=pool_package,kind__in=("grant","purchase")).filter(models_q_expiry(now)).values_list("amount","remaining"))
        if pool_rows:
            pool={"plan_key":pool_package.plan_key,"plan_name":plans.get(pool_package.plan_key,""),"amount":sum(amount for amount,_ in pool_rows),"remaining":sum(max(0,remaining or 0) for _,remaining in pool_rows),"expires_at":pool_package.ends_at.isoformat()}
    team=None
    if owned:
        holders=list(TeamSeat.objects.filter(package=owned,active=True).select_related("user").order_by("user__email","user_id"))
        team={"plan_key":owned.plan_key,"plan_name":plans.get(owned.plan_key,""),"ends_at":owned.ends_at.isoformat(),"seats_total":owned.seats,"seats_used":len(holders),"seat_holders":[{"user_id":x.user_id,"name":x.user.get_full_name() or "","email":x.user.email} for x in holders]}
    from .contracts import contract_summary
    chosen_contract=Contract.objects.filter(user=request.user,app=getattr(settings,"DCM_BILLING_APP",""),stripe_object_id=chosen.stripe_subscription_id).first() if chosen else None
    result={"access":{"status":s.status,"source":s.source,"ends_at":s.ends_at.isoformat() if s.ends_at else None},"tokens":{"balance":token_balance(request.user),"grants":grants,"pool":pool},"plans":_plans(),"subscription":subscription,"team":team}
    if _contract_config_enabled(): result["contract"]=contract_summary(chosen_contract,now) if chosen_contract else None
    if held:
        owner=held.package.owner
        payer_name=owner.get_full_name().strip() or owner.email or owner.username  # never the database id
        result["seat"]={"plan_key":held.package.plan_key,"plan_name":plans.get(held.package.plan_key,""),"ends_at":held.package.ends_at.isoformat(),"payer_name":payer_name}
    response=JsonResponse(result); response["Cache-Control"]="no-store"  # it now carries the seat holders' names and emails
    return response
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
    try: data=json.loads(request.body or b"{}")
    except (ValueError,UnicodeDecodeError): data=None
    if isinstance(data,dict) and data.get("context") is not None:
        return _contextual_checkout(request,data)
    try:
        if data is None: data=json.loads(request.body or b"{}")
        key=data.get("plan_key") if isinstance(data,dict) else None
        plan=next(p for p in load_billing_config().plans if p.key==key)
        if plan.kind == "continuation" or not plan.available: raise StopIteration
        from .stripe_service import _latest_price, _stripe
        price=_latest_price(plan.key)
        customer=get_customer(request.user)
        session=_stripe().checkout.Session.create(**checkout_session_params(plan=plan,price=price,user=request.user,customer_id=customer.stripe_customer_id))
    except (StopIteration,LookupError): return JsonResponse({"detail":"unknown_plan"},status=400)
    except Exception: return JsonResponse({"detail":"checkout_unavailable"},status=502)
    return JsonResponse(_checkout_body(session.url))

def _attempt_data(attempt):
    status="expired" if attempt.status=="open" and attempt.expires_at and attempt.expires_at<=timezone.now() else attempt.status
    return {"reference":attempt.reference,"context":attempt.context,"plan_key":attempt.plan_key,"status":status,
            "created_at":attempt.created_at.isoformat(),"expires_at":attempt.expires_at.isoformat() if attempt.expires_at else None}

def _checkout_body(url, attempt=None):
    if attempt is None: return {"url":url}
    state=_attempt_data(attempt)
    resumable=state["status"]=="open" and bool(attempt.stripe_session_id)
    return {"url":attempt.stripe_checkout_url if resumable else None,"attempt":state}

def _append_attempt(url, reference):
    parts=urlsplit(url)
    query=parse_qsl(parts.query,keep_blank_values=True)+[("checkout_attempt",reference)]
    return urlunsplit((parts.scheme,parts.netloc,parts.path,urlencode(query),parts.fragment))

def _contextual_checkout(request,data):
    from django.db import IntegrityError
    from .config import load_checkout_contexts
    from .stripe_service import _latest_price, _stripe
    contexts,_=load_checkout_contexts(); context=data.get("context")
    if not isinstance(context,str) or context not in contexts: return JsonResponse({"detail":"unknown_context"},status=400)
    idem=data.get("idempotency_key")
    if not isinstance(idem,str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,64}",idem,flags=re.ASCII): return JsonResponse({"detail":"invalid_idempotency_key"},status=400)
    key=data.get("plan_key")
    plan=next((p for p in load_billing_config().plans if p.key==key),None)
    if plan is None or plan.kind == "continuation" or not plan.available: return JsonResponse({"detail":"unknown_plan"},status=400)
    app=getattr(settings,"DCM_BILLING_APP","")
    attempt=CheckoutAttempt.objects.filter(user=request.user,app=app,idempotency_key=idem).first()
    if attempt is not None:
        if attempt.plan_key!=plan.key or attempt.context!=context: return JsonResponse({"detail":"idempotency_key_reused"},status=409)
    else:
        # a replay repeats the stored price; only a new attempt resolves the current one
        try: price=_latest_price(plan.key)
        except LookupError: return JsonResponse({"detail":"unknown_plan"},status=400)
        try:
            with transaction.atomic():
                attempt=CheckoutAttempt.objects.create(reference=secrets.token_urlsafe(24),user=request.user,app=app,plan_key=plan.key,
                    context=context,idempotency_key=idem,stripe_price_id=price.stripe_price_id)
        except IntegrityError:
            attempt=CheckoutAttempt.objects.get(user=request.user,app=app,idempotency_key=idem)
            if attempt.plan_key!=plan.key or attempt.context!=context: return JsonResponse({"detail":"idempotency_key_reused"},status=409)
    with transaction.atomic():
        attempt=CheckoutAttempt.objects.select_for_update().get(pk=attempt.pk)
        state=_attempt_data(attempt)
        if state["status"]!="open": return JsonResponse({"detail":"attempt_not_open","attempt":state},status=409)
        if attempt.stripe_session_id: return JsonResponse(_checkout_body(None,attempt))
        try:
            destinations=contexts[context]
            customer=get_customer(request.user)
            params=checkout_session_params(plan=plan,price=type("StoredPrice",(),{"stripe_price_id":attempt.stripe_price_id})(),user=request.user,
                customer_id=customer.stripe_customer_id,success_url=_append_attempt(destinations["success_url"],attempt.reference),
                cancel_url=_append_attempt(destinations["cancel_url"],attempt.reference),attempt_reference=attempt.reference,context=context)
            session=_stripe().checkout.Session.create(**params,idempotency_key=f"dcm-checkout:{app}:{attempt.reference}")
        except Exception: return JsonResponse({"detail":"checkout_unavailable"},status=502)
        attempt.stripe_session_id=session.id; attempt.stripe_checkout_url=session.url
        expiry=getattr(session,"expires_at",None)
        attempt.expires_at=datetime.fromtimestamp(expiry,tz=dt_timezone.utc) if expiry else None
        attempt.save(update_fields=("stripe_session_id","stripe_checkout_url","expires_at","updated_at"))
        return JsonResponse(_checkout_body(session.url,attempt))

@require_GET
def checkout_attempt(request,reference):
    response_headers={"Cache-Control":"no-store"}
    if not request.user.is_authenticated: return JsonResponse({"detail":"authentication_required"},status=401,headers=response_headers)
    attempt=CheckoutAttempt.objects.filter(reference=reference,user=request.user,app=getattr(settings,"DCM_BILLING_APP","")).first()
    if attempt is None: return JsonResponse({"detail":"attempt_not_found"},status=404,headers=response_headers)
    return JsonResponse(_checkout_body(None,attempt),headers=response_headers)

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
    contracts={c.purchase_id:c for c in Contract.objects.filter(user=request.user,app=getattr(settings,"DCM_BILLING_APP",""),purchase__isnull=False)}
    for p in Purchase.objects.filter(user=request.user).order_by("-purchased_at"):
        result=eligibility(p,now)
        row={"id":p.pk,"kind":p.kind,"plan_key":p.plan_key,"purchased_at":p.purchased_at.isoformat(),"amount_paid_cents":p.amount_paid_cents,"currency":p.currency,"eligible":result.eligible,"reason":result.reason,"withdrawn_at":p.withdrawn_at.isoformat() if p.withdrawn_at else None}
        contract=contracts.get(p.pk)
        if contract and contract.withdrawal_policy=="full":
            from .contracts import withdrawal_eligibility
            full=withdrawal_eligibility(contract,now); row.update({"deadline":full.deadline.isoformat(),"policy":"full","eligible":full.eligible,"reason":full.reason})
        rows.append(row)
    return JsonResponse({"purchases":rows})

def _purchase_or_404(request, purchase_id):
    from django.shortcuts import get_object_or_404
    return get_object_or_404(Purchase,user=request.user,pk=purchase_id)

@csrf_protect
@require_GET
def withdrawal_preview(request,purchase_id):
    if not request.user.is_authenticated: return JsonResponse({"detail":"authentication_required"},status=401)
    p=_purchase_or_404(request,purchase_id); now=timezone.now()
    contract=Contract.objects.filter(purchase=p,app=getattr(settings,"DCM_BILLING_APP",""),withdrawal_policy="full").first()
    if contract:
        from .contracts import withdrawal_eligibility
        result=withdrawal_eligibility(contract,now); payments=list(contract.payments.all())
        amount=sum(int(x.amount_paid_cents) for x in payments); refunded=sum(int(x.refunded_cents) for x in payments)
        return JsonResponse({"policy":"full","eligible":result.eligible,"reason":result.reason,"deadline":result.deadline.isoformat(),"amount_paid_cents":amount,"refunded_cents":refunded,"refund_cents":max(0,amount-refunded),"currency":(payments[0].currency if payments else p.currency)})
    eligible=eligibility(p,now); calc=refund_amount(p,now)
    line=f"Paid {p.amount_paid_cents} {p.currency}; time share {calc.time_share_cents}; token share {calc.token_share_cents}; amount used {calc.used_cents}; refund {calc.refund_cents} {p.currency}."
    return JsonResponse({"eligible":eligible.eligible,"reason":eligible.reason,"amount_paid_cents":p.amount_paid_cents,"currency":p.currency,"time_share_cents":calc.time_share_cents,"token_share_cents":calc.token_share_cents,"used_cents":calc.used_cents,"refund_cents":calc.refund_cents,"calculation":line})

@csrf_protect
@require_http_methods(["POST"])
def withdrawal_execute(request,purchase_id):
    if not request.user.is_authenticated: return JsonResponse({"detail":"authentication_required"},status=401)
    import json
    p=_purchase_or_404(request,purchase_id)
    contract=Contract.objects.filter(purchase=p,app=getattr(settings,"DCM_BILLING_APP",""),withdrawal_policy="full").first()
    if contract:
        try: data=json.loads(request.body or b"{}")
        except (ValueError,UnicodeDecodeError): return JsonResponse({"code":"invalid_json"},status=400)
        if not isinstance(data,dict) or data.get("confirm") is not True: return JsonResponse({"code":"confirmation_required"},status=400)
        from .declarations import create_owner_declaration
        with transaction.atomic():
            locked=Contract.objects.select_for_update().get(pk=contract.pk)
            declaration=Declaration.objects.filter(contract=locked,kind="withdrawal",channel="account").order_by("-received_at","-pk").first()
            if not declaration or declaration.status in ("needs_review","rejected"):
                declaration=create_owner_declaration(locked,"withdrawal")
        p.refresh_from_db()
        refunds=list(Refund.objects.filter(declaration=declaration))
        payment_total=sum(x.amount_paid_cents for x in contract.payments.all())
        refunded_total=sum(x.refunded_cents for x in contract.payments.all())
        refund_status=next((r.status for r in refunds if r.status not in ("succeeded","cancelled")),"succeeded" if refunded_total>=payment_total else "pending")
        return JsonResponse({"status":declaration.status,"declaration_reference":declaration.reference,
            "refund_status":refund_status,
            "refund_cents":sum(r.amount_cents for r in refunds),"withdrawn_at":p.withdrawn_at.isoformat() if p.withdrawn_at else None})
    if p.status=="withdrawn":
        return JsonResponse({"already_withdrawn":True,"withdrawn_at":p.withdrawn_at.isoformat() if p.withdrawn_at else None,"refund_cents":p.refund_cents or 0,"currency":p.currency,"calculation":p.withdrawal_note})
    try: data=json.loads(request.body or b"{}")
    except (ValueError,UnicodeDecodeError): return JsonResponse({"detail":"invalid_json"},status=400)
    if not isinstance(data,dict) or data.get("confirm") is not True: return JsonResponse({"detail":"confirmation_required"},status=400)
    try: calc,already=execute_withdrawal(p)
    except ValueError as exc: return JsonResponse({"code":str(exc)},status=409)
    except Exception: return JsonResponse({"code":"withdrawal_unavailable"},status=502)
    p.refresh_from_db()  # the stored result: a webhook may have completed the withdrawal first
    return JsonResponse({"already_withdrawn":already,"refund_cents":p.refund_cents if already else calc.refund_cents,"currency":p.currency,"withdrawn_at":p.withdrawn_at.isoformat() if p.withdrawn_at else timezone.now().isoformat(),"calculation":p.withdrawal_note})

from django.http import HttpResponse


def _contract_config_enabled():
    try: return bool(load_billing_config().contract)
    except Exception: return False


def _public_declarations_enabled():
    try:
        policy=load_billing_config().contract
        return bool(policy and policy["public_declarations"])
    except Exception: return False


@ensure_csrf_cookie
@require_GET
def public_declarations_csrf(request):
    if not _public_declarations_enabled(): return JsonResponse({"code":"not_found"},status=404)
    return JsonResponse({})


@csrf_protect
@require_http_methods(["POST"])
def public_declarations(request):
    if not _public_declarations_enabled(): return JsonResponse({"code":"not_found"},status=404)
    from .declarations import DeclarationInputError,check_public_throttles,create_public_declaration,validate_declaration
    try: data=json.loads(request.body or b"{}")
    except (ValueError,UnicodeDecodeError): data=None
    email=data.get("email","") if isinstance(data,dict) and isinstance(data.get("email", ""),str) else ""
    if not check_public_throttles(request,email): return JsonResponse({"code":"throttled"},status=429)
    try: values=validate_declaration(data)
    except DeclarationInputError as exc: return JsonResponse({"code":exc.code},status=400)
    declaration=create_public_declaration(values)
    submitted={k:(v.isoformat() if hasattr(v,"isoformat") else v) for k,v in values.items()}
    response=JsonResponse({"reference":declaration.reference,"kind":declaration.kind,"received_at":declaration.received_at.isoformat(),"submitted":submitted,"status":"received"},status=201)
    response["Cache-Control"]="no-store"
    return response


@csrf_protect
@require_http_methods(["POST"])
def public_declarations_confirm(request):
    if not _public_declarations_enabled(): return JsonResponse({"code":"not_found"},status=404)
    try: data=json.loads(request.body or b"{}")
    except (ValueError,UnicodeDecodeError): data={}
    from .declarations import confirm_public_declaration
    declaration=confirm_public_declaration(data.get("token","") if isinstance(data,dict) else "")
    if declaration is None: return JsonResponse({"code":"invalid_token"},status=400)
    response=JsonResponse({"reference":declaration.reference,"status":declaration.status,"effective_at":declaration.effective_at.isoformat() if declaration.effective_at else None})
    response["Cache-Control"]="no-store"
    return response


@csrf_protect
@require_GET
def contracts_list(request):
    if not _contract_config_enabled(): return JsonResponse({"code":"not_found"},status=404)
    if not request.user.is_authenticated: return JsonResponse({"detail":"authentication_required"},status=401)
    from .contracts import contract_summary
    rows=Contract.objects.filter(user=request.user,app=getattr(settings,"DCM_BILLING_APP","" )).order_by("-started_at","-pk")
    response=JsonResponse({"contracts":[contract_summary(c) for c in rows]}); response["Cache-Control"]="no-store"
    return response


def _own_contract(request,reference):
    from django.shortcuts import get_object_or_404
    return get_object_or_404(Contract.objects.select_related("purchase","user"),reference=reference,user=request.user,app=getattr(settings,"DCM_BILLING_APP",""))


@csrf_protect
@require_http_methods(["POST"])
def contract_cancel(request,reference):
    if not _contract_config_enabled(): return JsonResponse({"code":"not_found"},status=404)
    if not request.user.is_authenticated: return JsonResponse({"detail":"authentication_required"},status=401)
    contract=_own_contract(request,reference)
    try: data=json.loads(request.body or b"{}")
    except (ValueError,UnicodeDecodeError): return JsonResponse({"code":"invalid_json"},status=400)
    if not isinstance(data,dict): return JsonResponse({"code":"invalid_request"},status=400)
    cancellation_type=data.get("cancellation_type","ordinary")
    if cancellation_type not in ("ordinary","extraordinary"): return JsonResponse({"code":"invalid_cancellation_type"},status=400)
    requested=data.get("requested_end_date")
    if requested in (None,""): requested_date=None
    elif isinstance(requested,str) and len(requested)<=10:
        from django.utils.dateparse import parse_date
        requested_date=parse_date(requested)
        if requested_date is None: return JsonResponse({"code":"invalid_requested_end_date"},status=400)
    else: return JsonResponse({"code":"invalid_requested_end_date"},status=400)
    reason=data.get("reason",""); message=data.get("message","")
    if not isinstance(reason,str) or len(reason)>2000 or (cancellation_type=="extraordinary" and not reason.strip()): return JsonResponse({"code":"invalid_reason"},status=400)
    if not isinstance(message,str) or len(message)>2000: return JsonResponse({"code":"invalid_message"},status=400)
    from .declarations import create_owner_declaration
    declaration=create_owner_declaration(contract,"cancellation",cancellation_type=cancellation_type,requested_end_date=requested_date,reason=reason,message=message)
    response=JsonResponse({"reference":declaration.reference,"status":declaration.status,"received_at":declaration.received_at.isoformat(),"effective_at":declaration.effective_at.isoformat() if declaration.effective_at else None},status=201)
    response["Cache-Control"]="no-store"
    return response


@csrf_protect
@require_http_methods(["POST"])
def contract_withdraw(request,reference):
    if not _contract_config_enabled(): return JsonResponse({"code":"not_found"},status=404)
    if not request.user.is_authenticated: return JsonResponse({"detail":"authentication_required"},status=401)
    contract=_own_contract(request,reference)
    try: data=json.loads(request.body or b"{}")
    except (ValueError,UnicodeDecodeError): return JsonResponse({"code":"invalid_json"},status=400)
    if not isinstance(data,dict) or data.get("confirm") is not True: return JsonResponse({"code":"confirmation_required"},status=400)
    from .declarations import create_owner_declaration
    declaration=create_owner_declaration(contract,"withdrawal")
    refunds=list(Refund.objects.filter(declaration=declaration))
    payment_total=sum(x.amount_paid_cents for x in contract.payments.all()); refunded_total=sum(x.refunded_cents for x in contract.payments.all())
    refund_status=next((r.status for r in refunds if r.status not in ("succeeded","cancelled")),"succeeded" if refunded_total>=payment_total else "pending")
    response=JsonResponse({"reference":declaration.reference,"status":declaration.status,"refund_status":refund_status,"refund_cents":sum(x.amount_cents for x in refunds)},status=201)
    response["Cache-Control"]="no-store"
    return response

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
