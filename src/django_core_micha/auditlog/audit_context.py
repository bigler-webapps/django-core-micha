from contextvars import ContextVar

_audit_actor_id: ContextVar[int | None] = ContextVar("auditlog_actor_id", default=None)
_audit_request_id: ContextVar[str | None] = ContextVar("auditlog_request_id", default=None)


def set_current_actor(user):
    actor_id = None
    if user is not None and getattr(user, "is_authenticated", False):
        actor_id = user.pk
    return _audit_actor_id.set(actor_id)


def reset_current_actor(token):
    _audit_actor_id.reset(token)


def get_current_actor_id():
    return _audit_actor_id.get()


def set_current_request_id(request_id):
    return _audit_request_id.set(request_id)


def reset_current_request_id(token):
    _audit_request_id.reset(token)


def get_current_request_id():
    return _audit_request_id.get()
