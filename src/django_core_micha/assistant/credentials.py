from django.conf import settings
def resolve_credential(user, driver):
    """Single credential seam. Per-user credential support is intentionally deferred."""
    if driver=="claude_cli": return getattr(settings,"CLAUDE_CODE_OAUTH_TOKEN", ""), "deployment"
    if driver=="openai_api": return getattr(settings,"OPENAI_API_KEY", ""), "deployment"
    if driver=="anthropic_api": return getattr(settings,"ANTHROPIC_API_KEY", ""), "deployment"
    return "", "deployment"
