"""Route a Django AdminSite's login through allauth's secure login gate.

The default admin site is wired automatically by ``CoreAuthConfig`` when both
Django admin and allauth account are installed. Applications using a custom
``AdminSite`` should call ``secure_admin_site(their_site)`` during setup.
"""


_WRAPPER_MARKER = "_dcm_secure_admin_login"


def secure_default_admin_site():
    """Wire the default ``admin.site`` when Django admin and allauth account are installed.

    Returns True when the site was wired (or already was), False when either app is missing.
    """
    from django.apps import apps

    if not (apps.is_installed("django.contrib.admin") and apps.is_installed("allauth.account")):
        return False
    from django.contrib import admin

    secure_admin_site(admin.site)
    return True


def secure_admin_site(site):
    """Wrap ``site.login`` with allauth's login redirect and staff checks.

    This is idempotent so app setup can safely call it more than once.
    """
    if getattr(site.login, _WRAPPER_MARKER, None) is True:
        return site

    from allauth.account.decorators import secure_admin_login

    wrapped_login = secure_admin_login(site.login)
    setattr(wrapped_login, _WRAPPER_MARKER, True)
    site.login = wrapped_login
    return site
