"""Catalogue projection and immutable per-group capability snapshots.

Execution paths must intersect their existing authorization with effective_ids().
"""
import hashlib
import json
import re
from django.db import transaction

from .hooks import _hook

_ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_POLICIES = {"direct", "confirmation_required"}


@transaction.atomic
def catalogue(chat, user):
    from .catalogue_activation import groups_enabled
    groups_enabled()
    provider = _hook("GROUP_CATALOGUE")
    if provider is None:
        return None
    from .authorization_snapshot import lock_authorization
    snapshot = lock_authorization(getattr(user, "pk", None))
    chat = authorized_chat(getattr(chat, "pk", None), user)
    user = chat._authorized_actor
    groups = validate_catalogue(provider(chat, user))
    from .catalogue_activation import verified_state, attach_generations
    state = verified_state()
    groups = attach_generations(groups, state)
    groups.authorization = snapshot
    return groups


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
    # Labels and display order are deliberately excluded; eligibility and policy are not.
    security = sorted((g["id"], g["kind"], sorted((m["id"], m["available"], m["confirmation"] or "", sorted(m["tools"]), sorted(m["selectors"]), m.get("generation"), m.get("activation_revision"))
                                                   for m in g["members"])) for g in groups)
    return hashlib.sha256(json.dumps([getattr(groups, "activation_revision", None), getattr(groups, "authorization", (None, None))[0], security], separators=(",", ":")).encode()).hexdigest()


def member_revision(group, member):
    identity = [group["id"], group["kind"], member["id"], member["confirmation"],
                sorted(member["tools"]), sorted(member["selectors"]), member.get("generation")]
    return hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()


def validate_baseline(baseline):
    if not isinstance(baseline, dict) or len(baseline) > 100:
        raise ValueError("invalid_group_baseline")
    for gid, ids in baseline.items():
        if (not isinstance(gid, str) or not _ID.fullmatch(gid) or not isinstance(ids, dict)
                or len(ids) > 100 or any(not isinstance(i, str) or not _ID.fullmatch(i)
                                           or not isinstance(rev, str) or not re.fullmatch(r"[0-9a-f]{64}", rev)
                                           for i, rev in ids.items())):
            raise ValueError("invalid_group_baseline")
    return baseline


def initial_grants(groups):
    # The cohort pair is supplied by the app, never inferred from first access.
    baseline = getattr(groups, "authorization", (None, None))[1]
    if baseline is None:
        return {}
    return {gid: {"authorization": list(baseline), "members": members}
            for gid, members in getattr(groups, "baseline", {}).items()}


@transaction.atomic
def create_chat_with_baseline(*, owner, scope, **fields):
    """Create a private chat under the app and catalogue serialization fences."""
    from django.core.exceptions import PermissionDenied
    from .catalogue_activation import attach_generations, groups_enabled, verified_state
    from .hooks import is_shared_scope
    from .models import AssistantChat

    if is_shared_scope(scope):
        return AssistantChat.objects.create(owner=owner, scope=scope, **fields)
    provider = _hook("GROUP_CATALOGUE")
    if provider is None:
        # Detect a withdrawn hook after activation; an unconfigured app stays unchanged.
        groups_enabled()
        return AssistantChat.objects.create(owner=owner, scope=scope, **fields)
    if not callable(provider):
        raise PermissionDenied("catalogue_unverified")

    from .authorization_snapshot import lock_authorization
    current, cohort = lock_authorization(owner.pk)
    state = verified_state(lock=True)
    try:
        baseline = validate_baseline(state.baseline)
    except (TypeError, ValueError) as exc:
        raise PermissionDenied("catalogue_unverified") from exc
    chat = AssistantChat.objects.create(owner=owner, scope=scope, group_grants={}, **fields)
    try:
        groups = attach_generations(validate_catalogue(provider(chat, owner)), state)
    except (TypeError, ValueError) as exc:
        raise PermissionDenied("catalogue_unverified") from exc
    if cohort is not None:
        grants = {}
        for group in groups:
            members = {member["id"]: member_revision(group, member)
                       for member in group["members"] if member["available"]
                       and baseline.get(group["id"], {}).get(member["id"]) == member_revision(group, member)}
            if members:
                grants[group["id"]] = {"authorization": list(current), "members": members}
        chat.group_grants = grants
        chat.save(update_fields=["group_grants"])
    return chat


def effective_ids(chat, groups):
    grants = chat.group_grants if chat.group_grants is not None else initial_grants(groups)
    current = getattr(groups, "authorization", (None, None))[0]
    def approved(group, member):
        entry = grants.get(group["id"])
        return (isinstance(entry, dict) and entry.get("authorization") == list(current or ())
                and isinstance(entry.get("members"), dict)
                and entry["members"].get(member["id"]) == member_revision(group, member))
    return {m["id"] for g in groups for m in g["members"]
            if m["available"] and approved(g, m)}


