"""Subscription-based recipient resolution for events with no natural owner.

Some events have no user who triggered them -- a nightly job, a CLI-started run -- so
``notify()``'s required ``recipients`` list has no addressee at all (NOTIF-26 scope D).
This resolves recipients from users who explicitly opted in to a *category*, via
``NotificationCategorySubscription`` -- a different consent from
``NotificationCategoryChannelPreference``'s per-category channel override (see that
model's docstring for why the two must never share a row shape).

This deliberately does NOT reuse ``prefs.is_channel_enabled``: its bottom precedence
tier defaults ``chip``/``todo``/``popup`` to ``True`` for every user, so filtering a
subscription fan-out through it would deliver a chip to every user in the app on day
one, with nobody actually subscribed. Opt-in is enforced here instead: no explicit
``NotificationCategorySubscription`` row -> not a recipient -> nothing on any channel.

Categories only become visible to the ``preferences/`` endpoint (NOTIF-26 scope G) once
an app calls ``register_subscribable_category`` -- a category existing as a
``NotificationType.category`` value is not, by itself, subscribable; the app decides.
"""
from django.contrib.auth import get_user_model

from .models import NotificationCategorySubscription

_SUBSCRIBABLE_CATEGORIES: dict[str, str] = {}


def register_subscribable_category(category: str, label_key: str) -> None:
    """Register ``category`` as discoverable and subscribable via ``preferences/``.

    ``label_key`` is resolved through ``text_registry`` at read time, the same way a
    notification's own title/body keys are -- the app registers the translations
    separately via ``register_notification_text``.
    """

    _SUBSCRIBABLE_CATEGORIES[category] = label_key


def iter_subscribable_categories() -> dict[str, str]:
    """Return the registered {category: label_key} map."""

    return dict(_SUBSCRIBABLE_CATEGORIES)


def resolve_category_subscribers(category: str):
    """Return the users who explicitly subscribed to ``category``."""

    user_model = get_user_model()
    subscriber_ids = NotificationCategorySubscription.objects.filter(category=category).values_list(
        "user_id", flat=True
    )
    return user_model.objects.filter(pk__in=subscriber_ids)


def is_subscribed(user, category: str) -> bool:
    return NotificationCategorySubscription.objects.filter(user=user, category=category).exists()


def set_subscription(user, category: str, subscribed: bool) -> None:
    """Create or remove ``user``'s subscription row for ``category``."""

    if subscribed:
        NotificationCategorySubscription.objects.get_or_create(user=user, category=category)
    else:
        NotificationCategorySubscription.objects.filter(user=user, category=category).delete()
