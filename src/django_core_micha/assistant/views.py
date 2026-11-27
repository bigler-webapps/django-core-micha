"""Owner-only REST API for catalogue, persistent chats, and turns."""
import uuid
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .availability import availability
from .catalogue import (default_entry, entries, entry_by_id, entry_id, entry_enabled, model_level,
    current_family_policy, row_source_id, row_source_label, selection_of, usable_entries,
    validate, validate_family)
from .hooks import availability_reason, can_access_scope, is_shared_scope, _hook
from .access import can_view_chat, is_creator
from .models import AssistantChat
from .history import summary_payload
from .pagination import InvalidCursor, decode_cursor, encode_cursor
from .turns import delete_session, reconcile_interrupted, turn_payload
from django_core_micha.ai_policy.policy import current_policy, policy_enabled
from django_core_micha.ai_policy.resolver import family_identity_for_route

def _chat(user, chat_id):
    try: chat = AssistantChat.objects.get(pk=chat_id)
    except (AssistantChat.DoesNotExist, ValueError): raise Http404
    if not can_view_chat(user, chat): raise Http404
    reconcile_interrupted(chat)
    return chat

def _iso(value): return value.isoformat() if value else None
def payload(chat):
    if policy_enabled():
        choice = _selection(chat)
        result = {"id":str(chat.pk),"scope":chat.scope,"title":chat.title,"title_source":chat.title_source,
            "archived_at":_iso(chat.archived_at),"effort":chat.selected_effort,"selection":choice,
            "created_at":_iso(chat.created_at),"updated_at":_iso(chat.updated_at)}
        if choice:
            result.update({"provider": choice["provider"], "family": choice["family"]})
        return result
    return {"id":str(chat.pk),"scope":chat.scope,"title":chat.title,"title_source":chat.title_source,
        "archived_at":_iso(chat.archived_at),"driver":chat.selected_driver,"model":chat.selected_model,
        "source_id":chat.selected_source_id or None,
        "effort":chat.selected_effort,"selection":_selection(chat),"created_at":_iso(chat.created_at),"updated_at":_iso(chat.updated_at)}

def _selection(chat):
    if current_family_policy():
        provider, family = chat.selected_provider, chat.selected_family
        if not provider or not family:
            identity = family_identity_for_route(chat.selected_source_id, chat.selected_model,
                                                 chat.selected_driver)
            if identity:
                provider, family = identity
        if provider and family:
            return {"entry":f"{provider}:{family}","provider":provider,"family":family,"effort":chat.selected_effort}
        return None
    if policy_enabled():
        return None
    managed = policy_enabled()
    row = next((r for r in entries() if r.get("driver") == chat.selected_driver and r.get("model") == chat.selected_model
                and (not managed or r.get("source_id") == chat.selected_source_id)), None)
    return selection_of(row,chat.selected_effort) if row else None

def _problem(code, status_code=400): return Response({"code":code,"error":code},status=status_code)

def _family_selection(value):
    if not isinstance(value, dict) or set(value) - {"entry", "provider", "family", "effort"}:
        raise ValueError("invalid_selection")
    provider, family = value.get("provider"), value.get("family")
    entry = value.get("entry")
    if entry is not None:
        if not isinstance(entry, str) or entry.count(":") != 1:
            raise ValueError("invalid_selection")
        entry_provider, entry_family = entry.split(":")
        if provider not in (None, entry_provider) or family not in (None, entry_family):
            raise ValueError("invalid_selection")
        provider, family = entry_provider, entry_family
    return provider, family, value.get("effort")
def _cursor_context(kind,user,**extra): return {"kind":kind,"user":str(user.pk),**extra}

