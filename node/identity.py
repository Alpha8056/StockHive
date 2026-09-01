# ============================================================
# FILE: identity.py
# Thin facade over db/labels/mdns_advertise for this node's
# identity (id, label, theme, launcher link) as used by the
# Settings page and the "Apps" back-button.
# ============================================================

import db
import labels
import mdns_advertise


def snapshot() -> dict:
    return {
        "id": db.get_node_id(),
        "label": labels.get_node_label(),
        "theme": labels.get_theme(),
        "launcher_url": mdns_advertise.get_launcher_url(),
    }


def update_identity(label: str = None, theme: str = None) -> None:
    """Save label/theme changes and immediately re-announce over mDNS
    so the launcher's tile updates without waiting on TTL expiry."""
    changed = False
    if label is not None:
        labels.set_node_label(label)
        changed = True
    if theme is not None:
        labels.set_theme(theme)
        changed = True
    if changed:
        mdns_advertise.refresh()
