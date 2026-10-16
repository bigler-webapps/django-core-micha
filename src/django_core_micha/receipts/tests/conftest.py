import pytest
from django.test import RequestFactory

@pytest.fixture(autouse=True)
def _factory_requests_skip_csrf(monkeypatch):
    original=RequestFactory.request
    def request(self,**kwargs):
        built=original(self,**kwargs);built._dont_enforce_csrf_checks=True;return built
    monkeypatch.setattr(RequestFactory,"request",request)

