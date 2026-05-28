"""S211 — AuthN-Event AuditLog tests."""
import hashlib
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.test import RequestFactory

# Connect signal receivers — django_core_micha.auth is not in test INSTALLED_APPS
# (its models are not needed here), so AppConfig.ready() never runs. We import
# the module directly to wire up the @receiver decorators.
import django_core_micha.auth.signals as _auth_signals  # noqa: F401
_auth_signals.connect_mfa_signals()

from django_core_micha.auditlog.models import AuditEvent

User = get_user_model()


@pytest.fixture()
def user(db):
    return User.objects.create_user(username="tester", password="pw", email="test@example.com")


@pytest.fixture()
def rf():
    return RequestFactory()


def _make_request(rf, session_key="abc123"):
    req = rf.get("/")
    req.META["REMOTE_ADDR"] = "10.20.30.40"
    req.META["HTTP_USER_AGENT"] = "Mozilla/5.0 (Windows NT 10.0) Chrome/120"
    session = MagicMock()
    session.session_key = session_key
    req.session = session
    return req


# ---------------------------------------------------------------------------
# user_logged_in
# ---------------------------------------------------------------------------

class TestUserLoggedIn:
    def test_creates_audit_event(self, user, db, rf):
        req = _make_request(rf)
        user_logged_in.send(sender=User, request=req, user=user)
        event = AuditEvent.objects.filter(event_type="users.user.logged_in").first()
        assert event is not None
        assert event.actor_id == user.pk

    def test_ip_is_anonymized(self, user, db, rf):
        req = _make_request(rf)
        user_logged_in.send(sender=User, request=req, user=user)
        event = AuditEvent.objects.get(event_type="users.user.logged_in")
        assert event.metadata["ip"] == "10.20.30.0/24"

    def test_no_plaintext_password_in_metadata(self, user, db, rf):
        req = _make_request(rf)
        user_logged_in.send(sender=User, request=req, user=user)
        event = AuditEvent.objects.get(event_type="users.user.logged_in")
        meta_str = str(event.metadata)
        assert "pw" not in meta_str
        assert "password" not in meta_str.lower()


# ---------------------------------------------------------------------------
# user_logged_out
# ---------------------------------------------------------------------------

class TestUserLoggedOut:
    def test_creates_audit_event(self, user, db, rf):
        req = _make_request(rf)
        user_logged_out.send(sender=User, request=req, user=user)
        event = AuditEvent.objects.filter(event_type="users.user.logged_out").first()
        assert event is not None
        assert event.actor_id == user.pk


# ---------------------------------------------------------------------------
# user_login_failed
# ---------------------------------------------------------------------------

class TestUserLoginFailed:
    def test_creates_audit_event_with_no_actor(self, db, rf):
        req = _make_request(rf)
        user_login_failed.send(
            sender=User,
            credentials={"username": "attacker@example.com", "password": "wrong"},
            request=req,
        )
        event = AuditEvent.objects.filter(event_type="users.user.login_failed").first()
        assert event is not None
        assert event.actor_id is None

    def test_username_is_hashed_not_plaintext(self, db, rf):
        req = _make_request(rf)
        email = "attacker@example.com"
        user_login_failed.send(
            sender=User,
            credentials={"username": email, "password": "wrong"},
            request=req,
        )
        event = AuditEvent.objects.get(event_type="users.user.login_failed")
        assert email not in str(event.metadata)
        # 32 hex chars = 128 bits — safe against offline preimage attacks (S2)
        expected_hash = hashlib.sha256(email.lower().encode()).hexdigest()[:32]
        assert event.metadata["credential_hash"] == expected_hash
        assert len(event.metadata["credential_hash"]) == 32

    def test_no_plaintext_password_in_metadata(self, db, rf):
        req = _make_request(rf)
        user_login_failed.send(
            sender=User,
            credentials={"username": "someone@example.com", "password": "sekret"},
            request=req,
        )
        event = AuditEvent.objects.get(event_type="users.user.login_failed")
        assert "sekret" not in str(event.metadata)


# ---------------------------------------------------------------------------
# allauth account signals — fire directly, no full allauth stack needed
# ---------------------------------------------------------------------------

class TestPasswordSignals:
    def test_password_changed(self, user, db, rf):
        from allauth.account.signals import password_changed
        req = _make_request(rf)
        password_changed.send(sender=User, request=req, user=user)
        assert AuditEvent.objects.filter(event_type="users.user.password_changed", actor=user).exists()

    def test_password_set(self, user, db, rf):
        from allauth.account.signals import password_set
        req = _make_request(rf)
        password_set.send(sender=User, request=req, user=user)
        assert AuditEvent.objects.filter(event_type="users.user.password_set", actor=user).exists()

    def test_password_reset(self, user, db, rf):
        from allauth.account.signals import password_reset
        req = _make_request(rf)
        password_reset.send(sender=User, request=req, user=user)
        assert AuditEvent.objects.filter(event_type="users.user.password_reset", actor=user).exists()


