# webapp_management/invitations/views.py

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from rest_framework.throttling import ScopedRateThrottle, AnonRateThrottle

from allauth.account.models import EmailAddress

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.http import Http404
from django.conf import settings


from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView
from rest_framework.response import Response

User = get_user_model()

# django_core_micha/invitations/viewsets.py

from django_core_micha.auth.permissions import IsAccessCodeAdminOrSuperuser
from django_core_micha.auth.signals import registration_completed
from django_core_micha.auth.throttles import PerAccessCodeScopedRateThrottle
from .models import AccessCode
from .serializers import AccessCodeSerializer
from .conf import ACCESS_CODE_REGISTRATION_ENABLED
from .access_codes import validate_access_code_or_error

ACCESS_CODE_REGISTRATION_ENABLED = getattr(
    settings,
    "ACCESS_CODE_REGISTRATION_ENABLED",
    False,
)

# django_core_micha/invitations/viewsets.py




class AccessCodeViewSet(viewsets.ModelViewSet):
    queryset = AccessCode.objects.all().order_by("-created_at")
    serializer_class = AccessCodeSerializer
    # Standard: alle „normalen“ Actions nur für Access-Code-Admins
    permission_classes = [IsAccessCodeAdminOrSuperuser]

    def get_permissions(self):
        # validate: öffentlich, ohne Login
        if self.action == "validate":
            return [AllowAny()]
        return super().get_permissions()
    
    def get_throttles(self):
        # Only the 'validate' action uses a scoped throttle.
        if getattr(self, "action", None) == "validate":
            self.throttle_scope = "access_code_validate"
        else:
            self.throttle_scope = None
        throttles = super().get_throttles()

        # S52: per-access-code throttle on /validate. The per-IP scope
        # (access_code_validate: 100/hour) leaves a 1-IP attacker 100 probes
        # per code per hour; with proxy rotation that becomes effectively
        # unlimited. Hashing on the code itself caps probing per-code.
        if getattr(self, "action", None) == "validate":
            per_code = PerAccessCodeScopedRateThrottle()
            per_code.scope = "access_code_probe"
            throttles = list(throttles) + [per_code]
        return throttles

    @action(
        detail=False,
        methods=["post"],
        url_path="validate",
        permission_classes=[AllowAny],
        authentication_classes=[],
    )
    def validate(self, request):
        code = request.data.get("code")
        try:
            validate_access_code_or_error(code, consume=False)
        except ValidationError as exc:
            return Response({"code": exc.detail.get("code")}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"valid": True}, status=status.HTTP_200_OK)

    @action(
        detail=False,
        methods=["post"],
        url_path="set",
    )
    def set(self, request):
        """
        Admin-only endpoint to create or update an access code.

        POST {"code": "ABC123", "is_active": true}
        """
        # Zugriff ist schon über IsAccessCodeAdminOrSuperuser abgesichert
        code = (request.data.get("code") or "").strip()
        is_active = bool(request.data.get("is_active", True))

        if not code:
            return Response(
                {"code": "Auth.ACCESS_CODE_REQUIRED"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        obj, created = AccessCode.objects.update_or_create(
            code=code,
            defaults={
                "is_active": is_active,
                "created_by": request.user,
            },
        )
        serializer = self.get_serializer(obj)
        return Response(
            {
                "code": "Auth.ACCESS_CODE_CREATED" if created else "Auth.ACCESS_CODE_UPDATED",
                "access_code": serializer.data,
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )
        


class PasswordResetConfirmView(APIView):
    throttle_classes = [ScopedRateThrottle, AnonRateThrottle]
    throttle_scope = "password_reset"

    permission_classes = [AllowAny]

    # Optional: Codes als Felder, wenn du die Struktur behalten möchtest
    link_valid_code = "Auth.RESET_LINK_VALID"
    link_invalid_code = "Auth.RESET_LINK_INVALID"
    missing_password_code = "Auth.RESET_PASSWORD_REQUIRED"
    password_invalid_code = "Auth.RESET_PASSWORD_INVALID"
    success_code = "Auth.RESET_PASSWORD_SUCCESS"
    def get_user_from_uid(self, uidb64):
        try:
            uid = force_str(urlsafe_base64_decode(uidb64))
            return User.objects.get(pk=uid)
        except Exception:
            return None

    def get(self, request, uidb64, token, *args, **kwargs):
        user = self.get_user_from_uid(uidb64)
        if user and default_token_generator.check_token(user, token):
            return Response({"code": self.link_valid_code})
        return Response(
            {"code": self.link_invalid_code},
            status=status.HTTP_400_BAD_REQUEST,
        )

    def post(self, request, uidb64, token, *args, **kwargs):
        new_pw = request.data.get("new_password")
        if not new_pw:
            return Response(
                {"code": self.missing_password_code},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = self.get_user_from_uid(uidb64)
        if not user:
            return Response(
                {"code": self.link_invalid_code},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not default_token_generator.check_token(user, token):
            return Response(
                {"code": self.link_invalid_code},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            validate_password(new_pw, user=user)
        except ValidationError as exc:
            return Response(
                {
                    "code": self.password_invalid_code,
                    "messages": exc.messages,  # optional, kann auch weggelassen werden
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.set_password(new_pw)
        user.save()

        # allauth EmailAddress sicherstellen + als verifiziert markieren
        # (User hat Zugang zur Email bewiesen durch den Token-Link)
        email_obj, _created = EmailAddress.objects.get_or_create(
            user=user,
            email__iexact=user.email,
            defaults={"email": user.email.lower(), "primary": True},
        )
        if not email_obj.verified:
            email_obj.verified = True
            email_obj.save(update_fields=["verified"])

        registration_completed.send(
            sender=self.__class__,
            user=user,
            request=request,
        )

        return Response({"code": self.success_code})
