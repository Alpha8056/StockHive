# ============================================================
# FILE: mdns_advertise.py
# Publishes this node on the LAN via mDNS (zeroconf) so the
# launcher can discover it automatically, and listens for the
# launcher's own advertisement so this node can show a working
# "back to launcher" link without any manual IP entry.
# ============================================================

import socket
import threading

from zeroconf import ServiceInfo, ServiceBrowser, ServiceListener, Zeroconf

import db
import labels

NODE_SERVICE_TYPE = "_stockpi._tcp.local."
LAUNCHER_SERVICE_TYPE = "_stockpi-launcher._tcp.local."

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
        server=f"{socket.gethostname()}.local.",
    )


class _LauncherListener(ServiceListener):
    def add_service(self, zc, type_, name):
        self._update(zc, type_, name)

    def update_service(self, zc, type_, name):
        self._update(zc, type_, name)

    def remove_service(self, zc, type_, name):
        global _discovered_launcher_url
        with _lock:
            _discovered_launcher_url = None

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
    the app from serving inventory pages (e.g. mDNS blocked/unavailable)."""
    global _zc, _service_info, _registered_port
    try:
        _zc = Zeroconf()
        _service_info = _build_service_info(port)
        _registered_port = port
        _zc.register_service(_service_info)
        ServiceBrowser(_zc, LAUNCHER_SERVICE_TYPE, _LauncherListener())
    except Exception as e:
        print("[mDNS] Failed to start advertising:", e)


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
