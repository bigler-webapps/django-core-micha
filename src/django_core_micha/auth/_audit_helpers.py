# src/django_core_micha/auth/_audit_helpers.py
import hashlib
import ipaddress


def _client_ip(request) -> str | None:
    if not request:
        return None
    # Prefer headers that cannot be forged by the client:
    # CF-Connecting-IP is set exclusively by Cloudflare (platform standard).
    # X-Real-IP is set by nginx when configured with proxy_set_header X-Real-IP.
    # REMOTE_ADDR is the immediate peer (nginx/proxy) — correct in local/non-CF envs.
    # X-Forwarded-For is intentionally NOT used: the leftmost entry is client-supplied
    # and trivially spoofable (S1).
    raw = (
        request.META.get("HTTP_CF_CONNECTING_IP")
        or request.META.get("HTTP_X_REAL_IP")
        or request.META.get("REMOTE_ADDR", "")
    ).strip()
    if not raw:
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
