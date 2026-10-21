import json

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory, override_settings
from django.utils import timezone
from rest_framework import serializers
from rest_framework.test import APIRequestFactory, force_authenticate

from django_core_micha.terms.models import TermsAcceptance
from django_core_micha.terms.services import accept_current_terms, has_accepted_current_terms
from django_core_micha.terms.views import terms_view
from django_core_micha.auth.views import BaseUserViewSet

DOC = {"app": "sample", "version": "v1", "url": "https://example.test/terms"}


def make_user(name="terms-user"):
    return get_user_model().objects.create_user(username=name, password="secret")


def request(method, user, body=None):
    req = getattr(RequestFactory(), method)("/terms/", data=body or "", content_type="application/json")
    req.user = user
    req._dont_enforce_csrf_checks = True
    return req


@pytest.mark.django_db
@override_settings(DCM_TERMS_DOCUMENT=DOC)
def test_acceptance_persists_server_document_and_is_idempotent():
    user = make_user()
    response = terms_view(request("post", user, json.dumps({"version": "v1", "accept": True})))
    assert response.status_code == 201
    row = TermsAcceptance.objects.get(user=user)
    assert (row.app, row.version, row.document_url) == ("sample", "v1", DOC["url"])
    original = row.accepted_at
    assert timezone.now() >= original
    again, created = accept_current_terms(user, "v1")
    assert not created and again.pk == row.pk and again.accepted_at == original
    assert TermsAcceptance.objects.filter(user=user).count() == 1
    assert response["Cache-Control"] == "no-store"


@pytest.mark.django_db
@override_settings(DCM_TERMS_DOCUMENT=DOC)
@pytest.mark.parametrize("body,detail", [
    ({"version": "v1"}, "acceptance_required"), ({"version": "v1", "accept": False}, "acceptance_required"),
    ({"version": "old", "accept": True}, "version_mismatch"),
    ({"version": "v1", "accept": True, "accepted_at": "2000-01-01"}, "unexpected_fields"),
])
def test_invalid_acceptance_requests_write_nothing(body, detail):
    user = make_user()
    response = terms_view(request("post", user, json.dumps(body)))
    assert response.status_code == (409 if detail == "version_mismatch" else 400)
    assert json.loads(response.content)["detail"] == detail
    assert not TermsAcceptance.objects.exists()


@pytest.mark.django_db
def test_unconfigured_and_anonymous_requests_fail_closed_and_csrf_is_enforced():
    user = make_user()
    with override_settings(DCM_TERMS_DOCUMENT=None):
        response = terms_view(request("post", user, json.dumps({"version": "v1", "accept": True})))
        assert response.status_code == 404 and json.loads(response.content)["detail"] == "terms_not_configured"
    anonymous = type("Anonymous", (), {"is_authenticated": False})()
    assert terms_view(request("get", anonymous)).status_code == 401
    csrf_request = RequestFactory().post("/terms/", data='{"version":"v1","accept":true}', content_type="application/json")
    csrf_request.user = user
    csrf_request._dont_enforce_csrf_checks = False
    with override_settings(DCM_TERMS_DOCUMENT=DOC):
        refused = terms_view(csrf_request)
        assert refused.status_code == 403 and refused["Cache-Control"] == "no-store"
        not_allowed = terms_view(request("put", user, json.dumps({"version": "v1", "accept": True})))
        assert not_allowed.status_code == 405 and not_allowed["Cache-Control"] == "no-store"
    assert not TermsAcceptance.objects.exists()


@pytest.mark.django_db
@override_settings(DCM_TERMS_DOCUMENT=DOC)
def test_new_version_requires_new_acceptance_and_preserves_history():
    user = make_user()
    accept_current_terms(user, "v1")
    first = TermsAcceptance.objects.get(user=user)
    with override_settings(DCM_TERMS_DOCUMENT={**DOC, "version": "v2", "url": "https://example.test/v2"}):
        assert not has_accepted_current_terms(user)
        accept_current_terms(user, "v2")
    first.refresh_from_db()
    assert first.version == "v1" and first.document_url == DOC["url"]
    assert TermsAcceptance.objects.filter(user=user).count() == 2


@pytest.mark.django_db
@override_settings(DCM_TERMS_DOCUMENT=DOC)
def test_acceptance_is_private_and_app_cannot_be_chosen_by_request():
    owner, other = make_user("terms-owner"), make_user("terms-other")
    accept_current_terms(owner, "v1")
    other_state = json.loads(terms_view(request("get", other)).content)
    assert other_state["accepted"] is False and other_state["accepted_at"] is None
    forged = terms_view(request("post", other, json.dumps({"version": "v1", "accept": True, "app": "sample"})))
    assert forged.status_code == 400 and json.loads(forged.content)["detail"] == "unexpected_fields"
    assert TermsAcceptance.objects.filter(user=owner, app="sample").count() == 1
    assert not TermsAcceptance.objects.filter(user=other).exists()


