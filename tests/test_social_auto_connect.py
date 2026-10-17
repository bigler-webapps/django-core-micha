"""Provider verification requirements for social account auto-connect."""

from types import SimpleNamespace

import pytest
from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount, SocialLogin
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from django_core_micha.auth.adapters import InvitationOnlySocialAdapter
from django_core_micha.auth.signals import force_auto_connect_on_email_match


User = get_user_model()


@pytest.fixture
def request_obj():
    request = RequestFactory().get("/")
    request.session = SimpleNamespace(session_key="social-test-session")
    return request


@pytest.fixture
def adapter():
    return InvitationOnlySocialAdapter()


def make_user(email):
    return User.objects.create_user(
        username=email.split("@")[0], email=email, password="test-password"
    )


def make_sociallogin(provider, email=None, verified=False, uid="new-account", addresses=None):
    user = User(email=email or "")
    if addresses is None:
        addresses = [] if email is None else [EmailAddress(email=email, verified=verified)]
    account = SocialAccount(provider=provider, uid=uid, extra_data={})
    # The test settings do not install concrete provider apps; connect still
    # persists a real SocialAccount and emits the same signal with this stub.
    account.get_provider = lambda: SimpleNamespace(id=provider)
    return SocialLogin(
        user=user,
        account=account,
        email_addresses=addresses,
    )


def add_local_email(user, email, verified=True):
    return EmailAddress.objects.create(
        user=user, email=email, verified=verified, primary=True
    )


def db_counts():
    return User.objects.count(), EmailAddress.objects.count(), SocialAccount.objects.count()


@pytest.mark.django_db
def test_unverified_microsoft_match_is_refused_without_side_effects(
    request_obj, adapter, caplog
):
    user = make_user("victim@example.com")
    add_local_email(user, user.email)
    login = make_sociallogin("microsoft", user.email, verified=False)
    before = db_counts()

    force_auto_connect_on_email_match(None, request_obj, login)
    is_open = adapter.is_open_for_signup(request_obj, login)

    assert is_open is False
    assert login.user is not user
    assert login.is_existing is False
    assert not SocialAccount.objects.filter(provider="microsoft", uid="new-account").exists()
    assert db_counts() == before
    assert user.email not in caplog.text
    assert "provider=microsoft" in caplog.text
    assert "reason=no_provider_verified_email" in caplog.text


@pytest.mark.django_db
def test_verified_google_match_connects_to_verified_local_user(request_obj, adapter):
    user = make_user("google@example.com")
    add_local_email(user, user.email)
    login = make_sociallogin("google", user.email, verified=True)

    force_auto_connect_on_email_match(None, request_obj, login)

    linked = SocialAccount.objects.get(provider="google", uid="new-account")
    assert linked.user == user
    assert login.user == user
    assert login.is_existing is True
    assert adapter.is_open_for_signup(request_obj, login) is True


@pytest.mark.django_db
def test_provider_verified_address_does_not_link_unverified_local_address(
    request_obj, adapter
):
    user = make_user("local-unverified@example.com")
    add_local_email(user, user.email, verified=False)
    login = make_sociallogin("google", user.email, verified=True)
    before = db_counts()

    force_auto_connect_on_email_match(None, request_obj, login)

    assert adapter.is_open_for_signup(request_obj, login) is False
    assert login.user is not user
    assert not SocialAccount.objects.filter(uid="new-account").exists()
    assert db_counts() == before


@pytest.mark.parametrize("provider", ["microsoft", "google"])
@pytest.mark.django_db
def test_already_linked_account_is_unchanged(request_obj, provider):
    user = make_user(f"{provider}@example.com")
    add_local_email(user, user.email)
    linked = SocialAccount.objects.create(provider=provider, uid="already-linked", user=user)
    login = SocialLogin(
        user=user,
        account=linked,
        email_addresses=[EmailAddress(email=user.email, verified=False)],
    )
    before = db_counts()

    force_auto_connect_on_email_match(None, request_obj, login)

    assert login.is_existing is True
    assert login.user == user
    assert SocialAccount.objects.get(pk=linked.pk).user == user
    assert db_counts() == before


@pytest.mark.django_db
def test_verified_second_address_is_used_instead_of_unverified_first(
    request_obj, adapter
):
    user = make_user("second@example.com")
    add_local_email(user, user.email)
    login = make_sociallogin(
        "google",
        addresses=[
            EmailAddress(email="unverified@example.com", verified=False),
            EmailAddress(email="SECOND@example.com", verified=True),
        ],
    )

    force_auto_connect_on_email_match(None, request_obj, login)

    assert SocialAccount.objects.get(provider="google", uid="new-account").user == user
    assert login.user == user
    assert adapter.is_open_for_signup(request_obj, login) is True


@pytest.mark.parametrize(
    ("provider", "email", "verified"),
    [("google", None, False), ("google", "nobody@example.com", True)],
)
@pytest.mark.django_db
def test_no_email_or_matching_user_is_a_noop(
    request_obj, adapter, provider, email, verified
):
    login = make_sociallogin(provider, email=email, verified=verified)
    before = db_counts()

    force_auto_connect_on_email_match(None, request_obj, login)

    assert adapter.is_open_for_signup(request_obj, login) is False
    assert login.is_existing is False
    assert db_counts() == before


@pytest.mark.parametrize("verified", [False, True])
@pytest.mark.django_db
def test_case_insensitive_match_requires_provider_verification(
    request_obj, adapter, verified
):
    user = make_user("user@example.com")
    add_local_email(user, user.email)
    login = make_sociallogin("google", "USER@Example.com", verified=verified)
    before = db_counts()

    force_auto_connect_on_email_match(None, request_obj, login)

    if verified:
        assert SocialAccount.objects.get(provider="google", uid="new-account").user == user
        assert login.user == user
        assert adapter.is_open_for_signup(request_obj, login) is True
    else:
        assert adapter.is_open_for_signup(request_obj, login) is False
        assert login.user is not user
        assert db_counts() == before
