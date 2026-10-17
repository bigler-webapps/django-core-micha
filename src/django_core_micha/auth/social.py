"""Shared helpers for social login identity checks."""


def provider_vouched_email(sociallogin):
    """Return the first email address the identity provider verified."""
    for address in sociallogin.email_addresses:
        if address.verified:
            return address.email
    return None
