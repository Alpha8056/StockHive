# ============================================================
# FILE: mdns_advertise.py
# Publishes this node on the LAN via mDNS (zeroconf) so the
# launcher can discover it automatically, and listens for the
# launcher's own advertisement so this node can show a working
# "back to launcher" link without any manual IP entry.
# ============================================================

import atexit
import ipaddress
import socket
import threading
import time

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

# While the network isn't up yet at startup, check this often, for this
# long; after that (and once registered), check once a minute.
STARTUP_RETRY_SECONDS = 5
STARTUP_WAIT_SECONDS = 120
CHECK_INTERVAL_SECONDS = 60

_zc = None
_service_info = None
_browser = None
_registered_port = None
_registered_ip = None    # address in the live registration, or None
_zc_lan_ip = None        # LAN address detected when _zc was created
# Guards every _zc operation: the background check thread and refresh()
# (called from a Settings save) must never use zeroconf at the same time.
_zc_lock = threading.RLock()
_waiting_logged = False

_lock = threading.Lock()
_discovered_launcher_url = None


def detected_lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("1.1.1.1", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def is_usable_ip(ip: str) -> bool:
    """False for addresses other machines can't use to reach this node:
    loopback (127.x, what detection returns before the network is up) and
    link-local (169.254.x, what a machine gives itself when DHCP fails)."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (addr.is_loopback or addr.is_link_local or addr.is_unspecified)


def advertised_ip() -> str:
    """The IP to broadcast over mDNS: the manual override from Settings if
    one is set, otherwise whatever interface has the default route
    (normally the LAN, not a VPN like Tailscale)."""
    return labels.get_advertise_ip() or detected_lan_ip()


def registered_ip():
    """The address actually in the live mDNS registration, or None while
    it's still waiting for the network (shown on the Settings page)."""
    return _registered_ip


def _build_service_info(port: int, ip: str) -> ServiceInfo:
    node_id = db.get_node_id()
    name = f"{node_id}.{NODE_SERVICE_TYPE}"
    return ServiceInfo(
        NODE_SERVICE_TYPE,
        name,
        addresses=[socket.inet_aton(ip)],
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
    """Kicks off mDNS in a background thread and returns immediately.

    This used to run synchronously at import time, which meant gunicorn
    couldn't finish booting the worker (and the app couldn't serve any
    page at all, including plain inventory scanning) until mDNS
    registration finished. mDNS should never be able to block the app
    from serving its actual job, so everything here — the startup wait,
    registration and the once-a-minute address check — runs off-thread
    with every error caught and logged."""
    global _registered_port
    _registered_port = port
    threading.Thread(target=_run, args=(port,), daemon=True).start()


def _run(port: int) -> None:
    # Without an explicit close(), zeroconf's background socket thread can
    # occasionally hold gunicorn's worker process open past its graceful
    # shutdown window on `systemctl restart`. atexit fires during
    # gunicorn's normal clean-shutdown path on SIGTERM.
    atexit.register(_close_current)

    startup_deadline = time.monotonic() + STARTUP_WAIT_SECONDS
    while True:
        try:
            _check(port)
        except Exception as e:
            print("[mDNS] Address check failed:", e)
        starting = _registered_ip is None and time.monotonic() < startup_deadline
        time.sleep(STARTUP_RETRY_SECONDS if starting else CHECK_INTERVAL_SECONDS)


def _check(port: int) -> None:
    """One pass of the background check.

    zeroconf binds its sending sockets to the addresses this machine has
    when Zeroconf() is created, so it's only created once the network is
    up, and recreated (not updated in place) when the LAN address
    changes — an in-place update would keep sending from a socket bound
    to an address that no longer exists."""
    global _waiting_logged
    override = labels.get_advertise_ip()
    detected = detected_lan_ip()
    network_up = is_usable_ip(detected)

    with _zc_lock:
        if _zc is None:
            if override:
                # A manual override is registered right away, without
                # waiting for the network. If the network wasn't up yet,
                # the branch below re-announces once it is.
                _start_zeroconf(port, override, detected)
            elif network_up:
                _start_zeroconf(port, detected, detected)
            elif not _waiting_logged:
                print(f"[mDNS] Waiting for network (detected {detected})…")
                _waiting_logged = True
            return

        if network_up and detected != _zc_lan_ip:
            if override:
                print(f"[mDNS] Network address changed {_zc_lan_ip} -> {detected}; re-announcing {override}")
            else:
                print(f"[mDNS] Address changed {_zc_lan_ip} -> {detected}")
            _restart_zeroconf(port, override or detected, detected)
        elif _registered_ip is None and (override or network_up):
            # An earlier registration attempt failed; try again.
            _restart_zeroconf(port, override or detected, detected)


def _start_zeroconf(port: int, ip: str, lan_ip: str) -> None:
    """Create zeroconf, advertise this node at `ip`, and browse for the
    launcher. Each step has its own try/except: a failed registration
    must not stop the launcher browser from starting, or vice versa.
    Caller holds _zc_lock."""
    global _zc, _service_info, _browser, _registered_ip, _zc_lan_ip
    try:
        _zc = Zeroconf()
    except Exception as e:
        print("[mDNS] Failed to start Zeroconf:", e)
        return
    _zc_lan_ip = lan_ip

    try:
        info = _build_service_info(port, ip)
        _zc.register_service(info)
        _service_info = info
        _registered_ip = ip
        print(f"[mDNS] Advertising {ip}:{port}")
    except Exception as e:
        print("[mDNS] Failed to advertise this node:", e)

    try:
        _browser = ServiceBrowser(_zc, LAUNCHER_SERVICE_TYPE, _LauncherListener())
    except Exception as e:
        print("[mDNS] Failed to start browsing for the launcher:", e)


def _stop_zeroconf() -> None:
    """close() also sends a "goodbye" for our registration. Caller holds
    _zc_lock."""
    global _zc, _service_info, _browser, _registered_ip, _zc_lan_ip
    zc = _zc
    _zc = _service_info = _browser = _registered_ip = _zc_lan_ip = None
    if zc is not None:
        try:
            zc.close()
        except Exception as e:
            print("[mDNS] Failed to close Zeroconf:", e)


def _restart_zeroconf(port: int, ip: str, lan_ip: str) -> None:
    _stop_zeroconf()
    _start_zeroconf(port, ip, lan_ip)


def _close_current() -> None:
    with _zc_lock:
        _stop_zeroconf()


def refresh() -> None:
    """Re-publish after label/theme/override changes so the TXT record and
    address update immediately instead of waiting on mDNS cache TTL
    expiry. Before the first registration this does nothing: the
    background check registers with the new values."""
    global _service_info, _registered_ip
    with _zc_lock:
        if _zc is None or _registered_port is None:
            return
        ip = advertised_ip()
        if not is_usable_ip(ip):
            ip = _registered_ip   # cleared override, network down: keep the current address
        if not ip:
            return
        try:
            if _service_info is not None:
                _zc.unregister_service(_service_info)
            _service_info = None
            _registered_ip = None
            _service_info = _build_service_info(_registered_port, ip)
            _zc.register_service(_service_info)
            _registered_ip = ip
        except Exception as e:
            print("[mDNS] Failed to refresh advertisement:", e)


def get_launcher_url() -> str:
    manual = labels.get_launcher_url()
    if manual:
        return manual
    with _lock:
        return _discovered_launcher_url or ""
