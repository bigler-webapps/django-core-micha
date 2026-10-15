"""Safe provider availability; secrets are never returned."""
import shutil
from django.conf import settings

def availability():
    providers = {
        "claude_cli": "available" if shutil.which("claude") and getattr(settings,"CLAUDE_CODE_OAUTH_TOKEN","") else "not_configured",
        "openai_api": "available" if getattr(settings,"OPENAI_API_KEY","") else "not_configured",
        "anthropic_api": "available" if getattr(settings,"ANTHROPIC_API_KEY","") else "not_configured",
    }
    # `status` is the surface-level answer (any usable provider); `providers` the per-driver detail.
    return {"status": "available" if "available" in providers.values() else "not_configured", "providers": providers}
