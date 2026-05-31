"""Regression guard for S212 ACCOUNT_RATE_LIMITS (2.17.2).

allauth evaluates several rate-limited actions in an ANONYMOUS context — most
critically `login_failed`, which is consumed inside `pre_authenticate` before
any user is known. A `/user` rate key there makes allauth raise
`ImproperlyConfigured("ratelimit configured per user but used anonymously")`,
which surfaces as a 500 on every login attempt.

This test reads ACCOUNT_RATE_LIMITS_DEFAULTS straight from the settings_base
source via AST (importing the module would trip its production env guards),
and asserts no anonymous-context action keys on `/user`.
"""
from __future__ import annotations

import ast
import pathlib

import django_core_micha

# allauth evaluates these actions only when a user is already authenticated, so
# the `/user` rate key is valid for them. EVERY OTHER action in the config is
# reachable anonymously and must NOT use `/user` (allauth raises
# ImproperlyConfigured → 500). Testing the complement (rather than a fixed
# allow-list) means any future anonymous action is guarded automatically.
AUTHENTICATED_ONLY_ACTIONS = {
    "reauthenticate",
    "manage_email",
}


def _load_rate_limit_defaults() -> dict:
    base = pathlib.Path(django_core_micha.__file__).parent / "settings" / "settings_base.py"
    tree = ast.parse(base.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "ACCOUNT_RATE_LIMITS_DEFAULTS":
                    return ast.literal_eval(node.value)
    raise AssertionError("ACCOUNT_RATE_LIMITS_DEFAULTS not found in settings_base.py")


def _rate_keys(rate: str) -> list[str]:
    """Extract the per-key (3rd segment) of each comma-separated rate clause."""
    keys = []
    for clause in rate.split(","):
        parts = clause.strip().split("/")
        if len(parts) >= 3:  # amount/duration/key
            keys.append(parts[2])
    return keys


def test_anonymous_actions_never_key_on_user():
    defaults = _load_rate_limit_defaults()
    anonymous_actions = set(defaults) - AUTHENTICATED_ONLY_ACTIONS
    assert anonymous_actions, "expected at least one anonymous-context action"
    for action in anonymous_actions:
        rate = defaults[action]
        assert "user" not in _rate_keys(rate), (
            f"{action!r} uses a /user rate key ({rate!r}) but is evaluated in an "
            f"anonymous context — allauth will raise ImproperlyConfigured (500)."
        )


def test_action_names_match_allauth_canonical_keys():
    """allauth silently ignores unknown action keys, so a typo means the limit
    is never applied. Guard the names against allauth's own RATE_LIMITS keys."""
    from allauth.account import app_settings

    canonical = set(app_settings.RATE_LIMITS)
    unknown = set(_load_rate_limit_defaults()) - canonical
    assert not unknown, f"unknown allauth rate-limit action(s): {unknown}"


def test_login_failed_present_and_safe():
    defaults = _load_rate_limit_defaults()
    assert "login_failed" in defaults
    assert _rate_keys(defaults["login_failed"]), "login_failed should still be rate-limited"
