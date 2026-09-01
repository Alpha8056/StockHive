# ============================================================
# FILE: nodes_db.py
# SQLite store of every StockPi node the launcher has discovered
# over mDNS, plus online/offline state and the permanent-delete
# blocklist for Settings > Manage Nodes.
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
