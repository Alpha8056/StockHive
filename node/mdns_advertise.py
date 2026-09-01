# ============================================================
# FILE: mdns_advertise.py
# Publishes this node on the LAN via mDNS (zeroconf) so the
# launcher can discover it automatically, and listens for the
# launcher's own advertisement so this node can show a working
# "back to launcher" link without any manual IP entry.
# ============================================================

import atexit
import socket
import threading

from zeroconf import ServiceInfo, ServiceBrowser, ServiceListener, Zeroconf

import db
import labels

NODE_SERVICE_TYPE = "_stockpi._tcp.local."
# Must match launcher/discovery.py exactly. DNS-SD service type labels
# are capped at 15 characters (RFC 6763) — "_stockpi-launcher" (16) would
# silently fail registration on the launcher's side because of this;
# "_stockhive" (9) stays under the limit.
LAUNCHER_SERVICE_TYPE = "_stockhive._tcp.local."

VERSION = "2.0.0"

_zc = None
_service_info = None
_registered_port = None
_lock = threading.Lock()
_discovered_launcher_url = None


def _local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("1.1.1.1", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _build_service_info(port: int) -> ServiceInfo:
    node_id = db.get_node_id()
    name = f"{node_id}.{NODE_SERVICE_TYPE}"
    return ServiceInfo(
        NODE_SERVICE_TYPE,
        name,
        addresses=[socket.inet_aton(_local_ip())],
        port=port,
        properties={
            "id": node_id,
            "label": labels.get_node_label(),
            "theme": labels.get_theme(),
            "version": VERSION,
        },
        # No explicit `server` — it defaults to `name`, which is already
        # globally unique (includes node_id). Setting it to the OS
        # hostname is wrong when a node and a launcher share one machine:
        # both would register the exact same "server" identity from two
        # independent Zeroconf instances, and the second registration can
        # silently fail to actually announce.
    )


class _LauncherListener(ServiceListener):
    def add_service(self, zc, type_, name):
        self._update(zc, type_, name)

    def update_service(self, zc, type_, name):
        self._update(zc, type_, name)

    def remove_service(self, zc, type_, name):
        # mDNS "goodbye" / cache-expiry events are common and don't
        # reliably mean the launcher is actually gone (a single missed
        # multicast packet triggers this too) — keep the last known URL
        # rather than dropping the back-button link on every blip. If the
        # launcher really did move, the next add_service/update_service
        # overwrites this with the new address.
        pass

    def _update(self, zc, type_, name):
        global _discovered_launcher_url
        info = zc.get_service_info(type_, name)
        if not info or not info.addresses:
            return
        ip = socket.inet_ntoa(info.addresses[0])
        with _lock:
            _discovered_launcher_url = f"http://{ip}:{info.port}"


def start(port: int) -> None:
    """Register this node on mDNS and start browsing for the launcher.
    Safe to call once at app startup; failures here should never stop
    the app from serving inventory pages (e.g. mDNS blocked/unavailable).

    Advertising ourselves and browsing for the launcher are independent
    operations, each in their own try/except — previously they shared one
    try block, so if register_service() ever raised (e.g. a leftover
    registration from a killed/restarted worker), the ServiceBrowser for
    the launcher never even got created, and the "Apps" link would stay
    broken until the process restarted cleanly."""
    global _zc, _service_info, _registered_port
    try:
        _zc = Zeroconf()
    except Exception as e:
        print("[mDNS] Failed to start Zeroconf:", e)
        return

    # Without an explicit close(), zeroconf's background socket thread can
    # occasionally hold gunicorn's worker process open past its graceful
    # shutdown window on `systemctl restart`. atexit fires during
    # gunicorn's normal clean-shutdown path on SIGTERM.
    atexit.register(_zc.close)

    try:
        _service_info = _build_service_info(port)
        _registered_port = port
        _zc.register_service(_service_info)
    except Exception as e:
        print("[mDNS] Failed to advertise this node:", e)

    try:
        ServiceBrowser(_zc, LAUNCHER_SERVICE_TYPE, _LauncherListener())
    except Exception as e:
        print("[mDNS] Failed to start browsing for the launcher:", e)


def refresh() -> None:
    """Re-publish after label/theme changes so the TXT record updates
    immediately instead of waiting on mDNS cache TTL expiry."""
    global _service_info
    if _zc is None or _registered_port is None:
        return
    try:
        if _service_info is not None:
            _zc.unregister_service(_service_info)
        _service_info = _build_service_info(_registered_port)
        _zc.register_service(_service_info)
    except Exception as e:
        print("[mDNS] Failed to refresh advertisement:", e)


def get_launcher_url() -> str:
    manual = labels.get_launcher_url()
    if manual:
        return manual
    with _lock:
        return _discovered_launcher_url or ""
