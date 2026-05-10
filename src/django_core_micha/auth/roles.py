# src/django_core_micha/auth/roles.py
# Abstrakte Stufen – nur für Vergleiche relevant
ROLE_LEVEL_0 = 0  # minimal / guest
ROLE_LEVEL_1 = 1  # normal user
ROLE_LEVEL_2 = 2  # elevated / manager
ROLE_LEVEL_3 = 3  # owner / org admin

ROLE_LEVELS = (ROLE_LEVEL_0, ROLE_LEVEL_1, ROLE_LEVEL_2, ROLE_LEVEL_3)

from typing import Mapping, Dict, Any
from django.conf import settings

# Fallback, falls ein Projekt nichts definiert
DEFAULT_ROLE_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "user": {"level": ROLE_LEVEL_1, "label": "User"},
}
DEFAULT_ROLE_CODE = "user"


def get_role_definitions() -> Mapping[str, Dict[str, Any]]:
    """
    Returns ROLE_DEFINITIONS from project settings or a minimal default.
    Expected structure per project:
        ROLE_DEFINITIONS = {
            "none": {"level": 0, "label": "None"},
            "member": {"level": 1, "label": "Member"},
            "manager": {"level": 2, "label": "Manager"},
            "owner": {"level": 3, "label": "Owner"},
        }
    """
    return getattr(settings, "ROLE_DEFINITIONS", DEFAULT_ROLE_DEFINITIONS)


def get_default_role_code() -> str:
    return getattr(settings, "DEFAULT_ROLE_CODE", DEFAULT_ROLE_CODE)


def get_role_code_for_user(user) -> str:
    """
    Returns the role code stored on the user's profile or the project default.
    """
    profile = getattr(user, "profile", None)
    code = getattr(profile, "role", None)
    if code:
        return code
    return get_default_role_code()


def get_role_level_for_code(code: str) -> int:
    """
    Looks up the numeric level for a given role code.
    Unknown codes fall back to the lowest level (0).
    """
    defs = get_role_definitions()
    info = defs.get(code)
    if not info:
        return ROLE_LEVEL_0
    return int(info.get("level", ROLE_LEVEL_0))


def get_role_level_for_user(user) -> int:
    """
    Returns the numeric level for the given user's current role.
    """
    code = get_role_code_for_user(user)
    return get_role_level_for_code(code)


class RolePolicy:
    """
    Default policy for role changes and simple 'admin-like' checks.
    Project-specific policies can subclass and override methods.
    """

    def role_definitions(self) -> Mapping[str, Dict[str, Any]]:
        return get_role_definitions()

    def is_valid_code(self, code: str) -> bool:
        return code in self.role_definitions()

    def get_user_level(self, user) -> int:
        return get_role_level_for_user(user)

    def can_change_role(self, acting_user, target_user, new_code: str) -> bool:
        """
        Default:
        - Rolle muss valide sein (auch für Superuser!)
        - superuser darf alles
        - sonst: acting_level >= level(new_role)
                 und acting_level > level(target)
        """
        if not acting_user or not acting_user.is_authenticated:
            return False

        # 1. Zuerst: Existiert die Rolle überhaupt? (Schutz vor Datenmüll)
        if not self.is_valid_code(new_code):
            return False

        # 2. Dann: Superuser darf alles Validieren
        if acting_user.is_superuser:
            return True

        # 3. Logik für normale User
        acting_level = self.get_user_level(acting_user)
        target_level = self.get_user_level(target_user)
        new_level = get_role_level_for_code(new_code)

        if acting_level < new_level:
            return False
        if acting_level <= target_level:
            return False

        return True

    def is_admin_like(self, user) -> bool:
        """
        Example helper: consider 'admin-like' as level >= 2, plus superuser.
        Projects can override this by subclassing RolePolicy.
        """
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser:
            return True
        return self.get_user_level(user) >= ROLE_LEVEL_2
    
def get_role_choices() -> list[tuple[str, str]]:
    """
    Wandelt die ROLE_DEFINITIONS in eine Liste für Django models.choices um.
    Output: [('admin', 'Admin'), ('teacher', 'Teacher'), ...]
    """
    defs = get_role_definitions() # Nutzt deine existierende Funktion
    return [(code, info.get("label", code)) for code, info in defs.items()]