def _shape(request,rows,context_tokens):
    """Catalogue entry JSON; shared by the user catalogue and the admin model list."""
    from django_core_micha.ai_prices import price_usage
    from .execution import model_supports_execution
    converter=_catalogue_hook("COST_TO_TOKENS")
    medium_costs={entry_id(e):price_usage(e["model"],input_tokens=context_tokens,output_tokens=800) for e in rows}
    ranked=sorted(medium_costs.values())
    def relative(cost):
        if len(ranked)<=1:return 1
        rank=ranked.index(cost)
        return min(3,1+(rank*3//len(ranked)))
    shaped=[]
    for e in rows:
        estimates={}
        for effort,output in (("low",300),("medium",800),("high",2000),("max",5000)):
            if effort not in e["efforts"]:continue
            low_cost=price_usage(e["model"],input_tokens=context_tokens,output_tokens=output//2)
            high_cost=price_usage(e["model"],input_tokens=context_tokens,output_tokens=output*2)
            if converter:
                try: low,high=int(converter(request.user,low_cost)),int(converter(request.user,high_cost));unit="tokens"
                except Exception: low,high,unit=relative(medium_costs[entry_id(e)]),relative(medium_costs[entry_id(e)]),"level"
            else: low,high,unit=relative(medium_costs[entry_id(e)]),relative(medium_costs[entry_id(e)]),"level"
            estimates[effort]={"min":low,"max":high,"unit":unit}
        shaped.append({"id":entry_id(e),"driver":e["driver"],"model":e["model"],"label":e.get("name",e["model"]),
            "provider":{"claude_cli":"claude_code","anthropic_api":"anthropic","openai_api":"openai"}[e["driver"]],
            "level":model_level(e),"efforts":e["efforts"],"billing":e["billing"],"default":bool(e.get("default")),"estimate":estimates,
            **({"source_id": row_source_id(e), "source_label": row_source_label(e), "policy_revision": e.get("policy_revision")} if e.get("source_id") else {}),
            "code_execution_supported":model_supports_execution(e),
            "average_cost_usd_micros":_average_cost(f"{e['driver']}:{e['model']}")})
    return shaped

class CatalogueView(APIView):
    permission_classes=[IsAuthenticated]
    def get(self,request):
        rows=usable_entries(request.user)
        row=default_entry(request.user)
        chat_id=request.query_params.get("chat")
        context_tokens=0
        if chat_id:
            chat=_chat(request.user,chat_id)
            last=chat.turns.filter(state="completed").order_by("-sequence").first()
            context_tokens=last.context_tokens if last else 0
        if policy_enabled() and current_family_policy():
            shaped = [{"id":entry_id(item),"provider":item["provider"],"family":item["family"],
                       "label":item["family_label"],"efforts":list(item["efforts"])} for item in rows]
            payload = {"default": selection_of(row) if row else None, "entries": shaped}
        elif policy_enabled():
            payload = {"default": None, "entries": []}
        else:
            shaped=_shape(request,rows,context_tokens)
            payload = {"default": selection_of(row) if row else None, "entries": shaped}
        if policy_enabled():
            state = current_policy()
            payload["policy"] = {"revision": state.revision if state else None}
        return _no_store(Response(payload))

def _average_cost(value):
    from .limit import average_cost_usd_micros
    return average_cost_usd_micros(value)

def _is_settings_admin(user):
    try:
        fn = _hook("SETTINGS_ADMIN")
        return bool(fn(user)) if fn else bool(user.is_staff or user.is_superuser)
    except Exception:
        return False

def _no_store(response):
    response["Cache-Control"]="no-store"; return response

class UserLimitView(APIView):
    permission_classes=[IsAuthenticated]
    def get(self, request):
        from .limit import limit_state
        return _no_store(Response(limit_state(request.user)))

def _admin_limit():
    from .limit import effective_limit, limit_setting, stored_config
    conf=limit_setting(); row=stored_config()
    return {"period":conf["period"],"limit_usd_micros":effective_limit(),"default_limit_usd_micros":conf["usd_micros"],
            "override":bool(row and row.limit_usd_micros is not None)}

class AdminLimitView(APIView):
    permission_classes=[IsAuthenticated]
    def _guard(self, request):
        from .limit import limit_setting
        if not _is_settings_admin(request.user): return _problem("forbidden",403)
        if limit_setting() is None: return _problem("not_configured",404)
        return None
    def get(self, request):
        return self._guard(request) or _no_store(Response(_admin_limit()))
    def patch(self, request):
        from .limit import save_config
        denied=self._guard(request)
        if denied: return denied
        body=request.data if isinstance(request.data,dict) else None
        if body is None or set(body)!={"limit_usd_micros"}: return _problem("invalid_request")
        value=body["limit_usd_micros"]
        if value is not None and (type(value) is not int or value<0): return _problem("invalid_request")
        save_config(limit_usd_micros=value)
        return _no_store(Response(_admin_limit()))

def _admin_models(request):
    if current_family_policy():
        rows=usable_entries(request.user)
        default=default_entry(request.user)
        return {"default":selection_of(default) if default else None,
                "entries":[{"id":entry_id(row),"provider":row["provider"],"family":row["family"],
                    "label":row["family_label"],"efforts":list(row["efforts"]),"enabled":True,
                    "provider_available":True} for row in rows]}
    if policy_enabled():
        return {"default":None,"entries":[]}
    from .limit import stored_default_entry
    from .availability import availability
    providers=availability().get("providers",{})
    enabled=[row for row in entries() if entry_enabled(row)]
    stored=stored_default_entry()
    default=next((row for row in enabled if entry_id(row)==stored),None) if stored else None
    if default is None and enabled:
        level=getattr(settings,"DCM_ASSISTANT_DEFAULT_LEVEL","balanced")
        default=next((row for row in enabled if model_level(row)==level),next((row for row in enabled if row.get("default")),enabled[0]))
    shaped=[{**item,"enabled":entry_enabled(row),"provider_available":providers.get(row["driver"])=="available"}
            for row,item in zip(entries(),_shape(request,entries(),0))]
    return {"default":entry_id(default) if default else None,"entries":shaped}

class AdminModelsView(APIView):
    permission_classes=[IsAuthenticated]
    def get(self, request):
        if not _is_settings_admin(request.user): return _problem("forbidden",403)
        return _no_store(Response(_admin_models(request)))
    def patch(self, request):
        if current_family_policy(): return _problem("not_configured",404)
        from .limit import disabled_entry_ids, save_config, stored_default_entry
        if not _is_settings_admin(request.user): return _problem("forbidden",403)
        body=request.data if isinstance(request.data,dict) else None
        if not body or set(body)-{"enabled","default"}: return _problem("invalid_request")
        all_ids=[entry_id(row) for row in entries()]
        if "enabled" in body:
            picked=body["enabled"]
            if not isinstance(picked,list) or any(not isinstance(item,str) for item in picked): return _problem("invalid_request")
            if any(item not in all_ids for item in picked): return _problem("invalid_selection")
            enabled=[item for item in all_ids if item in set(picked)]
        else:
            disabled=disabled_entry_ids(); enabled=[item for item in all_ids if item not in disabled]
        default=body.get("default",stored_default_entry())
        if default is None: default=""  # cleared: the settings default rule picks among the enabled entries
        if "default" in body and (not isinstance(default,str) or (default and default not in all_ids)): return _problem("invalid_selection")
        if default and default not in enabled: return _problem("default_disabled")
        save_config(disabled_entries=[item for item in all_ids if item not in enabled],default_entry=default or "")
        return _no_store(Response(_admin_models(request)))

def _catalogue_hook(name):
    from .hooks import _hook
    return _hook(name)

class AvailabilityView(APIView):
    permission_classes=[IsAuthenticated]
    def get(self,request):
        result=availability()
        if "scope" in request.query_params:
            scope=request.query_params.get("scope","")
            if not can_access_scope(request.user,scope): return _problem("not_found",404)
            reason=availability_reason(request.user,scope)
            if reason:
                if policy_enabled():
                    state = current_policy()
                    return Response({"status":"unavailable", "reason":reason,
                                     "policy":{"revision":state.revision if state else None}})
                return Response({"status":"unavailable","reason":reason,"providers":result.get("providers",{})})
        if current_family_policy():
            rows = usable_entries(request.user)
            state = current_policy()
            result = {"status": "available" if rows else "unavailable",
                      "policy": {"revision": state.revision if state else None}}
            if not rows:
                result["reason"] = "no_model_enabled" if state else "policy_unavailable"
        elif policy_enabled():
            state = current_policy()
            result = {"status":"unavailable","reason":"policy_unavailable",
                      "policy":{"revision":state.revision if state else None}}
        elif entries() and result.get("status") == "available" and not usable_entries(request.user):
            return Response({"status": "unavailable", "reason": "no_model_enabled", "providers": result.get("providers", {})})
        return Response(result)

class ChatListCreateView(APIView):
    permission_classes=[IsAuthenticated]
    def get(self,request):
        archived=request.query_params.get("archived","false").lower() in {"1","true","yes"}
        try: limit=min(100,max(1,int(request.query_params.get("limit",20))))
        except (TypeError,ValueError): return _problem("invalid_cursor")
        scope=request.query_params.get("scope")
        if scope is not None and not can_access_scope(request.user,scope): return _problem("not_found",404)
        context=_cursor_context("chats",request.user,archived=archived,limit=limit,scope=scope)
        qs=AssistantChat.objects.filter(owner=request.user,archived_at__isnull=not archived)
        if scope is not None:
            qs=AssistantChat.objects.filter(scope=scope,archived_at__isnull=not archived) if is_shared_scope(scope) else qs.filter(scope=scope)
        if request.query_params.get("cursor"):
            try:
                c=decode_cursor(request.query_params["cursor"],context); stamp=timezone.datetime.fromisoformat(c["updated_at"]); cid=uuid.UUID(c["id"])
                qs=qs.filter(Q(updated_at__lt=stamp)|Q(updated_at=stamp,pk__lt=cid))
            except Exception: return _problem("invalid_cursor")
        rows=[row for row in qs.order_by("-updated_at","-pk") if can_view_chat(request.user,row)][:limit+1]
        more=len(rows)>limit; rows=rows[:limit]
        nxt=encode_cursor({"updated_at":rows[-1].updated_at.isoformat(),"id":str(rows[-1].pk)},context) if more and rows else None
        return Response({"results":[payload(x) for x in rows],"next":nxt,"has_more":more})
    def post(self,request):
        if policy_enabled() and not current_family_policy(): return _problem("not_configured",503)
        body=request.data if isinstance(request.data,dict) else {}
        if current_family_policy():
            if set(body)-{"title","scope","provider","family","effort","selection"}:
                return _problem("invalid_request")
            scope=body.get("scope","")
            if not isinstance(scope,str) or len(scope)>255 or not can_access_scope(request.user,scope): return _problem("access_lost",403)
            selection_body=body.get("selection")
            if selection_body is not None:
                if any(key in body for key in ("provider","family","effort")):
                    return _problem("invalid_request")
                try: provider, family, effort = _family_selection(selection_body)
                except ValueError: return _problem("invalid_selection")
            else:
                provider, family, effort = body.get("provider"), body.get("family"), body.get("effort")
            if (provider is None) != (family is None):
                return _problem("invalid_selection")
            picked = default_entry(request.user) if provider is None else None
            if provider is None:
                if picked is None: return _problem("not_configured")
                provider, family = picked["provider"], picked["family"]
            try:
                validated = validate_family(provider, family, effort, request.user)
            except ValueError:
                return _problem("invalid_selection")
            if effort is None:
                effort = (validated.get("efforts") or ["medium"])[0]
            title=body.get("title","New chat")
            if not isinstance(title,str) or not title.strip() or len(title)>200: return _problem("invalid_title")
            chat=AssistantChat.objects.create(owner=request.user,scope=scope,title=title.strip(),
                title_source="user" if "title" in body else "automatic",selected_provider=provider,
                selected_family=family,selected_driver=validated["driver"],selected_model=validated["model"],
                selected_source_id=validated["source_id"],selected_effort=effort)
            return Response(payload(chat),status=status.HTTP_201_CREATED)
        if set(body)-{"title","scope","driver","model","effort","source_id"}: return _problem("invalid_request")
        scope=body.get("scope","")
        if not isinstance(scope,str) or len(scope)>255 or not can_access_scope(request.user,scope): return _problem("access_lost",403)
        selection=default_entry(request.user)
        driver=body.get("driver",selection.get("driver") if selection else "")
        model=body.get("model",selection.get("model") if selection else "")
        effort=body.get("effort",(selection.get("efforts") or ["medium"])[0] if selection else "medium")
        source = body.get("source_id")
        explicit = any(key in body for key in ("driver", "model", "source_id"))
        if policy_enabled() and not explicit and selection:
            source = selection.get("source_id")
        if policy_enabled() and explicit and not (driver and model):
            return _problem("invalid_selection")
        if driver and model:
            try: validated = validate(driver, model, effort, request.user, source)
            except ValueError: return _problem("invalid_selection")
            source = validated.get("source_id") if policy_enabled() else None
        title=body.get("title","New chat")
        if not isinstance(title,str) or not title.strip() or len(title)>200: return _problem("invalid_title")
        chat=AssistantChat.objects.create(owner=request.user,scope=scope,title=title.strip(),title_source="user" if "title" in body else "automatic",
            selected_driver=driver,selected_model=model,selected_effort=effort,
            selected_source_id=source or "")
        return Response(payload(chat),status=status.HTTP_201_CREATED)

class ChatDetailView(APIView):
    permission_classes=[IsAuthenticated]
    def get(self,request,chat_id): return Response(payload(_chat(request.user,chat_id)))
    @transaction.atomic
    def patch(self,request,chat_id):
        # Row lock: archiving serializes with a concurrent group-grant save on the same chat.
        chat = AssistantChat.objects.select_for_update().filter(pk=chat_id).first()
        if chat is None or not can_view_chat(request.user, chat): raise Http404
        reconcile_interrupted(chat)
        body=request.data if isinstance(request.data,dict) else {}
        if policy_enabled() and not current_family_policy(): return _problem("not_configured",503)
        if chat.archived_at and body.get("archived") is not False: return _problem("archived",409)
        if current_family_policy():
            allowed={"title","archived","provider","family","effort","selection"}
            if not body or set(body)-allowed: return _problem("invalid_request")
            if "selection" in body:
                picked=body["selection"]
                if any(key in body for key in ("provider","family","effort")):
                    return _problem("invalid_request")
                try: provider, family, effort = _family_selection(picked)
                except ValueError: return _problem("invalid_selection")
                body={**{k:v for k,v in body.items() if k!="selection"},"provider":provider,"family":family,
                      **({"effort":effort} if effort is not None else {})}
            if "title" in body:
                title=body["title"]
                if not isinstance(title,str) or not title.strip() or len(title)>200: return _problem("invalid_title")
                chat.title=title.strip(); chat.title_source="user"
            if "archived" in body:
                if type(body["archived"]) is not bool: return _problem("invalid_request")
                chat.archived_at=timezone.now() if body["archived"] else None
            if set(body)&{"provider","family","effort"}:
                provider=body.get("provider",chat.selected_provider)
                family=body.get("family",chat.selected_family)
                effort=body.get("effort",chat.selected_effort)
                if "effort" in body and effort is None: return _problem("invalid_selection")
                try: validated=validate_family(provider,family,effort,request.user)
                except ValueError: return _problem("invalid_selection")
                chat.selected_provider,chat.selected_family=provider,family
                chat.selected_driver,chat.selected_model=validated["driver"],validated["model"]
                chat.selected_source_id=validated["source_id"]
                chat.selected_effort=effort
            chat.save(); return Response(payload(chat))
        allowed={"title","archived","driver","model","effort","selection"}
        if policy_enabled(): allowed.add("source_id")
        if not body or set(body)-allowed: return _problem("invalid_request")
        if "selection" in body:
            picked=body["selection"]
            row=entry_by_id(picked.get("entry")) if isinstance(picked,dict) and set(picked)<={"entry","effort","source_id"} else None
            if row is None: return _problem("invalid_selection")
            body={**{k:v for k,v in body.items() if k!="selection"},"driver":row["driver"],"model":row["model"],
                "effort":picked.get("effort",chat.selected_effort)}
            source = row.get("source_id")
            if picked.get("source_id") not in (None, source):
                return _problem("invalid_selection")
            if policy_enabled():
                body["source_id"] = source
        if "title" in body:
            title=body["title"]
            if not isinstance(title,str) or not title.strip() or len(title)>200: return _problem("invalid_title")
            chat.title=title.strip(); chat.title_source="user"
        if "archived" in body:
            if type(body["archived"]) is not bool: return _problem("invalid_request")
            chat.archived_at=timezone.now() if body["archived"] else None
        if set(body)&{"driver","model","effort","source_id"}:
            driver=body.get("driver",chat.selected_driver); model=body.get("model",chat.selected_model); effort=body.get("effort",chat.selected_effort)
            source = body.get("source_id", chat.selected_source_id if driver == chat.selected_driver and model == chat.selected_model else None)
            try: validated = validate(driver, model, effort, request.user, source or None)
            except ValueError: return _problem("invalid_selection")
            chat.selected_driver,chat.selected_model,chat.selected_effort=driver,model,effort
            chat.selected_source_id=(validated.get("source_id") or "") if policy_enabled() else ""
        chat.save(); return Response(payload(chat))
    def delete(self,request,chat_id):
        chat=_chat(request.user,chat_id)
        if not is_creator(request.user,chat): return _problem("forbidden",403)
        delete_session(chat); chat.delete(); return Response(status=204)

class TurnsView(APIView):
    permission_classes=[IsAuthenticated]
    def get(self,request,chat_id):
        chat=_chat(request.user,chat_id)
        try: limit=min(100,max(1,int(request.query_params.get("limit",50))))
        except (TypeError,ValueError): return _problem("invalid_cursor")
        context=_cursor_context("turns",request.user,chat=str(chat.pk),limit=limit)
        qs=chat.turns.all()
        if request.query_params.get("cursor"):
            try:
                c=decode_cursor(request.query_params["cursor"],context); seq=int(c["sequence"]); qs=qs.filter(sequence__lt=seq)
            except Exception: return _problem("invalid_cursor")
        rows=list(qs.order_by("-sequence")[:limit+1]); more=len(rows)>limit; rows=rows[:limit]; rows.reverse()
        nxt=encode_cursor({"sequence":rows[0].sequence},context) if more and rows else None
        result={"results":[turn_payload(row,chat) for row in rows],"next":nxt,"has_more":more}
        if not request.query_params.get("cursor"):
            result["summary"]=summary_payload(chat)
        return Response(result)
