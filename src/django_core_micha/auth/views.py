# src/django_core_micha/auth/views.py
from django.shortcuts import get_object_or_404, redirect
from django.contrib.auth import get_user_model
from django.conf import settings
from django.core import signing
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.http import JsonResponse

from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied, ValidationError
from rest_framework.throttling import AnonRateThrottle, ScopedRateThrottle

from allauth.mfa.models import Authenticator
from allauth.account.models import EmailAddress

from django_core_micha.invitations.mixins import InviteActionsMixin
from django_core_micha.invitations.access_codes import validate_access_code_or_error
from django_core_micha.invitations.emails import send_invite_or_reset_email
from django_core_micha.auth.roles import RolePolicy
from django_core_micha.auth import services  # <--- Central Logic Import
from django_core_micha.auth.methods import get_auth_methods
from django_core_micha.auth.permissions import (
    can_manage_signup_qr,
    can_view_auth_policy,
    can_view_users_admin,
    can_write_auth_policy,
)
from django_core_micha.auth.policy import (
    SIGNUP_MODE_QR,
    create_signup_context_token,
    decode_signup_context_token,
    get_or_create_auth_policy,
    get_policy_state,
    is_allowed_email_domain,
    mode_requires_access_code,
    mode_requires_email_domain,
    mode_requires_qr_token,
    serialize_policy,
)
from .recovery import RecoveryRequest
from .serializers import (
    AuthPolicySerializer,
    RecoveryRequestSerializer,
    RegistrationRequestSerializer,
    SignupQrCreateSerializer,
)
from .permissions import IsSupportAgent, IsAssignedSupportOrAdmin

User = get_user_model()

# --- Standard Views ---

def recovery_complete_view(request, token: str):
    """
    Entry point from the email link. Redirects to frontend.
    """
    target_base = f"{settings.PUBLIC_ORIGIN}/login"
    try:
        rr = RecoveryRequest.objects.select_related("user").get(token=token)
    except RecoveryRequest.DoesNotExist:
        return redirect(f"{target_base}#recovery=invalid")

    if not rr.is_active():
        return redirect(f"{target_base}#recovery=expired")

    return redirect(f"{target_base}#recovery={rr.token}")


@ensure_csrf_cookie
def csrf_token_view(request):
    """
    Hilfs-View für SPAs. Erzwingt das Setzen des CSRF-Cookies,
    damit das Frontend den Token auslesen und im Header mitsenden kann.
    """
    return JsonResponse({"detail": "CSRF cookie set"})


def auth_methods_view(request):
    """
    Public endpoint for frontend capability toggles.
    Returns effective auth methods derived from project settings.
    """
    if request.method != "GET":
        return JsonResponse({"detail": "Method not allowed."}, status=405)
    return JsonResponse(get_auth_methods())

# --- API ViewSets ---

