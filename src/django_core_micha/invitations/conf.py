# z.B. django_core_micha/invitations/conf.py
from django.conf import settings

ACCESS_CODE_REGISTRATION_ENABLED = getattr(
    settings,
    "ACCESS_CODE_REGISTRATION_ENABLED",
    False,  # Default: Feature aus
)
