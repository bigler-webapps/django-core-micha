"""Owner-only REST API for catalogue, persistent chats, and turns."""
import uuid
from django.db.models import Q
from django.http import Http404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .availability import availability
from .catalogue import default_entry, entries, entry_by_id, entry_id, selection_of, validate
from .hooks import can_access_scope
from .models import AssistantChat
from .pagination import InvalidCursor, decode_cursor, encode_cursor
from .turns import delete_session, reconcile_interrupted, turn_payload

def _chat(user, chat_id):
    try: chat = AssistantChat.objects.get(pk=chat_id, owner=user)
    except (AssistantChat.DoesNotExist, ValueError): raise Http404
    if not can_access_scope(user, chat.scope): raise Http404
    reconcile_interrupted(chat)
    return chat

def _iso(value): return value.isoformat() if value else None
def payload(chat):
    return {"id":str(chat.pk),"scope":chat.scope,"title":chat.title,"title_source":chat.title_source,
        "archived_at":_iso(chat.archived_at),"driver":chat.selected_driver,"model":chat.selected_model,
        "effort":chat.selected_effort,"selection":_selection(chat),"created_at":_iso(chat.created_at),"updated_at":_iso(chat.updated_at)}

def _selection(chat):
    row=next((r for r in entries() if r.get("driver")==chat.selected_driver and r.get("model")==chat.selected_model),None)
    return selection_of(row,chat.selected_effort) if row else None

def _problem(code, status_code=400): return Response({"code":code,"error":code},status=status_code)
def _cursor_context(kind,user,**extra): return {"kind":kind,"user":str(user.pk),**extra}

class CatalogueView(APIView):
    permission_classes=[IsAuthenticated]
    def get(self,request):
        row=default_entry()
        return Response({"default":selection_of(row) if row else None,"entries":[{"id":entry_id(e),"driver":e["driver"],
            "model":e["model"],"label":e.get("name",e["model"]),"efforts":e["efforts"],"billing":e["billing"],
            "default":bool(e.get("default"))} for e in entries()]})

class AvailabilityView(APIView):
    permission_classes=[IsAuthenticated]
    def get(self,request): return Response(availability())

class ChatListCreateView(APIView):
    permission_classes=[IsAuthenticated]
    def get(self,request):
        archived=request.query_params.get("archived","false").lower() in {"1","true","yes"}
        try: limit=min(100,max(1,int(request.query_params.get("limit",20))))
        except (TypeError,ValueError): return _problem("invalid_cursor")
        context=_cursor_context("chats",request.user,archived=archived,limit=limit)
        qs=AssistantChat.objects.filter(owner=request.user,archived_at__isnull=archived)
        if request.query_params.get("cursor"):
            try:
                c=decode_cursor(request.query_params["cursor"],context); stamp=timezone.datetime.fromisoformat(c["updated_at"]); cid=uuid.UUID(c["id"])
                qs=qs.filter(Q(updated_at__lt=stamp)|Q(updated_at=stamp,pk__lt=cid))
            except Exception: return _problem("invalid_cursor")
        rows=[row for row in qs.order_by("-updated_at","-pk") if can_access_scope(request.user,row.scope)][:limit+1]
        more=len(rows)>limit; rows=rows[:limit]
        nxt=encode_cursor({"updated_at":rows[-1].updated_at.isoformat(),"id":str(rows[-1].pk)},context) if more and rows else None
        return Response({"results":[payload(x) for x in rows],"next":nxt,"has_more":more})
    def post(self,request):
        body=request.data if isinstance(request.data,dict) else {}
        if set(body)-{"title","scope","driver","model","effort"}: return _problem("invalid_request")
        scope=body.get("scope","")
        if not isinstance(scope,str) or len(scope)>255 or not can_access_scope(request.user,scope): return _problem("access_lost",403)
        selection=default_entry()
        driver=body.get("driver",selection.get("driver") if selection else "")
        model=body.get("model",selection.get("model") if selection else "")
        effort=body.get("effort",(selection.get("efforts") or ["medium"])[0] if selection else "medium")
        if driver and model:
            try: validate(driver,model,effort)
            except ValueError: return _problem("invalid_selection")
        title=body.get("title","New chat")
        if not isinstance(title,str) or not title.strip() or len(title)>200: return _problem("invalid_title")
        chat=AssistantChat.objects.create(owner=request.user,scope=scope,title=title.strip(),title_source="user" if "title" in body else "automatic",
            selected_driver=driver,selected_model=model,selected_effort=effort)
        return Response(payload(chat),status=status.HTTP_201_CREATED)

class ChatDetailView(APIView):
    permission_classes=[IsAuthenticated]
    def get(self,request,chat_id): return Response(payload(_chat(request.user,chat_id)))
    def patch(self,request,chat_id):
        chat=_chat(request.user,chat_id); body=request.data if isinstance(request.data,dict) else {}
        if chat.archived_at and body.get("archived") is not False: return _problem("archived",409)
        allowed={"title","archived","driver","model","effort","selection"}
        if not body or set(body)-allowed: return _problem("invalid_request")
        if "selection" in body:
            picked=body["selection"]
            row=entry_by_id(picked.get("entry")) if isinstance(picked,dict) and set(picked)<={"entry","effort"} else None
            if row is None: return _problem("invalid_selection")
            body={**{k:v for k,v in body.items() if k!="selection"},"driver":row["driver"],"model":row["model"],
                "effort":picked.get("effort",chat.selected_effort)}
        if "title" in body:
            title=body["title"]
            if not isinstance(title,str) or not title.strip() or len(title)>200: return _problem("invalid_title")
            chat.title=title.strip(); chat.title_source="user"
        if "archived" in body:
            if type(body["archived"]) is not bool: return _problem("invalid_request")
            chat.archived_at=timezone.now() if body["archived"] else None
        if set(body)&{"driver","model","effort"}:
            driver=body.get("driver",chat.selected_driver); model=body.get("model",chat.selected_model); effort=body.get("effort",chat.selected_effort)
            try: validate(driver,model,effort)
            except ValueError: return _problem("invalid_selection")
            chat.selected_driver,chat.selected_model,chat.selected_effort=driver,model,effort
        chat.save(); return Response(payload(chat))
    def delete(self,request,chat_id):
        chat=_chat(request.user,chat_id); delete_session(chat); chat.delete(); return Response(status=204)

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
        return Response({"results":[turn_payload(row) for row in rows],"next":nxt,"has_more":more})
