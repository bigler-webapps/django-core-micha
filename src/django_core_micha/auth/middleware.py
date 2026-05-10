# django_core_micha/auth/middleware.py
from .security import get_security_level

class AuthLevelMiddleware:
    """
    Attaches auth_level to the request for convenience.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.auth_level = get_security_level(request)
        return self.get_response(request)