def projection(chat, groups):
    grants = chat.group_grants if chat.group_grants is not None else initial_grants(groups)
    effective = effective_ids(chat, groups)
    return {"supported": True, "grant_revision": str(chat.grant_revision),
            "catalogue_revision": catalogue_revision(groups),
            "groups": [{**g, "enabled": g["id"] in grants,
                        "editable": any(m["available"] for m in g["members"]),
                        "members": [{**{k: v for k, v in m.items() if k not in {"tools", "selectors", "generation"}},
                                     "effective": m["id"] in effective} for m in g["members"]]}
                       for g in groups]}


@transaction.atomic
def save_selection(chat, groups, selected, reauthorize=()):
    from django.core.exceptions import PermissionDenied
    from .authorization_snapshot import lock_authorization
    from .catalogue_activation import verified_state
    from .models import AssistantChat
    snapshot = lock_authorization(chat.owner_id)
    state = verified_state(lock=True)
    locked = AssistantChat.objects.select_for_update().get(pk=chat.pk)
    if locked.owner_id != chat.owner_id:
        raise PermissionDenied("authorization_unverified")
    fresh = catalogue(locked, locked.owner)
    if (getattr(groups, "activation_revision", None) != state.revision
            or getattr(groups, "authorization", None) != snapshot
            or catalogue_revision(groups) != catalogue_revision(fresh)
            or locked.grant_revision != chat.grant_revision):
        raise PermissionDenied("authorization_unverified")
    groups = fresh
    previous = locked.group_grants if locked.group_grants is not None else initial_grants(groups)
    by_id = {g["id"]: g for g in groups}
    if any(gid not in by_id for gid in selected) or not set(reauthorize).issubset(selected):
        raise ValueError("invalid_selection")
    if any(not any(m["available"] for m in by_id[gid]["members"]) and (gid not in previous or gid in reauthorize)
           for gid in selected):
        raise ValueError("invalid_selection")
    entries = {}
    for gid in selected:
        old = previous.get(gid)
        if gid in previous and gid not in reauthorize:
            # Retain old authorization, including stale or legacy entries; never refresh it implicitly.
            entries[gid] = old
        else:
            entries[gid] = {"authorization": list(snapshot[0]), "members": {
                m["id"]: member_revision(by_id[gid], m)
                for m in by_id[gid]["members"] if m["available"]}}
    chat.group_grants = entries
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


@transaction.atomic
def allowed_tools(chat, user):
    """Return the opted-in tool ceiling, or None for the legacy contract."""
    from .catalogue_activation import groups_enabled
    if not groups_enabled():
        return None
    from .hooks import is_shared_scope
    from .models import AssistantChat
    candidate = AssistantChat.objects.filter(pk=getattr(chat, "pk", None)).first()
    if candidate is not None and not is_shared_scope(candidate.scope):
        from .authorization_snapshot import lock_authorization
        lock_authorization(getattr(user, "pk", None))
    chat = authorized_chat(getattr(chat, "pk", None), user)
    user = chat._authorized_actor
    if is_shared_scope(chat.scope):
        return None
    groups = catalogue(chat, user)
    if groups is None:
        return None
    effective = effective_ids(chat, groups)
    return {name for group in groups for member in group["members"]
            if member["id"] in effective for name in member["tools"]}


@transaction.atomic
def allowed_selectors(chat, user):
    """Return the current app-data ceiling, or None for the legacy contract."""
    from .catalogue_activation import groups_enabled
    if not groups_enabled():
        return None
    from .hooks import is_shared_scope
    from .models import AssistantChat
    candidate = AssistantChat.objects.filter(pk=getattr(chat, "pk", None)).first()
    if candidate is not None and not is_shared_scope(candidate.scope):
        from .authorization_snapshot import lock_authorization
        lock_authorization(getattr(user, "pk", None))
    chat = authorized_chat(getattr(chat, "pk", None), user)
    user = chat._authorized_actor
    if is_shared_scope(chat.scope):
        return None
    groups = catalogue(chat, user)
    if groups is None:
        return None
    effective = effective_ids(chat, groups)
    return {selector for group in groups for member in group["members"]
            if member["id"] in effective for selector in member["selectors"]}


@transaction.atomic
def tool_authorization(chat_id, user, name):
    """Resolve current access and return a token for detecting mid-call changes."""
    from django.core.exceptions import PermissionDenied
    from .models import AssistantChat
    from .hooks import is_shared_scope
    candidate = AssistantChat.objects.filter(pk=chat_id).first()
    from .catalogue_activation import groups_enabled
    if candidate is not None and groups_enabled() and not is_shared_scope(candidate.scope):
        from .authorization_snapshot import lock_authorization
        lock_authorization(getattr(user, "pk", None))
    try:
        chat = authorized_chat(chat_id, user)
    except PermissionDenied:
        return False, None
    user = chat._authorized_actor
    from .hooks import is_shared_scope, tools_for_chat
    if is_shared_scope(chat.scope):
        return True, None
    groups = catalogue(chat, user)
    if groups is None:
        return True, None
    effective = effective_ids(chat, groups)
    allowed = (any(name in member["tools"] and member["id"] in effective
                   for group in groups for member in group["members"])
               and any(tool.name == name for tool in tools_for_chat(chat)))
    return allowed, (chat.grant_revision, catalogue_revision(groups), groups.authorization[0])
