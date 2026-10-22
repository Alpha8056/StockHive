# ============================================================
# FILE: common_security.py
# Shared by node + launcher: a same-origin check for state-changing
# POSTs (stops another website from using your browser to trigger
# them) and salted-hash helpers for the admin token.
# ============================================================

import hashlib
import hmac
import secrets
from urllib.parse import urlsplit


def _hostname(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def same_origin_ok(req) -> bool:
    """False when the request clearly came from another site.

    Hostnames only, not ports: nginx forwards Host without the port, so a
    node on :8080 would otherwise never match its own pages. Requests that
    send neither Origin nor Referer (curl, scripts) pass this check — the
    admin token is what stops those once one is set."""
    if req.headers.get("Sec-Fetch-Site", "").lower() == "cross-site":
        return False
    own = (req.host or "").rsplit(":", 1)[0].strip("[]").lower()
    for header in ("Origin", "Referer"):
        value = req.headers.get(header)
        if value and value != "null":
            return _hostname(value) == own
    if req.headers.get("Origin") == "null":
        return False
    return True


def hash_token(token: str) -> str:
    """'salt$sha256hex' — the token itself is never stored."""
    salt = secrets.token_hex(16)
    digest = hashlib.sha256((salt + token).encode("utf-8")).hexdigest()
    return f"{salt}${digest}"


def token_matches(token: str, stored: str) -> bool:
    if not token or not stored or "$" not in stored:
        return False
    salt, digest = stored.split("$", 1)
    candidate = hashlib.sha256((salt + token).encode("utf-8")).hexdigest()
    return hmac.compare_digest(candidate, digest)


def bearer_token(req) -> str:
    auth = req.headers.get("Authorization", "")
    return auth[7:].strip() if auth.startswith("Bearer ") else ""