class BaseUserViewSet(InviteActionsMixin, viewsets.ModelViewSet):
    queryset = User.objects.all()
    serializer_class = None 
    permission_classes = [IsAuthenticated]
    throttle_scope = None
    role_policy_class = RolePolicy
    current_patch_allowed_fields = frozenset(
        {
            "first_name",
            "last_name",
            "language",
            "accepted_privacy_statement",
            "accepted_convenience_cookies",
        }
    )

    def get_auth_policy(self):
        return get_or_create_auth_policy()

    def _ensure_auth_policy_view(self, request):
        user = getattr(request, "user", None)
        if not can_view_auth_policy(user, request=request):
            raise PermissionDenied("Permission denied.")

    def _ensure_auth_policy_write(self, request):
        user = getattr(request, "user", None)
        if not can_write_auth_policy(user, request=request):
            raise PermissionDenied("Permission denied.")

    def _ensure_signup_qr_admin(self, request):
        user = getattr(request, "user", None)
        if not can_manage_signup_qr(user, request=request):
            raise PermissionDenied("Permission denied.")

    def get_queryset(self):
        queryset = self.queryset
        if hasattr(queryset, "all"):
            queryset = queryset.all()

        user = getattr(self.request, "user", None)
        if not user or not user.is_authenticated:
            return User.objects.none()
        if can_view_users_admin(user, request=self.request):
            return queryset
        return queryset.filter(pk=user.pk)

    def _upsert_user_for_registration(self, email: str):
        email = email.lower()
        user_obj, created = User.objects.get_or_create(
            email__iexact=email,
            defaults={"username": email, "email": email},
        )
        EmailAddress.objects.get_or_create(
            user=user_obj,
            email__iexact=email,
            defaults={"email": email, "verified": False, "primary": True},
        )
        self._mark_invited_profile(user_obj, created=created)
        return user_obj, created

    def apply_registration_context(self, user, registration_context: dict | None):
        """
        Hook for project apps. Phase A keeps this as a no-op so consumers can
        add app-specific membership logic later without changing the core contract.
        """
        return None

    def get_role_policy(self):
        return self.role_policy_class()

    def get_throttles(self):
        action = getattr(self, "action", None)
        if action == "mfa_support_help":
            self.throttle_scope = "mfa_support_help"
        else:
            self.throttle_scope = None
        return super().get_throttles()

    @action(detail=False, methods=["get", "patch"], url_path="current")
    def current(self, request):
        user = request.user
        if request.method == "GET":
            serializer = self.get_serializer(user)
            return Response(serializer.data)

        incoming_keys = set(request.data.keys())
        disallowed = sorted(incoming_keys - self.current_patch_allowed_fields)
        if disallowed:
            raise ValidationError(
                {
                    "non_field_errors": [
                        "Only safe profile fields can be changed on this endpoint."
                    ],
                    "disallowed_fields": disallowed,
                }
            )

        serializer = self.get_serializer(user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    @action(
        detail=False,
        methods=["get", "patch"],
        url_path="auth-policy",
    )
    def auth_policy(self, request):
        if request.method == "GET":
            self._ensure_auth_policy_view(request)
        else:
            self._ensure_auth_policy_write(request)
        policy = self.get_auth_policy()
        if policy is None:
            return Response({"detail": "Auth policy not configured."}, status=404)

        if request.method == "GET":
            return Response(serialize_policy(policy))

        serializer = AuthPolicySerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        for field, value in serializer.validated_data.items():
            setattr(policy, field, value)
        policy.save()
        return Response(serialize_policy(policy))

    @action(
        detail=False,
        methods=["post"],
        permission_classes=[AllowAny],
        url_path="register-request",
    )
    def register_request(self, request):
        serializer = RegistrationRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        email = data["email"].lower()
        mode = data["mode"]
        policy_state = get_policy_state(self.get_auth_policy())

        if mode not in policy_state.signup_modes:
            return Response(
                {"code": "Auth.SELF_SIGNUP_DISABLED"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if mode_requires_access_code(mode):
            validate_access_code_or_error(
                data.get("access_code"),
                consume=getattr(settings, "ACCESS_CODE_SINGLE_USE", False),
            )

        if mode_requires_email_domain(mode) and not is_allowed_email_domain(
            email, policy_state.allowed_email_domains
        ):
            return Response(
                {"code": "Auth.EMAIL_DOMAIN_NOT_ALLOWED"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        has_registration_context_token = bool(data.get("registration_context_token"))
        registration_context = {}
        if has_registration_context_token:
            try:
                payload = decode_signup_context_token(data["registration_context_token"])
            except signing.SignatureExpired:
                return Response(
                    {"code": "Auth.SIGNUP_QR_EXPIRED"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            except signing.BadSignature:
                return Response(
                    {"code": "Auth.SIGNUP_QR_INVALID"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            registration_context = payload.get("registration_context") or {}
            if payload.get("mode") != SIGNUP_MODE_QR:
                return Response(
                    {"code": "Auth.SIGNUP_QR_INVALID"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        if mode_requires_qr_token(mode) and not has_registration_context_token:
            return Response(
                {"code": "Auth.SIGNUP_QR_INVALID"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user_obj, created = self._upsert_user_for_registration(email)
        self.apply_registration_context(user_obj, registration_context)

        url = self._build_frontend_url(request, user_obj, is_new_user=True)
        send_invite_or_reset_email(user=user_obj, url=url, is_new_user=True)

        return Response(
            {
                "code": "Auth.INVITE_SENT",
                "email": email,
                "created": created,
                "mode": mode,
            },
            status=status.HTTP_201_CREATED,
        )

    @action(
        detail=False,
        methods=["post"],
        url_path="signup-qr",
    )
    def signup_qr(self, request):
        self._ensure_signup_qr_admin(request)
        policy_state = get_policy_state(self.get_auth_policy())
        if not policy_state.allow_self_signup_qr:
            return Response({"code": "Auth.SIGNUP_QR_DISABLED"}, status=400)

        serializer = SignupQrCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        token, expires_at = create_signup_context_token(
            registration_context=payload.get("registration_context"),
            label=payload.get("label", ""),
            expires_minutes=payload.get("expires_minutes"),
            policy=self.get_auth_policy(),
        )
        signup_url = f"{request.build_absolute_uri('/').rstrip('/')}/signup?rt={token}"
        return Response(
            {
                "signup_url": signup_url,
                "registration_context_token": token,
                "expires_at": expires_at,
                "preview_context": payload.get("registration_context") or {},
            }
        )

    @action(detail=True, methods=["patch"], url_path="update-role")
    def update_role(self, request, pk=None):
        user = self.get_object()
        new_role = request.data.get("role")
        policy = self.get_role_policy()

        if not policy.is_valid_code(new_role):
            return Response({"detail": "Invalid role."}, status=400)

        if not policy.can_change_role(request.user, user, new_role):
            return Response({"detail": "Permission denied."}, status=403)

        user.profile.role = new_role
        user.profile.save()
        return Response({"detail": "Role updated successfully."})

    MFA_SUPPORT_MESSAGE_MAX_LENGTH = 2000

    @action(
        detail=False,
        methods=["post"],
        permission_classes=[AllowAny],
        throttle_classes=[ScopedRateThrottle, AnonRateThrottle],
        url_path="mfa/support-help",
    )
    def mfa_support_help(self, request):
        identifier = request.data.get("email") or request.data.get("identifier")
        raw_message = request.data.get("message", "") or ""

        if not identifier:
            return Response({"code": "Auth.MFA_IDENTIFIER_REQUIRED"}, status=400)

        if not isinstance(raw_message, str):
            return Response({"code": "Auth.MFA_MESSAGE_INVALID"}, status=400)

        if len(raw_message) > self.MFA_SUPPORT_MESSAGE_MAX_LENGTH:
            return Response(
                {"code": "Auth.MFA_MESSAGE_TOO_LONG"},
                status=400,
            )

        # Strip any HTML tags so the message is rendered as plain text in support UI / e-mail.
        # Defense-in-depth — the rendering layer must escape too, but this prevents
        # tag content from ever entering the DB.
        from django.utils.html import strip_tags
        message = strip_tags(raw_message)

        try:
            user = User.objects.get(email__iexact=identifier)
            RecoveryRequest.objects.create(user=user, message=message)
        except User.DoesNotExist:
            pass # Silent fail — preserves enumeration resistance.

        return Response({"code": "Auth.MFA_HELP_REQUESTED"}, status=200)


class PasskeyViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    def list(self, request):
        qs = Authenticator.objects.filter(user=request.user, type=Authenticator.Type.WEBAUTHN)
        data = []
        for a in qs:
            wrapped = a.wrap()
            data.append({
                "id": a.pk,
                "name": getattr(wrapped, "name", None),
                "created_at": a.created_at,
                "last_used_at": a.last_used_at,
                "is_device_passkey": getattr(wrapped, "is_device_passkey", None),
            })
        return Response(data)

    def destroy(self, request, pk=None):
        obj = get_object_or_404(Authenticator, pk=pk, user=request.user, type=Authenticator.Type.WEBAUTHN)
        obj.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class RecoveryRequestViewSet(viewsets.ModelViewSet):
    queryset = RecoveryRequest.objects.all().select_related("user", "resolved_by")
    serializer_class = RecoveryRequestSerializer
    throttle_scope = None

    def get_throttles(self):
        if getattr(self, "action", None) == "recovery_login":
            self.throttle_scope = "recovery_login"
        else:
            self.throttle_scope = None
        return super().get_throttles()

    def get_permissions(self):
        if self.action == "recovery_login":
            return [AllowAny()]
        if self.action == "create_from_mfa":
            return [IsAuthenticated()]
        if self.action in ("list", "retrieve"):
            return [IsSupportAgent()]
        if self.action in ("approve", "reject"):
            return [IsAssignedSupportOrAdmin()]
        return super().get_permissions()

    def get_queryset(self):
        qs = super().get_queryset()
        status_param = self.request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)
        return qs

    @action(methods=["post"], detail=True)
    def approve(self, request, pk=None):
        rr = self.get_object()
        # Permission check via get_permissions handles the class check, 
        # but specific object permission is checked by DRF automatically in many cases.
        # However, since we use custom logic in permissions.py, calling it explicitly is safer if not using standard DRF flow.
        self.check_object_permissions(request, rr)

        support_note = request.data.get("support_note", "")
        
        # Call Service
        recovery_url = services.approve_recovery_request(request, rr, support_note)

        serializer = self.get_serializer(rr)
        data = serializer.data
        data["recovery_link"] = recovery_url
        return Response(data)

    @action(methods=["post"], detail=True)
    def reject(self, request, pk=None):
        rr = self.get_object()
        self.check_object_permissions(request, rr)

        support_note = request.data.get("support_note", "")
        
        # Call Service
        services.reject_recovery_request(request, rr, support_note)
        
        serializer = self.get_serializer(rr)
        return Response(serializer.data)

    @action(
        methods=["post"],
        detail=False,
        permission_classes=[AllowAny],
        throttle_classes=[ScopedRateThrottle, AnonRateThrottle],
        url_path=r"recovery-login/(?P<token>[^/.]+)",
    )
    def recovery_login(self, request, token=None):
        identifier = request.data.get("email") or request.data.get("identifier")
        password = request.data.get("password")

        if not identifier or not password:
            return Response({"code": "Auth.CREDENTIALS_REQUIRED"}, status=400)

        try:
            # Call Service
            services.perform_recovery_login(request, identifier, password, token)
        except AuthenticationFailed as e:
            # Use the code from the service exception
            return Response({"code": e.detail.code}, status=400)

        return Response({"status": 200, "code": "Auth.RECOVERY_LOGIN_OK"}, status=200)
