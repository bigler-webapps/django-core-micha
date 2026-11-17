from django.conf import settings
def resolve_credential(user, driver, source_id=None):
    """Single credential seam. Per-user credential support is intentionally deferred."""
    if source_id:
        from django_core_micha.ai_policy.policy import policy_enabled
        from django_core_micha.ai_policy.resolver import credential_setting, source_config
        if policy_enabled():
            config = source_config(source_id) or {}
            setting = credential_setting(config)
            if not setting:
                return "", "managed"
            return getattr(settings, setting, ""), str(config.get("billing_source") or "managed")
    if driver=="claude_cli": return getattr(settings,"CLAUDE_CODE_OAUTH_TOKEN", ""), "deployment"
    if driver=="openai_api": return getattr(settings,"OPENAI_API_KEY", ""), "deployment"
    if driver=="anthropic_api": return getattr(settings,"ANTHROPIC_API_KEY", ""), "deployment"
    return "", "deployment"
