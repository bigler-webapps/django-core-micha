# src/django_core_micha/auth/_audit_helpers.py
import hashlib
import ipaddress

from django_core_micha.auth.client_ip import resolve_client_ip


def _client_ip(request) -> str | None:
    if not request:
        return None
    raw = resolve_client_ip(request)
    if raw is None:
        return None
    try:
        addr = ipaddress.ip_address(raw)
        if isinstance(addr, ipaddress.IPv4Address):
            net = ipaddress.IPv4Network(f"{raw}/24", strict=False)
        else:
            net = ipaddress.IPv6Network(f"{raw}/48", strict=False)
        return str(net)
    except ValueError:
        return None


def _ua_family(request) -> str | None:
    if not request:
        return None
    ua = request.META.get("HTTP_USER_AGENT", "")
    if not ua:
        return None
    # Coarse bucketing — avoids storing the full UA string.
    browsers = ("Firefox", "Chrome", "Safari", "Edge", "Opera", "MSIE", "Trident")
    oses = ("Windows", "Linux", "Mac OS", "Android", "iOS", "iPhone", "iPad")
    browser = next((b for b in browsers if b in ua), "Unknown")
    os_name = next((o for o in oses if o in ua), "Unknown")
    # Trident means IE; normalise
    if browser == "Trident":
        browser = "MSIE"
    return f"{browser} on {os_name}"


def _session_key_digest(session_key: str | None) -> str | None:
    if not session_key:
        return None
    return hashlib.sha256(session_key.encode()).hexdigest()[:16]


def _credential_hash(credentials: dict | None) -> str | None:
    """sha256[:32] of the lowercased entered username/email — correlatable but not reversible.

    32 hex chars = 16 bytes = 128 bits. Short truncations (≤8 chars) are offline-preimage-
    attackable against a known email address population; 32 chars is the accepted minimum
    for a stored, queryable pseudonymisation value (S2).
    """
    if not credentials:
        return None
    value = credentials.get("username") or credentials.get("email") or ""
    if not value:
        return None
    return hashlib.sha256(value.lower().encode()).hexdigest()[:32]
