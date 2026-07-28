"""Cloudflare Turnstile verification for self-service registration."""

from __future__ import annotations

import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings


TURNSTILE_SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def verify_turnstile_token(
    token, *, remoteip=None, allowed_hostnames=()
) -> tuple[bool, str]:
    """Verify a Turnstile response token, failing closed on every error."""
    if not token:
        return False, "Missing Turnstile token."

    try:
        payload = {"secret": getattr(settings, "TURNSTILE_SECRET_KEY", ""), "response": token}
        if remoteip:
            payload["remoteip"] = remoteip
        request = Request(
            TURNSTILE_SITEVERIFY_URL,
            data=urlencode(payload).encode("utf-8"),
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            result = json.load(response)
        if not result.get("success"):
            return False, "Turnstile verification failed."
        if allowed_hostnames and result.get("hostname", "").lower() not in {
            hostname.lower() for hostname in allowed_hostnames
        }:
            return False, "Turnstile hostname is not allowed."
    except Exception:
        return False, "Turnstile verification unavailable."

    return True, ""
