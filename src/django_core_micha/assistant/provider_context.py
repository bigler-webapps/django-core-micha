"""Live authorization snapshots for provider-bound context."""
from django.db import transaction

from .hooks import _hook, is_shared_scope
from .group_grants import authorized_chat, catalogue, catalogue_revision


@transaction.atomic
def context_token(chat_id, user):
    from .catalogue_activation import groups_enabled
    if not groups_enabled():
        return None
    from .models import AssistantChat
    candidate = AssistantChat.objects.filter(pk=chat_id).first()
    if candidate is not None and not is_shared_scope(candidate.scope):
        from .authorization_snapshot import lock_authorization
        lock_authorization(getattr(user, "pk", None))
    chat = authorized_chat(chat_id, user)
    user = chat._authorized_actor
    if is_shared_scope(chat.scope):
        return None
    groups = catalogue(chat, user)
    if groups is None:
        return None
    return (chat.grant_revision, catalogue_revision(groups), groups.authorization[0])
