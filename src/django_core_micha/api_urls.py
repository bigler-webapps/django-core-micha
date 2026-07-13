# django_core_micha/api_urls.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter

# Views
from django_core_micha.invitations.views import PasswordResetConfirmView
from django_core_micha.invitations import urls as invitations_urls
from django_core_micha.notifications import urls as notifications_urls
from django_core_micha.onboarding import urls as onboarding_urls
from django_core_micha.auth.views import (
    csrf_token_view,
    auth_methods_view,
    RecoveryRequestViewSet,
    recovery_complete_view,
    recovery_session_token_view,
)
from django_core_micha.health.views import healthz_view

router = DefaultRouter()
router.register(
    r"recovery-requests",
    RecoveryRequestViewSet,
    basename="recovery-request",
)

urlpatterns = [
    # Health probe (public, no auth) — used by Uptime Kuma and the
    # staging-health PR gate. Checks DB + cache.
    path("healthz", healthz_view, name="healthz"),

    # Allauth Headless Endpoints (Login, Signup, MFA, etc.)
    # URLs sind dann z.B. /api/auth/login, /api/auth/signup
    path("auth/", include("allauth.headless.urls")),
    # OAuth provider callback/login URLs required by allauth social providers.
    # Mounted under /api/accounts/* so they work in SPA setups behind /api proxy.
    path("accounts/", include("allauth.urls")),
    path("accounts/mfa/", include("allauth.mfa.urls")),
    
    # Hilfs-Endpoint für CSRF (wichtig für SPA)
    path("csrf/", csrf_token_view, name="csrf-token"),
    path("auth-methods/", auth_methods_view, name="auth-methods"),
    
    # Support-APIs (Recovery Requests verwalten)
    path("support/", include(router.urls)),
    
    # Recovery Abschluss (User klickt auf Link)
    path(
        "mfa/recovery/<str:token>/",
        recovery_complete_view,
        name="mfa-recovery-complete",
    ),

    # S164: one-shot session handoff. After `recovery_complete_view` parks
    # the plaintext token in the user's server session and redirects to
    # `/login#recovery=ok`, the SPA pulls the token back from here and
    # submits it in the `/recovery-login` POST body. Pops on every read.
    path(
        "auth/recovery/session-token/",
        recovery_session_token_view,
        name="mfa-recovery-session-token",
    ),

    # Password Reset Confirm (User klickt auf E-Mail Link)
    path(
        "users/password-reset/<uidb64>/<token>/", 
        PasswordResetConfirmView.as_view(), 
        name="password-reset-api"
    ),

    # Access-Code-API (Einladungen)
    # Hängt AccessCodeViewSet unter /access-codes/ ein
    path("", include(invitations_urls)),
    path("notifications/", include(notifications_urls)),
    path("onboarding/", include(onboarding_urls)),
]
