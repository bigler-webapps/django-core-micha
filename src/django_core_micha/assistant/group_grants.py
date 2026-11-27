"""Live per-chat group selection and catalogue intersection."""
import hashlib
import json
import re
from django.db import transaction
from .hooks import _hook

_ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_POLICIES = {"direct", "confirmation_required"}

def catalogue(chat, user):
    provider = _hook("GROUP_CATALOGUE")
    return None if provider is None else validate_catalogue(provider(chat, user))

def validate_catalogue(groups):
    if not isinstance(groups, list) or len(groups) > 100:
        raise ValueError("invalid_group_catalogue")
    group_ids, member_ids = set(), set()
    result = []
    for group in groups:
        if not isinstance(group, dict):
            raise ValueError("invalid_group_catalogue")
        gid, kind, members = group.get("id"), group.get("kind"), group.get("members")
        if (not isinstance(gid, str) or not _ID.fullmatch(gid) or gid in group_ids
                or kind not in {"read", "write"} or not isinstance(group.get("label"), str)
                or not isinstance(members, list) or len(members) > 100):
            raise ValueError("invalid_group_catalogue")
        group_ids.add(gid)
        projected = []
        for member in members:
            if not isinstance(member, dict):
                raise ValueError("invalid_group_catalogue")
            mid = member.get("id")
            available = member.get("available")
            policy = member.get("confirmation")
            tools, selectors = member.get("tools", []), member.get("selectors", [])
            if (not isinstance(mid, str) or not _ID.fullmatch(mid) or mid in member_ids
                    or not isinstance(member.get("label"), str) or type(available) is not bool
                    or (kind == "write" and policy not in _POLICIES)
                    or (kind == "read" and policy is not None)
                    or any(not isinstance(items, list) or len(items) > 100 or
                           any(not isinstance(item, str) or not _ID.fullmatch(item) for item in items)
                           for items in (tools, selectors))
                    or (not available and not isinstance(member.get("unavailable_reason"), str))):
                raise ValueError("invalid_group_catalogue")
            member_ids.add(mid)
            projected.append({"id": mid, "label": member["label"], "available": available,
                              "unavailable_reason": member.get("unavailable_reason") if not available else None,
                              "confirmation": policy, "tools": tools, "selectors": selectors})
        result.append({"id": gid, "label": group["label"], "kind": kind, "members": projected})
    return result


def catalogue_revision(groups):
    security = sorted((g["id"], g["kind"], sorted((m["id"], m["available"], m["confirmation"] or "", sorted(m["tools"]), sorted(m["selectors"])) for m in g["members"])) for g in groups)
    return hashlib.sha256(json.dumps(security, separators=(",", ":")).encode()).hexdigest()


def enabled_ids(chat, groups):
    if chat.group_grants is None:
        return {g["id"] for g in groups if g["kind"] == "read"}
    return set(chat.group_grants) if isinstance(chat.group_grants, list) else set()


def effective_ids(chat, groups):
    enabled = enabled_ids(chat, groups)
    return {m["id"] for g in groups if g["id"] in enabled for m in g["members"] if m["available"]}


def projection(chat, groups):
    enabled = enabled_ids(chat, groups)
    effective = effective_ids(chat, groups)
    return {"supported": True, "grant_revision": str(chat.grant_revision),
            "catalogue_revision": catalogue_revision(groups),
            "groups": [{**g, "enabled": g["id"] in enabled,
                        "editable": any(m["available"] for m in g["members"]),
                        "members": [{**{k: v for k, v in m.items() if k not in {"tools", "selectors"}},
                                     "effective": m["id"] in effective} for m in g["members"]]} for g in groups]}


def save_selection(chat, groups, selected, reauthorize=()):
    # Unknown IDs are dropped, so a later-added group is never pre-enabled by a stale or forged selection.
    known = {group["id"] for group in groups}
    chat.group_grants = [gid for gid in selected if gid in known]
    chat.grant_revision += 1
    chat.save(update_fields=["group_grants", "grant_revision"])

def authorized_chat(chat_id, user):
    """Resolve the live chat and actor boundary before using catalogue state."""
    from django.core.exceptions import PermissionDenied, ValidationError
    from django.contrib.auth import get_user_model
    from .access import can_view_chat
    from .models import AssistantChat
    if chat_id is None or user is None or not getattr(user, "is_authenticated", False) or user.pk is None:
        raise PermissionDenied("access_lost")
    try:
        actor = get_user_model()._default_manager.filter(pk=user.pk).first()
        chat = AssistantChat.objects.filter(pk=chat_id).first()
    except (ValueError, TypeError, ValidationError):
        raise PermissionDenied("access_lost")
    if (actor is None or not actor.is_authenticated or not getattr(actor, "is_active", True)
            or chat is None or chat.archived_at or not can_view_chat(actor, chat)):
        raise PermissionDenied("access_lost")
    chat._authorized_actor = actor
    if chat.owner_id == actor.pk:
        chat.owner = actor
    return chat


def _allowed(chat, user, key):
    from .hooks import is_shared_scope
    if _hook("GROUP_CATALOGUE") is None:
        return None
    chat = authorized_chat(getattr(chat, "pk", None), user)
    if is_shared_scope(chat.scope):
        return None
    groups = catalogue(chat, chat._authorized_actor)
    effective = effective_ids(chat, groups)
    return {item for group in groups for member in group["members"]
            if member["id"] in effective for item in member[key]}


def allowed_tools(chat, user):
    return _allowed(chat, user, "tools")


def allowed_selectors(chat, user):
    return _allowed(chat, user, "selectors")


def tool_authorization(chat_id, user, name):
    from django.core.exceptions import PermissionDenied
    from .hooks import tools_for_chat
    try:
        chat = authorized_chat(chat_id, user)
        allowed = allowed_tools(chat, chat._authorized_actor)
        if allowed is None:
            return True, None
        return name in allowed and any(tool.name == name for tool in tools_for_chat(chat)), None
    except PermissionDenied:
        return False, None
