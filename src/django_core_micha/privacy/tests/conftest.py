import copy
import json
import uuid

import pytest
from django.core.cache import cache
from django.test import Client, override_settings


def sample_manifest():
    return {
        "app": "sample-app",
        "site": "sample-site",
        "locales": ["de", "en"],
        "controller": {"name": "Sample Controller", "contact": "privacy@example.test"},
        "notice": {
            "version": "privacy-1",
            "documents": {
                "privacy": {"de": "/privacy/de/", "en": "https://example.test/privacy/en"}
            },
        },
        "choice_validity_days": 180,
        "evidence_retention_days": 365,
        "purposes": [
            {
                "id": "analytics",
                "revision": 1,
                "required": False,
                "label": {"de": "Besuche messen", "en": "Measure visits"},
                "summary": {"de": "Optionale Messung", "en": "Optional measurement"},
                "services": [
                    {
                        "id": "first-party-metrics",
                        "provider": "Example service",
                        "trigger": "After an explicit analytics grant",
                        "data_categories": ["visit counts"],
                        "recipients": ["Sample Controller"],
                        "transfers": [],
                        "browser_storage": [
                            {
                                "kind": "localStorage",
                                "name": "analytics_id",
                                "scope": "this site",
                                "lifetime": "180 days",
                            }
                        ],
                        "server_retention": "30 days",
                    }
                ],
                "withdrawal_clears": ["analytics_id"],
            },
            {
                "id": "personalization",
                "revision": 1,
                "required": False,
                "label": {"de": "Personalisierung", "en": "Personalization"},
                "summary": {"de": "Optionale Anpassungen", "en": "Optional tailoring"},
                "services": [
                    {
                        "id": "theme-preference",
                        "provider": "Sample Controller",
                        "trigger": "After a personalization grant",
                        "data_categories": ["display preference"],
                        "recipients": ["Sample Controller"],
                        "transfers": [],
                        "browser_storage": [
                            {
                                "kind": "cookie",
                                "name": "theme_choice",
                                "scope": "host-only",
                                "lifetime": "180 days",
                            }
                        ],
                        "server_retention": "While the purpose is enabled",
                    }
                ],
                "withdrawal_clears": ["theme_choice"],
            },
            {
                "id": "account-session",
                "revision": 1,
                "required": True,
                "justification": "The session is required to serve the requested signed-in account.",
                "label": {"de": "Konto", "en": "Account"},
                "summary": {"de": "Anmeldung", "en": "Sign-in"},
                "services": [],
                "withdrawal_clears": [],
            },
        ],
    }


def current_config_version():
    from django_core_micha.privacy.manifest import current_manifest, public_manifest_from_config

    return public_manifest_from_config(current_manifest())["config_version"]


def choice_body(choices, *, expected_seq=0, request_id=None, notice_version="privacy-1", **extra):
    body = {
        "request_id": request_id or str(uuid.uuid4()),
        "expected_seq": expected_seq,
        "notice_version": notice_version,
        "config_version": current_config_version(),
        "locale": "en",
        "channel": "settings",
        "choices": choices,
    }
    body.update(extra)
    return body


def post_choice(client, choices, *, expected_seq=0, request_id=None, notice_version="privacy-1", **kwargs):
    body = choice_body(
        choices,
        expected_seq=expected_seq,
        request_id=request_id,
        notice_version=notice_version,
        **kwargs,
    )
    return client.post(
        "/privacy/choices/",
        data=json.dumps(body),
        content_type="application/json",
        HTTP_HOST="testserver",
        HTTP_ORIGIN="http://testserver",
    )


@pytest.fixture(autouse=True)
def privacy_settings():
    with override_settings(
        DCM_PRIVACY=sample_manifest(),
        ROOT_URLCONF="django_core_micha.privacy.tests.urls",
        ALLOWED_HOSTS=["testserver", "example.test", "evil.test", "allowed.test"],
        SESSION_COOKIE_SECURE=True,
    ):
        cache.clear()
        yield
        cache.clear()


@pytest.fixture
def api_client():
    return Client()


@pytest.fixture
def manifest_factory():
    return lambda: copy.deepcopy(sample_manifest())
