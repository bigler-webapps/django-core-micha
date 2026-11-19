"""Private capture of reset links for isolated, owned test accounts."""
import logging
import re

from django.apps import apps
from django.conf import settings
from django.db import transaction

logger = logging.getLogger(__name__)


_LINK = re.compile(r"https?://[^\s<>\"']+")


def link_from_message(body, expected):
    """Return the link as it appears in the rendered message body, or an empty string."""
    if not isinstance(body, str) or not isinstance(expected, str) or not expected:
        return ""
    for candidate in _LINK.findall(body):
        candidate = candidate.rstrip(".,;:)")
        if candidate == expected:
            return candidate
    return ""


def capture_password_reset(user, url, body=None):
    """Store the reset link of the rendered message. ``body`` is the rendered text:
    when given, the link is taken from it and a message without that link is not captured."""
    try:
        if not apps.is_installed("django_core_micha.test_management") or getattr(settings, "TEST_MANAGEMENT_ENABLED", False) is not True:
            return False
        if body is not None:
            url = link_from_message(body, url)
        if not url:
            logger.warning("test_management_mail_capture_failed")
            return False
        from .activation import ensure_instance
        from .models import TestAccount, TestInstance, TestMailMessage, TestRun

        instance = ensure_instance()
        with transaction.atomic():
            TestInstance.objects.select_for_update().get(pk=instance.pk)
            runs = TestRun.objects.select_for_update().filter(instance=instance, status=TestRun.Status.ACTIVE)
            accounts = list(TestAccount.objects.filter(
                run__in=runs, status=TestAccount.Status.ACTIVE, user_ref=str(user.pk),
            )[:2])
            if len(accounts) != 1:
                return False
            account = accounts[0]
            # Lock and recheck the run before storing, including against cleanup.
            run = TestRun.objects.select_for_update().get(pk=account.run_id)
            account.refresh_from_db()
            if (run.status != TestRun.Status.ACTIVE or account.status != TestAccount.Status.ACTIVE
                    or account.user_ref != str(user.pk)):
                return False
            TestMailMessage.objects.filter(account=account, status=TestMailMessage.Status.CAPTURED).update(
                link="", status=TestMailMessage.Status.DISCARDED,
            )
            TestMailMessage.objects.create(run=run, account=account, link=url)
            return True
    except Exception:
        logger.warning("test_management_mail_capture_failed")
        return False
