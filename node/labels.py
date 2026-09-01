# ============================================================
# FILE: labels.py
# Editable page-level text for this node (Settings > Labels).
# Defaults match the original StockPi wording so a fresh install
# looks the same as before; installs meant for a different kind
# of storage (tote bins, electronics parts, etc.) can rename any
# of these live from /settings without a restart.
# ============================================================

import json

import db

DEFAULT_LABELS = {
    "grocery_list": "Grocery List",
    "grocery_low_stock": "Grocery & Low Stock",
}

DEFAULT_THEME = "dark"
DEFAULT_NODE_LABEL = "Kitchen Inventory"


def get_labels() -> dict:
    raw = db.get_setting("labels_json")
    if not raw:
        return dict(DEFAULT_LABELS)
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return dict(DEFAULT_LABELS)
    merged = dict(DEFAULT_LABELS)
    if isinstance(data, dict):
        merged.update({k: v for k, v in data.items() if k in DEFAULT_LABELS})
    return merged


def set_label(key: str, value: str) -> None:
    if key not in DEFAULT_LABELS:
        raise ValueError(f"Unknown label key: {key}")
    labels = get_labels()
    labels[key] = value.strip() or DEFAULT_LABELS[key]
    db.set_setting("labels_json", json.dumps(labels))


def get_node_label() -> str:
    return db.get_setting("node_label", DEFAULT_NODE_LABEL)


def set_node_label(value: str) -> None:
    db.set_setting("node_label", value.strip() or DEFAULT_NODE_LABEL)


def get_theme() -> str:
    return db.get_setting("theme", DEFAULT_THEME)


def set_theme(value: str) -> None:
    db.set_setting("theme", value)


def get_launcher_url() -> str:
    return db.get_setting("launcher_url", "")


def set_launcher_url(value: str) -> None:
    db.set_setting("launcher_url", value.strip())


def get_advertise_ip() -> str:
    """Manual override for the IP this node broadcasts over mDNS (and
    that the launcher builds tile links from). Auto-detection picks
    whichever interface has the default route, which is normally the LAN
    — on a box that's also on a VPN/Tailscale, that's not reachable to
    anyone connecting to the launcher from off the LAN, so this lets you
    pin it to the VPN address instead. Blank = auto-detect (unchanged)."""
    return db.get_setting("advertise_ip", "")


def set_advertise_ip(value: str) -> None:
    db.set_setting("advertise_ip", value.strip())
