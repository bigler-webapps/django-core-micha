from .hooks import can_access_scope, is_shared_scope

def is_creator(user, chat):
    return chat.owner_id == getattr(user, "pk", None)

def can_view_chat(user, chat):
    try:
        return bool(can_access_scope(user, chat.scope) and (is_creator(user, chat) or is_shared_scope(chat.scope)))
    except Exception:
        return False