class TestEmailSignals:
    def _make_email_address(self, user):
        ea = MagicMock()
        ea.user = user
        ea.email = "test@example.com"
        return ea

    def test_email_confirmed(self, user, db, rf):
        from allauth.account.signals import email_confirmed
        req = _make_request(rf)
        email_confirmed.send(sender=MagicMock, request=req, email_address=self._make_email_address(user))
        event = AuditEvent.objects.filter(event_type="users.user.email.confirmed").first()
        assert event is not None
        assert event.metadata["email_domain"] == "example.com"

    def test_email_added(self, user, db, rf):
        from allauth.account.signals import email_added
        req = _make_request(rf)
        email_added.send(sender=MagicMock, request=req, user=user, email_address=self._make_email_address(user))
        assert AuditEvent.objects.filter(event_type="users.user.email.added", actor=user).exists()

    def test_email_removed(self, user, db, rf):
        from allauth.account.signals import email_removed
        req = _make_request(rf)
        email_removed.send(sender=MagicMock, request=req, user=user, email_address=self._make_email_address(user))
        assert AuditEvent.objects.filter(event_type="users.user.email.removed", actor=user).exists()

    def test_email_address_not_in_metadata(self, user, db, rf):
        from allauth.account.signals import email_confirmed
        req = _make_request(rf)
        ea = self._make_email_address(user)
        email_confirmed.send(sender=MagicMock, request=req, email_address=ea)
        event = AuditEvent.objects.get(event_type="users.user.email.confirmed")
        # Full email address must not be stored
        assert "test@example.com" not in str(event.metadata)


class TestMfaSignals:
    def _make_authenticator(self, type_str="totp"):
        auth = MagicMock()
        auth.type = type_str
        return auth

    def test_authenticator_added(self, user, db, rf):
        from allauth.mfa.signals import authenticator_added
        req = _make_request(rf)
        authenticator_added.send(
            sender=MagicMock, request=req, user=user,
            authenticator=self._make_authenticator("totp"),
        )
        event = AuditEvent.objects.filter(event_type="users.user.mfa.authenticator_added").first()
        assert event is not None
        assert event.metadata["authenticator_type"] == "totp"

    def test_authenticator_removed(self, user, db, rf):
        from allauth.mfa.signals import authenticator_removed
        req = _make_request(rf)
        authenticator_removed.send(
            sender=MagicMock, request=req, user=user,
            authenticator=self._make_authenticator("recovery_codes"),
        )
        assert AuditEvent.objects.filter(event_type="users.user.mfa.authenticator_removed").exists()

    def test_authenticator_reset(self, user, db, rf):
        from allauth.mfa.signals import authenticator_reset
        req = _make_request(rf)
        authenticator_reset.send(
            sender=MagicMock, request=req, user=user,
            authenticator=self._make_authenticator("recovery_codes"),
        )
        assert AuditEvent.objects.filter(event_type="users.user.mfa.authenticator_reset").exists()

    def test_no_secret_in_metadata(self, user, db, rf):
        from allauth.mfa.signals import authenticator_added
        req = _make_request(rf)
        auth = self._make_authenticator("totp")
        auth.secret = "JBSWY3DPEHPK3PXP"  # would be a TOTP secret
        authenticator_added.send(
            sender=MagicMock, request=req, user=user, authenticator=auth,
        )
        event = AuditEvent.objects.get(event_type="users.user.mfa.authenticator_added")
        assert "JBSWY3DPEHPK3PXP" not in str(event.metadata)


class TestSocialSignals:
    def _make_sociallogin(self, user, provider="google", uid="12345"):
        account = MagicMock()
        account.provider = provider
        account.uid = uid
        sl = MagicMock()
        sl.user = user
        sl.account = account
        return sl

    def _make_socialaccount(self, user, provider="google", uid="12345"):
        sa = MagicMock()
        sa.user = user
        sa.provider = provider
        sa.uid = uid
        return sa

    def test_social_account_added(self, user, db, rf):
        from allauth.socialaccount.signals import social_account_added
        req = _make_request(rf)
        social_account_added.send(
            sender=MagicMock, request=req,
            sociallogin=self._make_sociallogin(user),
        )
        event = AuditEvent.objects.filter(event_type="users.user.social.added").first()
        assert event is not None
        assert event.metadata["provider"] == "google"
        assert event.metadata["uid"] == "12345"

    def test_social_account_removed(self, user, db, rf):
        from allauth.socialaccount.signals import social_account_removed
        req = _make_request(rf)
        social_account_removed.send(
            sender=MagicMock, request=req,
            socialaccount=self._make_socialaccount(user),
        )
        assert AuditEvent.objects.filter(event_type="users.user.social.removed").exists()

    def test_social_account_updated(self, user, db, rf):
        from allauth.socialaccount.signals import social_account_updated
        req = _make_request(rf)
        social_account_updated.send(
            sender=MagicMock, request=req,
            sociallogin=self._make_sociallogin(user),
        )
        assert AuditEvent.objects.filter(event_type="users.user.social.updated").exists()


# ---------------------------------------------------------------------------
# PII guard — no token/access_token/secret anywhere
# ---------------------------------------------------------------------------

class TestPiiGuard:
    PII_WORDS = ("password", "secret", "token", "access_token", "totp_secret", "seed")

    def test_no_pii_in_login_event(self, user, db, rf):
        req = _make_request(rf)
        user_logged_in.send(sender=User, request=req, user=user)
        event = AuditEvent.objects.get(event_type="users.user.logged_in")
        meta_str = str(event.metadata).lower()
        for word in self.PII_WORDS:
            assert word not in meta_str, f"PII word '{word}' found in logged_in metadata"

    def test_no_pii_in_login_failed_event(self, db, rf):
        req = _make_request(rf)
        user_login_failed.send(
            sender=User,
            credentials={"username": "victim@example.com", "password": "hunter2"},
            request=req,
        )
        event = AuditEvent.objects.get(event_type="users.user.login_failed")
        meta_str = str(event.metadata).lower()
        assert "hunter2" not in meta_str
        assert "victim@example.com" not in meta_str
