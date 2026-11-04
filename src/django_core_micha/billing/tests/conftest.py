import importlib.util

# The billing tests need the optional `billing` extra (stripe, PyYAML); without it they are not collected.
collect_ignore_glob = [] if all(importlib.util.find_spec(name) for name in ("stripe", "yaml")) else ["test_*.py"]

import pytest
from django.test import RequestFactory


@pytest.fixture(autouse=True)
def _factory_requests_skip_csrf(monkeypatch):
    """The views enforce CSRF explicitly; factory requests opt out so the tests exercise the view logic."""
    original = RequestFactory.request

    def request(self, **kwargs):
        built = original(self, **kwargs)
        built._dont_enforce_csrf_checks = True
        return built

    monkeypatch.setattr(RequestFactory, "request", request)


@pytest.fixture(autouse=True)
def _declaration_mail_in_request_thread(monkeypatch):
    """Production sends public-intake mail on a background thread; tests send it inline so outbox assertions are deterministic."""
    from django_core_micha.billing import declarations
    monkeypatch.setattr(declarations, "_in_background", lambda function, *args: function(*args))
