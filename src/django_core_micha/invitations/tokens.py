from django.conf import settings
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.utils.crypto import constant_time_compare
from django.utils.http import base36_to_int


class InviteTokenGenerator(PasswordResetTokenGenerator):
    """Generate invite tokens with their own key salt and lifetime."""

    key_salt = "django_core_micha.invitations.tokens.InviteTokenGenerator"

    def check_token(self, user, token):
        """Check an invite token using the configured invite lifetime."""
        if not (user and token):
            return False
        try:
            ts_b36, _ = token.split("-")
        except ValueError:
            return False
        try:
            ts = base36_to_int(ts_b36)
        except ValueError:
            return False
        for secret in [self.secret, *self.secret_fallbacks]:
            if constant_time_compare(self._make_token_with_timestamp(user, ts, secret), token):
                break
        else:
            return False
        timeout_days = getattr(settings, "INVITE_LINK_TIMEOUT_DAYS", 30)
        if (self._num_seconds(self._now()) - ts) > timeout_days * 86400:
            return False
        return True


invite_token_generator = InviteTokenGenerator()