@pytest.mark.django_db
def test_generic_profile_patch_cannot_forge_terms_acceptance():
    class MinimalSerializer(serializers.ModelSerializer):
        class Meta:
            model = get_user_model()
            fields = ["id", "first_name"]

    class UserViewSet(BaseUserViewSet):
        serializer_class = MinimalSerializer

    user = make_user("terms-profile-patch")
    view = UserViewSet.as_view({"patch": "partial_update"})
    payload = {"first_name": "Changed", "terms_acceptances": [{"app": "sample", "version": "v1", "accepted_at": "2030-01-01"}]}
    req = APIRequestFactory().patch(f"/users/{user.pk}/", payload, format="json")
    force_authenticate(req, user=user)
    response = view(req, pk=user.pk)
    assert response.status_code == 400
    assert not TermsAcceptance.objects.exists()


def test_the_real_user_api_exposes_no_terms_field():
    from django_core_micha.auth.serializers import BaseUserSerializer
    # the shipped serializer and the self-edit allowlist are the only writable surfaces of /users/current/ and /users/<id>/
    assert not [name for name in BaseUserSerializer().fields if "terms" in name]
    assert not [name for name in BaseUserViewSet.current_patch_allowed_fields if "terms" in name]


@pytest.mark.django_db
def test_current_user_patch_cannot_forge_terms_acceptance():
    class MinimalSerializer(serializers.ModelSerializer):
        class Meta:
            model = get_user_model()
            fields = ["id", "first_name"]

    class UserViewSet(BaseUserViewSet):
        serializer_class = MinimalSerializer

    user = make_user("terms-current-patch")
    req = APIRequestFactory().patch("/users/current/", {"accepted_terms_version": "v1", "terms_accepted": True}, format="json")
    force_authenticate(req, user=user)
    response = UserViewSet.as_view({"patch": "current"})(req)
    assert response.status_code == 400
    assert sorted(response.data["disallowed_fields"]) == ["accepted_terms_version", "terms_accepted"]
    assert not TermsAcceptance.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize("setting", [
    {"app": "../bad", "version": "v1", "url": "https://example.test"},
    {"app": "sample", "version": "v1", "url": "javascript:alert(1)"},
    {"app": "sample", "version": "v1", "url": "https://example.test", "extra": 1},
])
def test_malformed_document_is_unconfigured(setting):
    with override_settings(DCM_TERMS_DOCUMENT=setting):
        assert terms_view(request("get", make_user())).status_code == 404


@pytest.mark.django_db
@override_settings(DCM_TERMS_DOCUMENT=DOC)
def test_http_replay_keeps_original_evidence_and_stale_version_reports_current_state():
    user = make_user("terms-replay")
    first = json.loads(terms_view(request("post", user, json.dumps({"version": "v1", "accept": True}))).content)
    replay = terms_view(request("post", user, json.dumps({"version": "v1", "accept": True})))
    assert replay.status_code == 200 and json.loads(replay.content)["accepted_at"] == first["accepted_at"]
    stale = terms_view(request("post", user, json.dumps({"version": "v0", "accept": True})))
    body = json.loads(stale.content)
    assert stale.status_code == 409 and body["detail"] == "version_mismatch"
    assert body["document"] == DOC and body["accepted"] is True and body["accepted_at"] == first["accepted_at"]
    assert TermsAcceptance.objects.filter(user=user).count() == 1


@pytest.mark.django_db
@override_settings(DCM_TERMS_DOCUMENT=DOC)
def test_a_concurrent_acceptance_resolves_to_the_row_the_first_request_wrote():
    from datetime import timedelta
    from django.db import IntegrityError, transaction
    user = make_user("terms-race")
    # the row a parallel request committed between this request's start and its insert
    earlier = timezone.now() - timedelta(seconds=5)
    winner = TermsAcceptance.objects.create(user=user, app="sample", version="v1", document_url=DOC["url"], accepted_at=earlier)
    with pytest.raises(IntegrityError), transaction.atomic():
        TermsAcceptance.objects.create(user=user, app="sample", version="v1", document_url=DOC["url"])
    response = terms_view(request("post", user, json.dumps({"version": "v1", "accept": True})))
    assert response.status_code == 200 and json.loads(response.content)["accepted_at"] == earlier.isoformat()
    row, created = accept_current_terms(user, "v1")
    assert not created and row.pk == winner.pk and row.accepted_at == earlier
    assert TermsAcceptance.objects.filter(user=user).count() == 1


