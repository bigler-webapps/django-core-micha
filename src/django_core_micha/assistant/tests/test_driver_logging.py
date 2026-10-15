import logging
from types import SimpleNamespace as NS

import pytest
from asgiref.sync import async_to_sync

from django_core_micha.assistant.drivers.base import DriverError


class ProviderFailure(Exception):
    def __init__(self, status_code, body):
        self.status_code = status_code
        self.body = body
        super().__init__(body.get("message", "provider failure") if isinstance(body, dict) else str(body))


def _run(driver):
    async def run():
        return [event async for event in driver.stream(model="model-test", messages=[])]

    return run


def _openai_driver(monkeypatch, error):
    from django_core_micha.assistant.drivers import openai_api

    monkeypatch.setattr(openai_api, "_reserve", lambda *args: object())
    monkeypatch.setattr(openai_api, "release_reservation", lambda **kwargs: None)
    async def fail(**kwargs):
        raise error
    return openai_api.OpenAIDriver(NS(responses=NS(create=fail)))


def _anthropic_driver(monkeypatch, error):
    from django_core_micha.assistant.drivers import base
    from django_core_micha.extraction import cost_guard
    monkeypatch.setattr(base, "_reserve", lambda *args: object())
    monkeypatch.setattr(cost_guard, "release_reservation", lambda **kwargs: None)
    def fail(**kwargs):
        raise error
    return __import__("django_core_micha.assistant.drivers.anthropic_api", fromlist=["AnthropicDriver"]).AnthropicDriver(
        NS(messages=NS(stream=fail)))


def _assert_logged_failure(caplog, driver, expected_code="provider_error"):
    with pytest.raises(DriverError) as result:
        async_to_sync(_run(driver))()
    assert result.value.code == expected_code
    records = [record for record in caplog.records if record.name == "django_core_micha.assistant.drivers.base"]
    assert len(records) == 1
    return records[0].getMessage()


def test_openai_provider_error_details_are_logged(caplog, monkeypatch):
    error = ProviderFailure(400, {"message": "invalid schema", "type": "invalid_request_error",
                                  "code": "bad_schema", "param": "tools[0].parameters"})
    driver = _openai_driver(monkeypatch, error)
    with caplog.at_level(logging.WARNING, logger="django_core_micha.assistant.drivers.base"):
        text = _assert_logged_failure(caplog, driver)
    assert all(value in text for value in ("400", "invalid_request_error", "bad_schema", "tools[0].parameters", "invalid schema"))


def test_anthropic_provider_error_details_are_logged(caplog, monkeypatch):
    error = ProviderFailure(400, {"type": "error", "error": {"type": "invalid_request_error", "message": "invalid schema",
                                                                  "code": "bad_schema", "param": "tools"}})
    driver = _anthropic_driver(monkeypatch, error)
    with caplog.at_level(logging.WARNING, logger="django_core_micha.assistant.drivers.base"):
        text = _assert_logged_failure(caplog, driver)
    assert all(value in text for value in ("400", "invalid_request_error", "bad_schema", "tools", "invalid schema"))


def test_provider_message_redacts_key_like_tokens(caplog, monkeypatch):
    secret = "sk-proj-abcdefgh12345678"
    error = ProviderFailure(400, {"message": f"rejected token {secret}", "type": "invalid_request_error"})
    driver = _openai_driver(monkeypatch, error)
    with caplog.at_level(logging.WARNING, logger="django_core_micha.assistant.drivers.base"):
        text = _assert_logged_failure(caplog, driver)
    assert secret not in text
    assert "[REDACTED]" in text


def test_api_429_still_maps_to_quota_exceeded(caplog, monkeypatch):
    error = ProviderFailure(429, {"message": "rate limit", "type": "rate_limit_error", "code": "rate_limit"})
    driver = _openai_driver(monkeypatch, error)
    with caplog.at_level(logging.WARNING, logger="django_core_micha.assistant.drivers.base"):
        text = _assert_logged_failure(caplog, driver, expected_code="quota_exceeded")
    assert "429" in text


def test_log_provider_error_extracts_fields_from_arbitrary_bodies_and_redacts_anthropic_keys(caplog):
    import logging
    from types import SimpleNamespace as NS

    from django_core_micha.assistant.drivers.base import log_provider_error

    openai_like = NS(status_code=422, body={"message": "bad value", "type": "invalid_request_error", "code": "bad_schema", "param": "tools[0]"})
    anthropic_like = NS(status_code=400, body={"type": "error", "error": {"type": "invalid_request_error", "message": "key sk-ant-api03-ABCDEFGH_12345678-xyz leaked"}})
    with caplog.at_level(logging.WARNING, logger="django_core_micha.assistant.drivers.base"):
        log_provider_error("openai_api", "m-1", openai_like)
        log_provider_error("anthropic_api", "m-2", anthropic_like)
        log_provider_error("openai_api", "m-3", ValueError("plain failure"))
    first, second, third = [record.getMessage() for record in caplog.records]
    assert "status=422" in first and "code=bad_schema" in first and "param=tools[0]" in first and "message=bad value" in first
    assert "model=m-2" in second and "status=400" in second and "sk-ant-api03" not in second and "[REDACTED]" in second
    assert "status=None" in third and "ValueError" in third and "plain failure" in third
