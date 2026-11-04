# ============================================================
# FILE: tls_info.py
# Read-only view of this node's HTTPS setup, for the Settings page,
# the /ca.crt download and the mDNS announcement.
#
# Everything lives outside the repo in /etc/stockhive/tls/, written by
# scripts/https-setup.sh (so Settings > Update never touches it):
#   https.env  HTTPS_ENABLED / HTTPS_PORT       (world-readable)
#   node.crt   this node's certificate         (world-readable)
#   ca.crt     the PUBLIC CA certificate       (world-readable)
#   node.key   private key, root-only. Never read here.
#
# Certificate details come from the `openssl` command (no extra Python
# packages); results are cached until the file changes.
# ============================================================

import base64
import datetime
import hashlib
import os
import re
import subprocess

TLS_DIR = os.environ.get("STOCKHIVE_TLS_DIR", "/etc/stockhive/tls")
ENV_PATH = os.path.join(TLS_DIR, "https.env")
CERT_PATH = os.path.join(TLS_DIR, "node.crt")
CA_PATH = os.path.join(TLS_DIR, "ca.crt")

WARN_DAYS = 30
CA_MAX_BYTES = 16 * 1024

_PEM_RE = re.compile(rb"-----BEGIN CERTIFICATE-----\s+([A-Za-z0-9+/=\s]+?)-----END CERTIFICATE-----")
_cache = {}


def _stamp(path):
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _read_env() -> dict:
    out = {}
    try:
        with open(ENV_PATH, encoding="utf-8") as f:
            for line in f:
                m = re.match(r'^([A-Z_]+)="?([^"\n]*)"?\s*$', line)
                if m:
                    out[m.group(1)] = m.group(2)
    except OSError:
        pass
    return out


def https_port():
    """The HTTPS port if HTTPS is switched on and a certificate exists,
    else None."""
    env = _read_env()
    if env.get("HTTPS_ENABLED") != "1" or not os.path.isfile(CERT_PATH):
        return None
    port = env.get("HTTPS_PORT", "443")
    return int(port) if port.isdigit() and 0 < int(port) < 65536 else None


def ca_pem():
    """The public CA certificate as bytes, or None. Refuses anything that
    isn't exactly certificates (never serves a key by mistake)."""
    try:
        if os.path.getsize(CA_PATH) > CA_MAX_BYTES:
            return None
        with open(CA_PATH, "rb") as f:
            data = f.read()
    except OSError:
        return None
    if b"PRIVATE KEY" in data or not _PEM_RE.search(data):
        return None
    return data


def ca_fingerprint():
    """SHA-256 fingerprint of the CA certificate, as shown by
    `openssl x509 -fingerprint -sha256` (AA:BB:...)."""
    data = ca_pem()
    if not data:
        return None
    m = _PEM_RE.search(data)
    try:
        der = base64.b64decode(b"".join(m.group(1).split()))
    except Exception:
        return None
    h = hashlib.sha256(der).hexdigest().upper()
    return ":".join(h[i:i + 2] for i in range(0, len(h), 2))


def cert_info():
    """{"expires": datetime (UTC), "days_left": int, "dns": [...], "ips": [...]}
    for this node's certificate, or None if there isn't a readable one."""
    stamp = _stamp(CERT_PATH)
    if stamp is None:
        return None
    hit = _cache.get("cert")
    if hit and hit[0] == stamp:
        parsed = hit[1]
    else:
        parsed = _parse_cert()
        _cache["cert"] = (stamp, parsed)
    if not parsed:
        return None
    info = dict(parsed)
    now = datetime.datetime.now(datetime.timezone.utc)
    info["days_left"] = (info["expires"] - now).days
    return info


def _parse_cert():
    try:
        r = subprocess.run(
            ["openssl", "x509", "-in", CERT_PATH, "-noout", "-enddate", "-ext", "subjectAltName"],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        return None
    if r.returncode != 0:
        return None
    m = re.search(r"notAfter=(.+)", r.stdout)
    if not m:
        return None
    try:
        expires = datetime.datetime.strptime(" ".join(m.group(1).split()), "%b %d %H:%M:%S %Y %Z")
    except ValueError:
        return None
    expires = expires.replace(tzinfo=datetime.timezone.utc)
    return {
        "expires": expires,
        "dns": re.findall(r"DNS:([^,\s]+)", r.stdout),
        "ips": re.findall(r"IP Address:([0-9.]+)", r.stdout),
    }
