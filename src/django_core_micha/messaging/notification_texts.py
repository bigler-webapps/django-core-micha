"""Per-language templates for the messaging ``new_message`` push/email notification.

Registered into ``notifications.text_registry`` the same way an app registers its own
``NotificationType`` (see ``register_messaging_notification_type``) -- title is the
sender's name (a language-independent placeholder template), body is either the
message text itself (kind ``chat``) or a translated per-kind fallback for content that
has no text of its own.
"""
from django_core_micha.notifications.text_registry import register_notification_text

NEW_MESSAGE_TITLE_KEY = "messaging.new_message.title"
NEW_MESSAGE_TITLE_UNKNOWN_SENDER_KEY = "messaging.new_message.title.unknown_sender"

NEW_MESSAGE_BODY_KEY_BY_KIND = {
    "chat": "messaging.new_message.body.chat",
    "attachment": "messaging.new_message.body.attachment",
    "poll": "messaging.new_message.body.poll",
    "announcement": "messaging.new_message.body.announcement",
    "system": "messaging.new_message.body.system",
    "deleted": "messaging.new_message.body.deleted",
}

NEW_MESSAGE_BODY_HIDDEN_KEY = "messaging.new_message.body.hidden"

_REGISTERED = False


def register_messaging_notification_texts() -> None:
    """Idempotently register this module's templates with the generic text registry."""

    global _REGISTERED
    if _REGISTERED:
        return

    register_notification_text(NEW_MESSAGE_TITLE_KEY, {"de": "{sender}", "en": "{sender}", "fr": "{sender}"})
    register_notification_text(NEW_MESSAGE_TITLE_UNKNOWN_SENDER_KEY, {
        "de": "Unbekannter Absender", "en": "Unknown sender", "fr": "Expéditeur inconnu",
    })
    register_notification_text(NEW_MESSAGE_BODY_KEY_BY_KIND["chat"], {
        "de": "{excerpt}", "en": "{excerpt}", "fr": "{excerpt}",
    })
    register_notification_text(NEW_MESSAGE_BODY_KEY_BY_KIND["attachment"], {
        "de": "hat einen Anhang gesendet", "en": "sent an attachment", "fr": "a envoyé une pièce jointe",
    })
    register_notification_text(NEW_MESSAGE_BODY_KEY_BY_KIND["poll"], {
        "de": "hat eine Umfrage gestartet", "en": "started a poll", "fr": "a lancé un sondage",
    })
    register_notification_text(NEW_MESSAGE_BODY_KEY_BY_KIND["announcement"], {
        "de": "hat eine Ankündigung veröffentlicht", "en": "posted an announcement", "fr": "a publié une annonce",
    })
    register_notification_text(NEW_MESSAGE_BODY_KEY_BY_KIND["system"], {
        "de": "Systemmeldung", "en": "System message", "fr": "Message système",
    })
    register_notification_text(NEW_MESSAGE_BODY_KEY_BY_KIND["deleted"], {
        "de": "Diese Nachricht wurde gelöscht", "en": "This message was deleted", "fr": "Ce message a été supprimé",
    })
    register_notification_text(NEW_MESSAGE_BODY_HIDDEN_KEY, {
        "de": "Neue Nachricht", "en": "New message", "fr": "Nouveau message",
    })
    _REGISTERED = True
