from uuid import uuid4

from .audit_context import (
    reset_current_actor,
    reset_current_request_id,
    set_current_actor,
    set_current_request_id,
)


class AuditlogActorMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        actor_token = set_current_actor(getattr(request, "user", None))
        request_id = request.headers.get("X-Request-ID") or uuid4().hex
        request_token = set_current_request_id(request_id)

        try:
            response = self.get_response(request)
        finally:
            reset_current_request_id(request_token)
            reset_current_actor(actor_token)

        return response
