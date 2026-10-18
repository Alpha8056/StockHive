# ============================================================
# FILE: nodes_db.py
# SQLite store of every StockPi node the launcher has discovered
# over mDNS, plus online/offline state and the permanent-delete
# blocklist for Settings > Manage Nodes. Also holds the
# Settings > Saved Dates entries shown on carousel page 3.
# ============================================================

import sqlite3
import time

DB_NAME = "nodes.db"


def _connect():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = _connect()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS nodes (
                id TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                theme TEXT NOT NULL DEFAULT 'dark',
                ip TEXT,
                port INTEGER,
                first_seen INTEGER,
                last_seen INTEGER,
                is_online INTEGER NOT NULL DEFAULT 1,
                offline_since INTEGER,
                is_deleted INTEGER NOT NULL DEFAULT 0
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS saved_dates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                date TEXT NOT NULL,
                repeats_yearly INTEGER NOT NULL DEFAULT 1,
                created_at INTEGER
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS launcher_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
        """)
        conn.commit()
    finally:
        conn.close()


def upsert_online(node_id: str, label: str, theme: str, ip: str, port: int) -> None:
    now = int(time.time())
    conn = _connect()
    try:
        conn.execute("""
            INSERT INTO nodes (id, label, theme, ip, port, first_seen, last_seen, is_online, offline_since)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, NULL)
            ON CONFLICT(id) DO UPDATE SET
                label = excluded.label,
                theme = excluded.theme,
                ip = excluded.ip,
                port = excluded.port,
                last_seen = excluded.last_seen,
                is_online = 1,
                offline_since = NULL;
        """, (node_id, label, theme, ip, port, now, now))
        conn.commit()
    finally:
        conn.close()


def mark_offline(node_id: str) -> None:
    conn = _connect()
    try:
        conn.execute("""
            UPDATE nodes SET is_online = 0, offline_since = ?
            WHERE id = ? AND is_online = 1;
        """, (int(time.time()), node_id))
        conn.commit()
    finally:
        conn.close()


def touch_last_seen(node_id: str) -> None:
    """Bumps last_seen without needing a fresh mDNS record — used when the
    sweep's active reachability check confirms a node with a stale mDNS
    record is still actually up, so it isn't re-checked every tick."""
    conn = _connect()
    try:
        conn.execute("UPDATE nodes SET last_seen = ? WHERE id = ?;", (int(time.time()), node_id))
        conn.commit()
    finally:
        conn.close()


def list_nodes(include_deleted: bool = False):
    conn = _connect()
    try:
        if include_deleted:
            rows = conn.execute("SELECT * FROM nodes ORDER BY label;").fetchall()
        else:
            rows = conn.execute("SELECT * FROM nodes WHERE is_deleted = 0 ORDER BY label;").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def is_deleted(node_id: str) -> bool:
    conn = _connect()
    try:
        row = conn.execute("SELECT is_deleted FROM nodes WHERE id = ?;", (node_id,)).fetchone()
        return bool(row and row["is_deleted"])
    finally:
        conn.close()


def delete_node(node_id: str) -> None:
    """Permanently hides a node's tile. If it's already known, flag it;
    otherwise insert a deleted placeholder so it stays hidden even if it
    hasn't broadcast yet (defensive; upsert_online always runs first in
    practice)."""
    conn = _connect()
    try:
        cur = conn.execute("UPDATE nodes SET is_deleted = 1 WHERE id = ?;", (node_id,))
        if cur.rowcount == 0:
            conn.execute("""
                INSERT INTO nodes (id, label, theme, is_online, is_deleted)
                VALUES (?, '', 'dark', 0, 1);
            """, (node_id,))
        conn.commit()
    finally:
        conn.close()


# ============================================================
# SECTION: Saved Dates (Settings > Saved Dates)
# date is stored as ISO YYYY-MM-DD. repeats_yearly entries recur on
# that month/day every year; one-time entries only on that exact date.
# ============================================================

def list_saved_dates():
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, name, date, repeats_yearly FROM saved_dates ORDER BY substr(date, 6), name;"
        ).fetchall()
        return [dict(r, repeats_yearly=bool(r["repeats_yearly"])) for r in rows]
    finally:
        conn.close()


def add_saved_date(name: str, date_iso: str, repeats_yearly: bool) -> None:
    conn = _connect()
    try:
        conn.execute("""
            INSERT INTO saved_dates (name, date, repeats_yearly, created_at)
            VALUES (?, ?, ?, ?);
        """, (name, date_iso, 1 if repeats_yearly else 0, int(time.time())))
        conn.commit()
    finally:
        conn.close()


def update_saved_date(date_id: int, name: str, date_iso: str, repeats_yearly: bool) -> None:
    conn = _connect()
    try:
        conn.execute("""
            UPDATE saved_dates SET name = ?, date = ?, repeats_yearly = ?
            WHERE id = ?;
        """, (name, date_iso, 1 if repeats_yearly else 0, date_id))
        conn.commit()
    finally:
        conn.close()


def delete_saved_date(date_id: int) -> None:
    conn = _connect()
    try:
        conn.execute("DELETE FROM saved_dates WHERE id = ?;", (date_id,))
        conn.commit()
    finally:
        conn.close()


# ============================================================
# SECTION: Launcher Settings (key/value — Settings > Display)
# ============================================================

def get_settings() -> dict:
    conn = _connect()
    try:
        rows = conn.execute("SELECT key, value FROM launcher_settings;").fetchall()
        return {r["key"]: r["value"] for r in rows}
    finally:
        conn.close()


def set_settings(values: dict) -> None:
    conn = _connect()
    try:
        conn.executemany("""
            INSERT INTO launcher_settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value;
        """, [(k, str(v)) for k, v in values.items()])
        conn.commit()
    finally:
        conn.close()
