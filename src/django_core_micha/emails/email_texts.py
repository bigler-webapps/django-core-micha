from django.conf import settings

SUPPORTED_LANGUAGES = ("de", "fr", "en")

def get_project_name() -> str:
    """
    Project-Name aus Settings holen.
    Fallback: MFA_WEBAUTHN_RP_NAME oder 'Our service'.
    """
    return (
        getattr(settings, "PROJECT_NAME", None)
        or getattr(settings, "MFA_WEBAUTHN_RP_NAME", None)
        or "Our service"
    )

def get_preferred_language(user) -> str:
    """
    Sprache des Users bestimmen.
    Prüft user.profile.language, Fallback auf Settings.
    """
    lang = None
    profile = getattr(user, "profile", None)
    if profile and getattr(profile, "language", None):
        lang = profile.language

    if not lang:
        # Fallback: Settings-Language
        lang = getattr(settings, "LANGUAGE_CODE", "en")[:2]

    if lang not in SUPPORTED_LANGUAGES:
        lang = "en"
    return lang

def get_greeting_name(user) -> str:
    """
    Bevorzugt vollständiger Name, sonst E-Mail.
    """
    if hasattr(user, "get_full_name"):
        full_name = user.get_full_name().strip()
        if full_name:
            return full_name
            
    if getattr(user, "first_name", None) or getattr(user, "last_name", None):
        return f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '')}".strip()
        
    return getattr(user, "email", "") or "there"

# --------------------------------------
# Texte: Invite (neuer Benutzer)
# --------------------------------------

INVITE_SUBJECT = {
    "en": "You have been invited to {project_name}",
    "de": "Einladung zu {project_name}",
    "fr": "Invitation à {project_name}",
}

INVITE_BODY = {
    "en": (
        "Hello {name},\n\n"
        "You have been invited to {project_name}.\n\n"
        "To set your password and sign in for the first time, open the following link:\n"
        "{url}\n\n"
        "If you did not expect this email, you can ignore it.\n"
    ),
    "de": (
        "Hallo {name},\n\n"
        "Sie wurden zu {project_name} eingeladen.\n\n"
        "Um Ihr Passwort zu setzen und sich zum ersten Mal anzumelden, öffnen Sie bitte folgenden Link:\n"
        "{url}\n\n"
        "Falls Sie diese E-Mail nicht erwartet haben, können Sie diese E-Mail ignorieren.\n"
    ),
    "fr": (
        "Bonjour {name},\n\n"
        "Vous avez été invité(e) à rejoindre {project_name}.\n\n"
        "Pour définir votre mot de passe et vous connecter pour la première fois, ouvrez le lien suivant :\n"
        "{url}\n\n"
        "Si vous n'attendiez pas cet e-mail, vous pouvez l'ignorer.\n"
    ),
}

# --------------------------------------
# Texte: Passwort-Reset (bestehender User)
# --------------------------------------

RESET_SUBJECT = {
    "en": "Reset your password for {project_name}",
    "de": "Setzen Sie Ihr Passwort für {project_name} zurück",
    "fr": "Réinitialisez votre mot de passe pour {project_name}",
}

RESET_BODY = {
    "en": (
        "Hello {name},\n\n"
        "You requested to reset your password for {project_name}.\n\n"
        "To choose a new password, open this link:\n"
        "{url}\n\n"
        "If you did not request a reset, you can ignore this email.\n"
    ),
    "de": (
        "Hallo {name},\n\n"
        "Sie haben angefordert, Ihr Passwort für {project_name} zurückzusetzen.\n\n"
        "Um ein neues Passwort zu wählen, öffnen Sie bitte diesen Link:\n"
        "{url}\n\n"
        "Falls Sie dies nicht veranlasst haben, können Sie diese E-Mail ignorieren.\n"
    ),
    "fr": (
        "Bonjour {name},\n\n"
        "Vous avez demandé à réinitialiser votre mot de passe pour {project_name}.\n\n"
        "Pour choisir un nouveau mot de passe, ouvrez ce lien :\n"
        "{url}\n\n"
        "Si vous n'êtes pas à l'origine de cette demande, vous pouvez ignorer cet e-mail.\n"
    ),
}

# --------------------------------------
# Texte: MFA-Recovery-Link
# --------------------------------------

RECOVERY_SUBJECT = {
    "en": "Your sign-in recovery link for {project_name}",
    "de": "Ihr Anmelde-Wiederherstellungslink für {project_name}",
    "fr": "Votre lien de récupération de connexion pour {project_name}",
}

RECOVERY_BODY = {
    "en": (
        "Hello {name},\n\n"
        "Your sign-in recovery request for {project_name} has been approved.\n\n"
        "Use the following one-time link together with your usual password to sign in and review your security settings:\n"
        "{url}\n\n"
        "If you did not request this, please contact support immediately.\n"
    ),
    "de": (
        "Hallo {name},\n\n"
        "Ihre Anfrage zur Anmelde-Wiederherstellung für {project_name} wurde bestätigt.\n\n"
        "Verwenden Sie den folgenden einmaligen Link zusammen mit Ihrem gewohnten Passwort, um sich anzumelden und Ihre Sicherheitseinstellungen zu überprüfen:\n"
        "{url}\n\n"
        "Falls Sie dies nicht veranlasst haben, kontaktieren Sie bitte umgehend den Support.\n"
    ),
    "fr": (
        "Bonjour {name},\n\n"
        "Votre demande de récupération de connexion pour {project_name} a été approuvée.\n\n"
        "Utilisez le lien unique suivant avec votre mot de passe habituel pour vous connecter et vérifier vos paramètres de sécurité :\n"
        "{url}\n\n"
        "Si vous n'êtes pas à l'origine de cette demande, veuillez contacter le support immédiatement.\n"
    ),
}

def render_invite_email(user, url, language=None):
    project_name = get_project_name()
    lang = language or get_preferred_language(user)
    subject_tpl = INVITE_SUBJECT.get(lang, INVITE_SUBJECT["en"])
    body_tpl = INVITE_BODY.get(lang, INVITE_BODY["en"])
    ctx = {
        "name": get_greeting_name(user),
        "url": url,
        "project_name": project_name,
    }
    return subject_tpl.format(**ctx), body_tpl.format(**ctx)

def render_reset_email(user, url, language=None):
    project_name = get_project_name()
    lang = language or get_preferred_language(user)
    subject_tpl = RESET_SUBJECT.get(lang, RESET_SUBJECT["en"])
    body_tpl = RESET_BODY.get(lang, RESET_BODY["en"])
    ctx = {
        "name": get_greeting_name(user),
        "url": url,
        "project_name": project_name,
    }
    return subject_tpl.format(**ctx), body_tpl.format(**ctx)

def render_recovery_email(user, url, language=None):
    project_name = get_project_name()
    lang = language or get_preferred_language(user)
    subject_tpl = RECOVERY_SUBJECT.get(lang, RECOVERY_SUBJECT["en"])
    body_tpl = RECOVERY_BODY.get(lang, RECOVERY_BODY["en"])
    ctx = {
        "name": get_greeting_name(user),
        "url": url,
        "project_name": project_name,
    }
    return subject_tpl.format(**ctx), body_tpl.format(**ctx)