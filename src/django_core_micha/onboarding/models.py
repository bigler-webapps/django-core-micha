from django.conf import settings
from django.db import models


UNIVERSAL_STEP_KEYS = ["cookie_consent", "complete_name", "browser_push", "pwa_install"]


class OnboardingStepConfig(models.Model):
    key = models.SlugField(unique=True)
    enabled = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "key"]

    def __str__(self):
        return f"{self.key} [{'on' if self.enabled else 'off'}]"


def get_registered_step_keys() -> list[str]:
    """Return universal and project-provided keys, preserving first occurrence."""
    configured = getattr(settings, "ONBOARDING_EXTRA_STEP_KEYS", [])
    extras = configured if isinstance(configured, (list, tuple)) else []
    return list(dict.fromkeys([*UNIVERSAL_STEP_KEYS, *[key for key in extras if isinstance(key, str) and key]]))


def get_step_config_map() -> dict[str, bool]:
    """Create and return enabled flags for every registered onboarding step."""
    keys = get_registered_step_keys()
    for key in keys:
        OnboardingStepConfig.objects.get_or_create(key=key)
    configs = OnboardingStepConfig.objects.filter(key__in=keys)
    return {config.key: config.enabled for config in configs}
