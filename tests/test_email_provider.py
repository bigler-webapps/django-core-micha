"""
Tests for resolve_email_backend() — pure-function, no Django setup required.
"""
import logging

import pytest

from django_core_micha.settings._email_config import resolve_email_backend

CONSOLE = "django.core.mail.backends.console.EmailBackend"
SMTP = "django.core.mail.backends.smtp.EmailBackend"
RESEND = "anymail.backends.resend.EmailBackend"
POSTMARK = "anymail.backends.postmark.EmailBackend"


def r(provider="", is_local=False, debug=False, host="", password="", resend_key="", postmark_token=""):
    return resolve_email_backend(provider, is_local, debug, host, password, resend_key, postmark_token)


class TestEmailProviderResolution:
    # ------------------------------------------------------------------ defaults

    def test_empty_local(self):
        backend, cfg = r(is_local=True)
        assert backend == CONSOLE
        assert cfg is None

    def test_empty_debug(self):
        backend, cfg = r(debug=True)
        assert backend == CONSOLE
        assert cfg is None

    def test_back_compat_nonlocal_with_credentials(self):
        """Existing apps: no EMAIL_PROVIDER + SMTP credentials → smtp, unchanged."""
        backend, cfg = r(host="smtp.example.com", password="s3cr3t")
        assert backend == SMTP
        assert cfg is None

    def test_empty_nonlocal_missing_creds_warns_and_falls_back(self, caplog):
        with caplog.at_level(logging.WARNING, logger="backend"):
            backend, cfg = r()
        assert backend == CONSOLE
        assert cfg is None
        assert caplog.records

    # ------------------------------------------------------------------ console

    def test_console_explicit(self):
        backend, cfg = r(provider="console")
        assert backend == CONSOLE
        assert cfg is None

    def test_console_does_not_require_smtp_vars(self):
        """console provider works without EMAIL_HOST/PORT — no crash."""
        backend, cfg = r(provider="console")
        assert backend == CONSOLE

    # ------------------------------------------------------------------ smtp

    def test_smtp_with_credentials(self):
        backend, cfg = r(provider="smtp", host="smtp.example.com", password="p@ss")
        assert backend == SMTP
        assert cfg is None

    def test_smtp_missing_creds_warns_and_falls_back(self, caplog):
        with caplog.at_level(logging.WARNING, logger="backend"):
            backend, cfg = r(provider="smtp")
        assert backend == CONSOLE
        assert cfg is None
        assert caplog.records

    # ------------------------------------------------------------------ resend

    def test_resend_with_key(self):
        backend, cfg = r(provider="resend", resend_key="re_key_123")
        assert backend == RESEND
        assert cfg == {"RESEND_API_KEY": "re_key_123"}

    def test_resend_does_not_require_email_port(self):
        """resend does not require EMAIL_HOST/PORT — no crash when SMTP vars absent."""
        backend, cfg = r(provider="resend", resend_key="re_key_123")
        assert backend == RESEND

    def test_resend_missing_key_warns_and_falls_back(self, caplog):
        with caplog.at_level(logging.WARNING, logger="backend"):
            backend, cfg = r(provider="resend")
        assert backend == CONSOLE
        assert cfg is None
        assert caplog.records

    # ------------------------------------------------------------------ postmark

    def test_postmark_with_token(self):
        backend, cfg = r(provider="postmark", postmark_token="pm_token_456")
        assert backend == POSTMARK
        assert cfg == {"POSTMARK_SERVER_TOKEN": "pm_token_456"}

    def test_postmark_does_not_require_email_port(self):
        """postmark does not require EMAIL_HOST/PORT — no crash when SMTP vars absent."""
        backend, cfg = r(provider="postmark", postmark_token="pm_token_456")
        assert backend == POSTMARK

    def test_postmark_missing_token_warns_and_falls_back(self, caplog):
        with caplog.at_level(logging.WARNING, logger="backend"):
            backend, cfg = r(provider="postmark")
        assert backend == CONSOLE
        assert cfg is None
        assert caplog.records

    # ------------------------------------------------------------------ unknown

    def test_unknown_provider_warns_and_falls_back(self, caplog):
        with caplog.at_level(logging.WARNING, logger="backend"):
            backend, cfg = r(provider="sendgrid")
        assert backend == CONSOLE
        assert cfg is None
        assert caplog.records