@pytest.mark.django_db
@override_settings(DCM_TERMS_DOCUMENT=DOC)
def test_anonymous_post_writes_nothing():
    anonymous = type("Anonymous", (), {"is_authenticated": False})()
    response = terms_view(request("post", anonymous, json.dumps({"version": "v1", "accept": True})))
    assert response.status_code == 401 and json.loads(response.content)["detail"] == "authentication_required"
    assert not TermsAcceptance.objects.exists()


@pytest.mark.django_db
@override_settings(DCM_TERMS_DOCUMENT=DOC)
def test_privacy_cookie_and_social_login_users_remain_unaccepted():
    from types import SimpleNamespace
    from allauth.socialaccount.models import SocialAccount
    privacy = make_user("terms-privacy-only")
    privacy.profile = SimpleNamespace(accepted_privacy_statement=True, accepted_convenience_cookies=True)
    social = make_user("terms-social-only")
    SocialAccount.objects.create(user=social, provider="google", uid="google-uid-1")
    assert not has_accepted_current_terms(privacy) and not has_accepted_current_terms(social)
    assert json.loads(terms_view(request("get", social)).content)["accepted"] is False
    assert not TermsAcceptance.objects.exists()


@pytest.mark.django_db
def test_gate_fails_closed_without_a_valid_document():
    user = make_user("terms-gate")
    with override_settings(DCM_TERMS_DOCUMENT=DOC):
        accept_current_terms(user, "v1")
        assert has_accepted_current_terms(user)
    assert not has_accepted_current_terms(user)  # tests/settings.py defines no DCM_TERMS_DOCUMENT
    with override_settings(DCM_TERMS_DOCUMENT={**DOC, "url": "not a url"}):
        assert not has_accepted_current_terms(user)


@pytest.mark.django_db
@override_settings(DCM_TERMS_DOCUMENT=DOC)
def test_permission_refuses_until_the_current_version_is_accepted():
    from rest_framework.response import Response
    from rest_framework.views import APIView
    from django_core_micha.terms.permissions import HasAcceptedCurrentTerms

    class SetupView(APIView):
        permission_classes = [HasAcceptedCurrentTerms]

        def post(self, request):
            return Response({"ok": True})

    user = make_user("terms-permission")
    denied = APIRequestFactory().post("/setup/", {}, format="json")
    force_authenticate(denied, user=user)
    response = SetupView.as_view()(denied)
    assert response.status_code == 403 and str(response.data["detail"]) == "terms_acceptance_required"
    assert response.data["detail"].code == "terms_acceptance_required"
    accept_current_terms(user, "v1")
    allowed = APIRequestFactory().post("/setup/", {}, format="json")
    force_authenticate(allowed, user=user)
    assert SetupView.as_view()(allowed).status_code == 200
    with override_settings(DCM_TERMS_DOCUMENT={**DOC, "version": "v2"}):
        again = APIRequestFactory().post("/setup/", {}, format="json")
        force_authenticate(again, user=user)
        assert SetupView.as_view()(again).status_code == 403


@pytest.mark.django_db
def test_system_check_warns_when_absent_and_errors_when_malformed():
    from django.core import checks
    ids = lambda: {message.id for message in checks.run_checks() if message.id and message.id.startswith("dcm_terms")}
    assert ids() == {"dcm_terms.W001"}
    with override_settings(DCM_TERMS_DOCUMENT={**DOC, "version": "bad version"}):
        assert ids() == {"dcm_terms.E001"}
    with override_settings(DCM_TERMS_DOCUMENT=DOC):
        assert ids() == set()


@pytest.mark.django_db(transaction=True)
def test_terms_initial_migration_creates_schema_without_profile_changes():
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    user = get_user_model().objects.create_user(username="terms-migration", first_name="Kept")
    executor = MigrationExecutor(connection)
    executor.migrate([("dcm_terms", None)])
    try:
        assert "dcm_terms_termsacceptance" not in connection.introspection.table_names()
        executor = MigrationExecutor(connection)
        executor.migrate([("dcm_terms", "0001_initial")])
        assert "dcm_terms_termsacceptance" in connection.introspection.table_names()
        user.refresh_from_db()
        assert user.first_name == "Kept" and not TermsAcceptance.objects.exists()
    finally:
        from django.core.management import call_command
        call_command("migrate", run_syncdb=True, verbosity=0)
