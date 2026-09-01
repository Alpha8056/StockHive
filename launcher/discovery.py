# ============================================================
# FILE: discovery.py
# Browses mDNS for StockPi nodes (_stockpi._tcp.local.) and keeps
# nodes.db in sync, and advertises the launcher itself
# (_stockhive._tcp.local.) so nodes can find their way back.
# ============================================================

import socket
import threading
import time

from zeroconf import ServiceInfo, ServiceBrowser, ServiceListener, Zeroconf

import nodes_db

NODE_SERVICE_TYPE = "_stockpi._tcp.local."
# DNS-SD service type labels are capped at 15 characters (RFC 6763) —
# "_stockpi-launcher" (16) silently failed registration because of this;
# "_stockhive" (9) stays under the limit.
LAUNCHER_SERVICE_TYPE = "_stockhive._tcp.local."

VERSION = "2.0.0"

# How long a node can go without a fresh mDNS record before the sweep
# marks it offline (catches missed "goodbye" packets).
STALE_TIMEOUT_SECONDS = 180
SWEEP_INTERVAL_SECONDS = 60

_zc = None
_launcher_service_info = None


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
    """Start browsing for nodes and advertising the launcher itself.

    These are two independent mDNS operations, each in its own
    try/except — previously one shared try block meant a failure
    registering the launcher's own service (e.g. a leftover registration
    from a killed/restarted worker) would also silently skip starting the
    node browser, or vice versa."""
    global _zc
    nodes_db.init_db()

    try:
        _zc = Zeroconf()
    except Exception as e:
        print("[discovery] Failed to start Zeroconf:", e)
        threading.Thread(target=_sweep_loop, daemon=True).start()
        return

    try:
        ServiceBrowser(_zc, NODE_SERVICE_TYPE, _NodeListener())
    except Exception as e:
        print("[discovery] Failed to start browsing for nodes:", e)

    global _launcher_service_info
    try:
        _launcher_service_info = ServiceInfo(
            LAUNCHER_SERVICE_TYPE,
            f"stockpi-launcher.{LAUNCHER_SERVICE_TYPE}",
            addresses=[socket.inet_aton(_local_ip())],
            port=launcher_port,
            properties={"version": VERSION},
            # No explicit `server` — see the matching comment in
            # node/mdns_advertise.py. It defaults to `name`, avoiding a
            # collision with a node's mDNS registration on the same host.
        )
        _zc.register_service(_launcher_service_info)
    except Exception as e:
        print("[discovery] Failed to advertise the launcher itself:", e)

    threading.Thread(target=_sweep_loop, daemon=True).start()


def get_debug_state() -> dict:
    """
    Diagnostic snapshot of this process's own mDNS state — used to tell
    apart 'we never actually registered anything' from 'we registered
    fine locally, but the announcement isn't reaching the network' (the
    latter points at a firewall/interface/multicast issue in this
    container rather than a code bug).
    """
    state = {
        "zeroconf_running": _zc is not None,
        "advertised_ip": _local_ip(),
        "attempted_registration": None,
        "self_lookup_succeeded": None,
        "known_nodes": nodes_db.list_nodes(include_deleted=False),
    }
    if _launcher_service_info is not None:
        state["attempted_registration"] = {
            "name": _launcher_service_info.name,
            "port": _launcher_service_info.port,
            "addresses": _launcher_service_info.parsed_addresses(),
        }
        if _zc is not None:
            try:
                info = _zc.get_service_info(LAUNCHER_SERVICE_TYPE, _launcher_service_info.name, timeout=3000)
                state["self_lookup_succeeded"] = info is not None
            except Exception as e:
                state["self_lookup_succeeded"] = f"error: {e}"
    return state
