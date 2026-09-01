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
    def add_service(self, zc, type_, name):
        self._upsert(zc, type_, name)

    def update_service(self, zc, type_, name):
        self._upsert(zc, type_, name)

    def remove_service(self, zc, type_, name):
        # An mDNS "goodbye" (or a cache entry simply expiring) doesn't
        # reliably mean the node is actually down — a single missed
        # multicast packet triggers this too, and was causing nodes to
        # grey out on the grid while still being perfectly reachable.
        # Leave the offline decision to the sweep below, which confirms
        # with a real TCP connect before believing it.
        pass

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
        nodes_db.upsert_online(node_id, label, theme, ip, info.port)


def _is_reachable(ip: str, port: int, timeout: float = 2.0) -> bool:
    if not ip or not port:
        return False
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def _sweep_loop():
    while True:
        time.sleep(SWEEP_INTERVAL_SECONDS)
        try:
            _sweep_stale_nodes()
        except Exception as e:
            print("[discovery] sweep error:", e)


def _sweep_stale_nodes() -> None:
    """
    A node whose mDNS record hasn't refreshed in a while might just be a
    quiet network moment, not an actual outage — mDNS is UDP/multicast and
    drops packets. Before greying a tile out, double-check with a real TCP
    connect to its last-known address; only mark it offline if that also
    fails. If it's still reachable, bump last_seen so the sweep doesn't
    immediately re-flag it next tick.
    """
    cutoff = int(time.time()) - STALE_TIMEOUT_SECONDS
    for row in nodes_db.list_nodes(include_deleted=False):
        if not row["is_online"] or not row["last_seen"] or row["last_seen"] >= cutoff:
            continue
        if _is_reachable(row.get("ip"), row.get("port")):
            nodes_db.touch_last_seen(row["id"])
        else:
            nodes_db.mark_offline(row["id"])


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
