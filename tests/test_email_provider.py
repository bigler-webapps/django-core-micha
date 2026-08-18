"""Tests for the two-provider email configuration."""
import logging

from django_core_micha.settings._email_config import build_mailers, resolve_email_backend

CONSOLE = "django.core.mail.backends.console.EmailBackend"
RESEND = "anymail.backends.resend.EmailBackend"


def r(provider="", is_local=False, debug=False, resend_key=""):
    return resolve_email_backend(provider, is_local, debug, resend_key)


class TestEmailProviderResolution:
    def test_empty_local(self):
        backend, cfg = r(is_local=True)
        assert backend == CONSOLE
        assert cfg is None

    def test_empty_debug(self):
        backend, cfg = r(debug=True)
        assert backend == CONSOLE
        assert cfg is None

    def test_empty_nonlocal_with_resend_key(self):
        backend, cfg = r(resend_key="re_key_123")
        assert backend == RESEND
        assert cfg == {"RESEND_API_KEY": "re_key_123"}

    def test_empty_nonlocal_missing_key_warns_and_falls_back(self, caplog):
        with caplog.at_level(logging.WARNING, logger="backend"):
            backend, cfg = r()
        assert backend == CONSOLE
        assert cfg is None
        assert caplog.records

    def test_console_explicit(self):
        backend, cfg = r(provider="console")
        assert backend == CONSOLE
        assert cfg is None

    def test_resend_with_key(self):
        backend, cfg = r(provider="resend", resend_key="re_key_123")
        assert backend == RESEND
        assert cfg == {"RESEND_API_KEY": "re_key_123"}

    def test_resend_missing_key_warns_and_falls_back(self, caplog):
        with caplog.at_level(logging.WARNING, logger="backend"):
            backend, cfg = r(provider="resend")
        assert backend == CONSOLE
        assert cfg is None
        assert caplog.records

    def test_smtp_is_unknown_and_falls_back(self, caplog):
        with caplog.at_level(logging.WARNING, logger="backend"):
            backend, cfg = r(provider="smtp")
        assert backend == CONSOLE
        assert cfg is None
        assert "Unknown EMAIL_PROVIDER='smtp'" in caplog.text

    def test_postmark_is_unknown_and_falls_back(self, caplog):
        with caplog.at_level(logging.WARNING, logger="backend"):
            backend, cfg = r(provider="postmark")
        assert backend == CONSOLE
        assert cfg is None
        assert "Unknown EMAIL_PROVIDER='postmark'" in caplog.text

    def test_unknown_provider_warns_and_falls_back(self, caplog):
        with caplog.at_level(logging.WARNING, logger="backend"):
            backend, cfg = r(provider="sendgrid")
        assert backend == CONSOLE
        assert cfg is None
        assert caplog.records

    def test_mailers_default_uses_local_console_backend(self):
        backend, cfg = r(is_local=True)
        assert cfg is None
        assert build_mailers(backend) == {"default": {"BACKEND": CONSOLE}}

    def test_mailers_default_uses_resend_backend(self):
        backend, cfg = r(provider="resend", resend_key="re_key_123")
        assert cfg == {"RESEND_API_KEY": "re_key_123"}
        assert build_mailers(backend) == {"default": {"BACKEND": RESEND}}
