# ============================================================
# FILE: discovery.py
# Browses mDNS for StockPi nodes (_stockpi._tcp.local.) and keeps
# nodes.db in sync, and advertises the launcher itself
# (_stockpi-launcher._tcp.local.) so nodes can find their way back.
# ============================================================

import socket
import threading
import time

from zeroconf import ServiceInfo, ServiceBrowser, ServiceListener, Zeroconf

import nodes_db

NODE_SERVICE_TYPE = "_stockpi._tcp.local."
LAUNCHER_SERVICE_TYPE = "_stockpi-launcher._tcp.local."

VERSION = "2.0.0"

# How long a node can go without a fresh mDNS record before the sweep
# marks it offline (catches missed "goodbye" packets).
STALE_TIMEOUT_SECONDS = 180
SWEEP_INTERVAL_SECONDS = 60

_zc = None


def _local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("1.1.1.1", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


class _NodeListener(ServiceListener):
    def __init__(self):
        # mDNS gives us only the instance name on removal, not the TXT
        # record, so remember name -> node_id from add/update to mark the
        # right row offline immediately instead of waiting on the sweep.
        self._name_to_id = {}

    def add_service(self, zc, type_, name):
        self._upsert(zc, type_, name)

    def update_service(self, zc, type_, name):
        self._upsert(zc, type_, name)

    def remove_service(self, zc, type_, name):
        node_id = self._name_to_id.pop(name, None)
        if node_id:
            nodes_db.mark_offline(node_id)

    def _upsert(self, zc, type_, name):
        info = zc.get_service_info(type_, name)
        if not info or not info.addresses:
            return
        props = {k.decode(): v.decode() for k, v in (info.properties or {}).items() if v is not None}
        node_id = props.get("id")
        if not node_id:
            return
        label = props.get("label") or "StockPi Node"
        theme = props.get("theme") or "dark"
        ip = socket.inet_ntoa(info.addresses[0])
        self._name_to_id[name] = node_id
        nodes_db.upsert_online(node_id, label, theme, ip, info.port)


def _sweep_loop():
    while True:
        time.sleep(SWEEP_INTERVAL_SECONDS)
        try:
            nodes_db.sweep_stale(STALE_TIMEOUT_SECONDS)
        except Exception as e:
            print("[discovery] sweep error:", e)


def start(launcher_port: int) -> None:
    """Start browsing for nodes and advertising the launcher itself."""
    global _zc
    nodes_db.init_db()
    try:
        _zc = Zeroconf()
        ServiceBrowser(_zc, NODE_SERVICE_TYPE, _NodeListener())

        launcher_info = ServiceInfo(
            LAUNCHER_SERVICE_TYPE,
            f"stockpi-launcher.{LAUNCHER_SERVICE_TYPE}",
            addresses=[socket.inet_aton(_local_ip())],
            port=launcher_port,
            properties={"version": VERSION},
            server=f"{socket.gethostname()}.local.",
        )
        _zc.register_service(launcher_info)
    except Exception as e:
        print("[discovery] Failed to start mDNS:", e)

    threading.Thread(target=_sweep_loop, daemon=True).start()
