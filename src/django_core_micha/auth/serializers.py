# django_core_micha/auth/serializers.py
from rest_framework import serializers
from .recovery import RecoveryRequest
from django.contrib.auth import get_user_model

from .security import get_user_security_state
from .roles import RolePolicy, ROLE_LEVEL_0
from .policy import (
    AUTH_FACTOR_SINGLE,
    AUTH_FACTOR_TWO,
    SELF_SIGNUP_MODES,
    SIGNUP_MODE_ACCESS_CODE,
    SIGNUP_MODE_QR,
    is_valid_signup_mode,
)
from .permissions import (
    can_manage_signup_qr,
    has_access_code_admin_rights,
    can_send_admin_invites,
    can_view_auth_policy,
    can_view_invite_admin,
    can_view_users_admin,
    can_write_auth_policy,
    can_manage_support_agents,
)

User = get_user_model()

class RecoveryRequestSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source="user.email", read_only=True)

    class Meta:
        model = RecoveryRequest
        fields = (
            "id",
            "user",
            "user_email",
            "message",
            "support_note",
            "status",
            "created_at",
            "approved_at",
            "resolved_at",
        )
        read_only_fields = (
            "user",
            "status",
            "created_at",
            "approved_at",
            "resolved_at",
        )


class RegistrationContextSerializer(serializers.Serializer):
    schema_version = serializers.CharField(required=False, default="1")
    event_ref = serializers.CharField(required=False, allow_blank=False)
    course_ref = serializers.CharField(required=False, allow_blank=False)
    group_ref = serializers.CharField(required=False, allow_blank=False)
    labels = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
    )
    metadata = serializers.JSONField(required=False)


class AuthPolicySerializer(serializers.Serializer):
    allow_admin_invite = serializers.BooleanField(required=False)
    allow_self_signup_access_code = serializers.BooleanField(required=False)
    allow_self_signup_open = serializers.BooleanField(required=False)
    allow_self_signup_email_domain = serializers.BooleanField(required=False)
    allow_self_signup_qr = serializers.BooleanField(required=False)
    allowed_email_domains = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
    )
    required_auth_factor_count = serializers.ChoiceField(
        choices=[AUTH_FACTOR_SINGLE, AUTH_FACTOR_TWO],
        required=False,
    )
    admin_required_auth_factor_count = serializers.ChoiceField(
        choices=[AUTH_FACTOR_SINGLE, AUTH_FACTOR_TWO],
        required=False,
    )
    signup_qr_expiry_days = serializers.IntegerField(
        required=False,
        min_value=1,
    )
    access_code_single_use = serializers.BooleanField(required=False)


class RegistrationConfirmSerializer(serializers.Serializer):
    """S13: payload for the confirm-pending-registration endpoint."""

    token = serializers.CharField()
    password = serializers.CharField(min_length=8, max_length=256, trim_whitespace=False)


class RegistrationRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()
    mode = serializers.ChoiceField(choices=list(SELF_SIGNUP_MODES))
    access_code = serializers.CharField(required=False, allow_blank=False)
    registration_context_token = serializers.CharField(required=False, allow_blank=False)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        mode = attrs.get("mode")

        if not is_valid_signup_mode(mode):
            raise serializers.ValidationError({"mode": "Invalid signup mode."})

        if mode == SIGNUP_MODE_ACCESS_CODE and not attrs.get("access_code"):
            raise serializers.ValidationError({"access_code": "Access code required."})

        if mode == SIGNUP_MODE_QR and not attrs.get("registration_context_token"):
            raise serializers.ValidationError(
                {
                    "registration_context_token": (
                        "QR signup requires a registration context token."
                    )
                }
            )

        return attrs


class SignupQrCreateSerializer(serializers.Serializer):
    label = serializers.CharField(required=False, allow_blank=True)
    expires_minutes = serializers.IntegerField(
        required=False,
        min_value=1,
    )
    max_redemptions = serializers.IntegerField(
        required=False,
        min_value=1,
    )
    registration_context = RegistrationContextSerializer(required=False)



