from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ObjectDoesNotExist
from django.test import override_settings

from django_core_micha.notifications import dispatch as dispatch_module
from django_core_micha.notifications.api import notify
from django_core_micha.notifications.models import (
    Notification,
    NotificationDelivery,
    NotificationPreference,
    NotificationRecipient,
)
from django_core_micha.notifications.text_registry import _REGISTRY, register_notification_text
from django_core_micha.notifications.types import NotificationType, _REGISTRY as TYPE_REGISTRY


TITLE_KEY = "test.recipient-language.title"
BODY_KEY = "test.recipient-language.body"
TRANSLATIONS = {
    "de": ("Titel Deutsch", "Text Deutsch"),
    "en": ("English title", "English body"),
    "fr": ("Titre français", "Texte français"),
}


@pytest.fixture(autouse=True)
def isolated_text_registry():
    original = _REGISTRY.copy()
    _REGISTRY.clear()
    register_notification_text(
        TITLE_KEY, {language: texts[0] for language, texts in TRANSLATIONS.items()}
    )
    register_notification_text(
        BODY_KEY, {language: texts[1] for language, texts in TRANSLATIONS.items()}
    )
    yield
    _REGISTRY.clear()
    _REGISTRY.update(original)


def _make_recipient(username, *, profile_language=None, contact_profile=...):
    user = get_user_model().objects.create_user(
        username=username,
        email=f"{username}@example.test",
        password="password",
    )
    if profile_language is not None:
        user.profile = SimpleNamespace(language=profile_language)
    if contact_profile is not ...:
        user.contact_profile = contact_profile
    notification, _ = Notification.objects.get_or_create_by_dedup(
        notification_type=f"recipient-language-{username}",
        category="system",
        content={
            "title_key": TITLE_KEY,
            "body_key": BODY_KEY,
            "require_registered_text": True,
        },
    )
    recipient = NotificationRecipient.objects.create(notification=notification, user=user)
    return user, recipient


@pytest.mark.django_db
@pytest.mark.parametrize("language", ("de", "en", "fr"))
@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_email_dispatch_renders_registered_text_in_app_profile_language(language):
    mail.outbox.clear()
    user, recipient = _make_recipient(f"app-language-{language}", profile_language=language)

    result = dispatch_module.EmailDispatcher().deliver(recipient.notification, recipient)

    assert result.ok is True
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [user.email]
    assert (mail.outbox[0].subject, mail.outbox[0].body) == TRANSLATIONS[language]


@pytest.mark.django_db
@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_contact_profile_language_takes_precedence_over_app_profile():
    mail.outbox.clear()
    user, recipient = _make_recipient(
        "contact-language-precedence",
        profile_language="fr",
        contact_profile=SimpleNamespace(language="en"),
    )

    dispatch_module.EmailDispatcher().deliver(recipient.notification, recipient)

    assert (mail.outbox[0].subject, mail.outbox[0].body) == TRANSLATIONS["en"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("username", "profile_language", "contact_profile", "expected_language"),
    [
        ("no-profiles", None, None, "de"),
        ("unsupported-app-language", "it", None, "de"),
        ("unsupported-contact-language", "fr", SimpleNamespace(language="it"), "de"),
        ("null-contact-language", "fr", SimpleNamespace(language=None), "de"),
        ("missing-contact-language", "en", SimpleNamespace(), "de"),
    ],
)
@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_missing_or_unsupported_profile_languages_default_to_german(
    username, profile_language, contact_profile, expected_language
):
    mail.outbox.clear()
    user, recipient = _make_recipient(
        username,
        profile_language=profile_language,
        contact_profile=contact_profile,
    )

    dispatch_module.EmailDispatcher().deliver(recipient.notification, recipient)

    assert mail.outbox[0].to == [user.email]
    assert (mail.outbox[0].subject, mail.outbox[0].body) == TRANSLATIONS[expected_language]


@pytest.mark.django_db
@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_none_contact_profile_uses_available_app_profile():
    mail.outbox.clear()
    user, recipient = _make_recipient(
        "none-contact-with-app", profile_language="fr", contact_profile=None,
    )
    dispatch_module.EmailDispatcher().deliver(recipient.notification, recipient)
    assert mail.outbox[0].to == [user.email]
    assert (mail.outbox[0].subject, mail.outbox[0].body) == TRANSLATIONS["fr"]


@pytest.mark.django_db
@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_missing_reverse_contact_relation_uses_app_profile(monkeypatch):
    mail.outbox.clear()
    user, recipient = _make_recipient("missing-reverse-contact", profile_language="en")

    class RelatedObjectDoesNotExist(ObjectDoesNotExist, AttributeError):
        pass

    def missing_contact(instance):
        raise RelatedObjectDoesNotExist("No contact profile row")

    # Match Django's reverse one-to-one descriptor exception without adding a
    # consumer-specific profile model or migration to the shared test app.
    monkeypatch.setattr(type(user), "contact_profile", property(missing_contact), raising=False)
    dispatch_module.EmailDispatcher().deliver(recipient.notification, recipient)
    assert mail.outbox[0].to == [user.email]
    assert (mail.outbox[0].subject, mail.outbox[0].body) == TRANSLATIONS["en"]


@pytest.mark.django_db
def test_non_email_rendering_preserves_contact_profile_only_behavior():
    user, recipient = _make_recipient("non-email-language", profile_language="fr")
    assert dispatch_module._recipient_language(user) == "de"
    title, body, _ = dispatch_module._render_content(recipient.notification.content, user)
    assert (title, body) == TRANSLATIONS["de"]


@pytest.mark.django_db
@pytest.mark.parametrize("opted_in", [True, False])
@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_canonical_email_delivery_respects_preference_and_renders_language(monkeypatch, opted_in):
    mail.outbox.clear()
    user, _ = _make_recipient("email-preference", profile_language="en")
    NotificationPreference.objects.create(user=user, email_opt_in=opted_in)
    notification_type = "recipient-language-preference"
    monkeypatch.setitem(TYPE_REGISTRY, notification_type, NotificationType(
        key=notification_type, category="system", mode="event",
        resolution="user-done", active=True, passive=False,
    ))
    notification = notify(
        type=notification_type, recipients=[user], channels=["email"],
        content={"title_key": TITLE_KEY, "body_key": BODY_KEY, "require_registered_text": True},
    )
    deliveries = NotificationDelivery.objects.filter(
        recipient__notification=notification, recipient__user=user, channel="email",
    )
    assert len(mail.outbox) == int(opted_in)
    assert deliveries.filter(status="sent").exists() is opted_in
    if opted_in:
        assert mail.outbox[0].to == [user.email]
        assert (mail.outbox[0].subject, mail.outbox[0].body) == TRANSLATIONS["en"]