class BaseUserSerializer(serializers.ModelSerializer):
    """
    Standard Serializer für User + Profile.
    Flattened Profil-Felder (role, language, etc.) in die Top-Level-Ansicht.
    """
    # --- Profile Fields (Read/Write) ---
    role = serializers.CharField(source="profile.role", required=False)
    language = serializers.CharField(source="profile.language", required=False)
    is_new = serializers.BooleanField(source="profile.is_new", required=False)
    is_invited = serializers.BooleanField(source="profile.is_invited", required=False)
    
    accepted_privacy_statement = serializers.BooleanField(
        source="profile.accepted_privacy_statement", required=False
    )
    accepted_convenience_cookies = serializers.BooleanField(
        source="profile.accepted_convenience_cookies", required=False
    )
    is_support_agent = serializers.BooleanField(
        source="profile.is_support_agent",
        required=False,
    )
    # Support Contact (Optional)
    support_contact_id = serializers.PrimaryKeyRelatedField(
        source="profile.support_contact",
        queryset=User.objects.all(),
        allow_null=True,
        required=False,
    )

    # --- Computed Fields (Read Only) ---
    security_state = serializers.SerializerMethodField()
    can_manage = serializers.SerializerMethodField()
    can_manage_support_agents = serializers.SerializerMethodField()

    ui_permissions = serializers.SerializerMethodField()
    available_roles = serializers.SerializerMethodField()
    successful_login = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id", "email", "username", "first_name", "last_name", 
            "is_superuser", "is_active", "last_login", "date_joined",
            "successful_login",
            # Profil-Felder
            "role", "language", "is_new", "is_invited",
            "accepted_privacy_statement", "accepted_convenience_cookies",
            "support_contact_id", "is_support_agent",
            # Computed
            "security_state", "can_manage", "can_manage_support_agents",
            "ui_permissions", "available_roles",
        ]
        read_only_fields = ["email", "username", "last_login", "date_joined", "is_superuser"]

    # --- Method Fields ---

    def get_security_state(self, obj):
        request = self.context.get("request")
        return get_user_security_state(obj, request=request)

    def get_can_manage(self, obj) -> bool:
        """
        Darf der Request-User den Ziel-User (obj) bearbeiten?
        """
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return False
        
        # Superuser darf alles
        if request.user.is_superuser:
            return True

        policy = RolePolicy()
        # Darf ich die Rolle des Ziels ändern? Wenn ja, darf ich ihn generell managen.
        # Wir nutzen hier 'role' des Ziels, oder default fallback.
        target_role = getattr(obj.profile, 'role', 'none')
        
        # Check: Habe ich höhere Rechte als das Ziel?
        return policy.can_change_role(request.user, obj, target_role)

    def get_can_manage_support_agents(self, obj) -> bool:
        """
        Darf der aktuelle User Support-Agents verwalten?
        Delegiert komplett an den zentralen Helper.
        """
        request = self.context.get("request")
        user = getattr(request, "user", None)
        return can_manage_support_agents(user, request=request)

    def get_successful_login(self, obj) -> bool:
        return bool(getattr(obj, "last_login", None))

    def validate(self, attrs):
        attrs = super().validate(attrs)

        request = self.context.get("request")
        acting_user = getattr(request, "user", None)
        if not acting_user or not acting_user.is_authenticated:
            return attrs

        target_user = self.instance or acting_user
        profile_data = attrs.get("profile", {}) or {}

        if "role" in profile_data:
            policy = RolePolicy()
            requested_role = profile_data.get("role")
            if not policy.can_change_role(acting_user, target_user, requested_role):
                raise serializers.ValidationError({"role": "Permission denied."})

        if any(key in profile_data for key in ("is_support_agent", "support_contact")):
            if not can_manage_support_agents(acting_user, request=request):
                raise serializers.ValidationError(
                    {"is_support_agent": "Permission denied."}
                )

        return attrs

    # --- Update Logic (Critical for Nested Fields) ---

    def update(self, instance, validated_data):
        """
        Überschreibt Standard-Update, um Profil-Felder (source='profile.xyz')
        korrekt im Profil-Modell zu speichern.
        """
        # 1. Profil-Daten extrahieren (DRF packt source='profile.x' in ein verschachteltes Dict)
        profile_data = validated_data.pop("profile", {})

        # 2. User-Felder updaten
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        # 3. Profil-Felder updaten
        if hasattr(instance, 'profile'):
            profile = instance.profile
            for attr, value in profile_data.items():
                setattr(profile, attr, value)
            profile.save()

        return instance
    
    def get_ui_permissions(self, obj) -> dict:
        """
        Liefert die UI-Permissions des *aktuellen* Request-Users.
        Diese Flags steuern Tabs wie Users / Invite / Access Codes / Support.
        """
        request = self.context.get("request")
        user = getattr(request, "user", None)

        if not user or not user.is_authenticated:
            return {
                "can_view_users": False,
                "can_invite": False,
                "can_view_invite": False,
                "can_view_auth_policy": False,
                "can_write_auth_policy": False,
                "can_send_invites": False,
                "can_manage_access_codes": False,
                "can_manage_signup_qr": False,
                "can_view_support": False,
            }

        profile = getattr(user, "profile", None)
        is_support_agent = bool(getattr(profile, "is_support_agent", False))

        return {
            "can_view_users": can_view_users_admin(user, request=request),
            "can_invite": can_view_invite_admin(user, request=request),
            "can_view_invite": can_view_invite_admin(user, request=request),
            "can_view_auth_policy": can_view_auth_policy(user, request=request),
            "can_write_auth_policy": can_write_auth_policy(user, request=request),
            "can_send_invites": can_send_admin_invites(user, request=request),
            "can_manage_access_codes": has_access_code_admin_rights(user, request=request),
            "can_manage_signup_qr": can_manage_signup_qr(user, request=request),
            # Support-UI: Support-Agent ODER jemand, der Support-Agent-Rollen verwalten darf
            "can_view_support": is_support_agent or can_manage_support_agents(user, request=request),
        }
    
    def get_available_roles(self, obj) -> list[str]:
        """
        Liefert eine Liste von Rollencodes, die der aktuelle Request-User
        prinzipiell vergeben darf (für UI-Dropdowns).
        """
        request = self.context.get("request")
        acting = getattr(request, "user", None)

        if not acting or not acting.is_authenticated:
            return []

        policy = RolePolicy()
        role_defs = policy.role_definitions()

        # Superuser: alle Rollen
        if acting.is_superuser:
            return list(role_defs.keys())

        acting_level = policy.get_user_level(acting)
        available = []

        for code, info in role_defs.items():
            level = int(info.get("level", ROLE_LEVEL_0))
            if level <= acting_level:
                available.append(code)

        return available
