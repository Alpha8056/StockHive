# ============================================================
# FILE: inventory.py
# StockPi — Inventory Logic + Events + Stats + Debug
# + Auto schema ensure (prevents missing-table crashes)
# + Barcode Alias Support (multiple UPCs -> one item)
# ============================================================

# ============================================================
# SECTION: Imports
# ============================================================
import os
import sqlite3
from datetime import datetime, timedelta

# ============================================================
# SECTION: Paths / DB
# ============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "inventory.db")

# ============================================================
# SECTION: Schema Ensure (prevents missing-table crashes)
# ============================================================

def _ensure_schema(conn: sqlite3.Connection):
    """
    Creates tables if missing. Safe to run on every connect.
    Prevents 500s like 'no such table: event_log'.
    """
    cur = conn.cursor()

    # event_log used by stats and logging
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS event_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            barcode TEXT,
            event_type TEXT NOT NULL,
            delta INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'ui'
        );
        """
    )

    # barcode aliases: multiple UPCs mapping to a single item row
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS barcode_aliases (
            barcode TEXT PRIMARY KEY,
            item_id INTEGER NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(item_id) REFERENCES items(id) ON DELETE CASCADE
        );
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_barcode_aliases_item_id
        ON barcode_aliases(item_id);
        """
    )

    conn.commit()


# ============================================================
# SECTION: DB Helpers
# ============================================================

def _connect():
    """
    One connection per operation.
    timeout helps if the Pi is briefly busy.
    row_factory gives dict-like rows.
    """
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row

    # Safer concurrency settings for SQLite on Pi
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL;")
    cur.execute("PRAGMA synchronous=NORMAL;")
    cur.execute("PRAGMA busy_timeout=8000;")  # ms
    conn.commit()

    # Ensure tables exist so routes don't crash
    try:
        _ensure_schema(conn)
    except Exception:
        # If items table doesn't exist yet (fresh DB), don't crash app here
        pass

    return conn


def _now_utc_iso():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


# ============================================================
# SECTION: Barcode Alias Resolution
# ============================================================

def resolve_barcode(barcode: str):
    """
    Returns canonical barcode from items table.
    Checks:
      1) items.barcode == barcode
      2) barcode_aliases.barcode == barcode -> mapped item -> canonical barcode

    Returns None if not found.
    """
    barcode = (barcode or "").strip()
    if not barcode:
        return None

    conn = _connect()
    try:
        cur = conn.cursor()

        # Direct hit
        cur.execute("SELECT barcode FROM items WHERE barcode = ?;", (barcode,))
        row = cur.fetchone()
        if row:
            return row["barcode"]

        # Alias hit
        cur.execute(
            """
            SELECT i.barcode AS barcode
            FROM barcode_aliases a
            JOIN items i ON i.id = a.item_id
            WHERE a.barcode = ?;
            """,
            (barcode,),
        )
        row = cur.fetchone()
        if row:
            return row["barcode"]

        return None
    finally:
        conn.close()


def add_barcode_alias(alias_barcode: str, canonical_barcode: str):
    """
    Map an alias UPC to an existing item (canonical barcode).
    After this, scanning alias_barcode behaves as scanning canonical_barcode.
    """
    alias_barcode = (alias_barcode or "").strip()
    canonical_barcode = (canonical_barcode or "").strip()

    if not alias_barcode or not canonical_barcode:
        raise ValueError("Both alias_barcode and canonical_barcode are required")

    conn = _connect()
    try:
        cur = conn.cursor()

        # canonical must exist in items
        cur.execute("SELECT id FROM items WHERE barcode = ?;", (canonical_barcode,))
        item = cur.fetchone()
        if not item:
            raise ValueError("Canonical item not found")

        # If alias barcode is itself a canonical item barcode, don't allow mapping
        cur.execute("SELECT 1 FROM items WHERE barcode = ?;", (alias_barcode,))
        if cur.fetchone():
            raise ValueError("Alias barcode already exists as a primary item barcode")

        cur.execute(
            """
            INSERT OR REPLACE INTO barcode_aliases (barcode, item_id)
            VALUES (?, ?);
            """,
            (alias_barcode, item["id"]),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def get_aliases_for_barcode(canonical_barcode: str):
    """
    Returns list of alias barcodes linked to this canonical item.
    """
    canonical_barcode = (canonical_barcode or "").strip()
    if not canonical_barcode:
        return []

    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM items WHERE barcode = ?;", (canonical_barcode,))
        item = cur.fetchone()
        if not item:
            return []

        cur.execute(
            """
            SELECT barcode
            FROM barcode_aliases
            WHERE item_id = ?
            ORDER BY created_at DESC;
            """,
            (item["id"],),
        )
        rows = cur.fetchall()
        return [r["barcode"] for r in rows]
    finally:
        conn.close()


# ============================================================
# SECTION: Internal Helpers
# ============================================================

def _log_event(cur, barcode, event_type, delta=0, source="ui"):
    """
    Writes to event_log table. If table doesn't exist for some reason,
    silently skip (keeps app alive).
    """
    try:
        cur.execute(
            """
            INSERT INTO event_log (created_at, barcode, event_type, delta, source)
            VALUES (?, ?, ?, ?, ?);
            """,
            (_now_utc_iso(), barcode, event_type, int(delta), source),
        )
    except Exception:
        pass


def _sync_grocery(cur, item_id, qty):
    """
    Keeps grocery_list in sync:
      - if qty <= 0 => ensure item is in grocery_list
      - if qty > 0  => remove from grocery_list
    IMPORTANT: uses SAME cursor/transaction as caller to avoid DB locks.
    """
    if qty <= 0:
        cur.execute(
            """
            INSERT OR IGNORE INTO grocery_list (item_id, added_date)
            VALUES (?, CURRENT_TIMESTAMP);
            """,
            (item_id,),
        )
    else:
        cur.execute("DELETE FROM grocery_list WHERE item_id = ?;", (item_id,))


# ============================================================
# SECTION: Core Queries
# ============================================================

def get_item_by_barcode(barcode: str):
    """
    Returns:
      (barcode, name, location, quantity, low_threshold, expiration_date)
    or None

    Supports alias barcodes.
    """
    barcode = (barcode or "").strip()
    if not barcode:
        return None

    canonical = resolve_barcode(barcode)
    if canonical:
        barcode = canonical

    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT barcode, name, location, quantity, low_threshold, expiration_date
            FROM items
            WHERE barcode = ?;
            """,
            (barcode,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return (row["barcode"], row["name"], row["location"], row["quantity"], row["low_threshold"], row["expiration_date"])
    finally:
        conn.close()


def get_inventory():
    """
    Returns list of tuples:
      (barcode, name, location, quantity, low_threshold, expiration_date)
    """
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT barcode, name, location, quantity, low_threshold, expiration_date
            FROM items
            ORDER BY name COLLATE NOCASE;
            """
        )
        rows = cur.fetchall()
        return [(r["barcode"], r["name"], r["location"], r["quantity"], r["low_threshold"], r["expiration_date"]) for r in rows]
    finally:
        conn.close()


def get_grocery_list():
    """
    Returns list of tuples:
      (barcode, name)
    """
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT i.barcode as barcode, i.name as name
            FROM grocery_list g
            JOIN items i ON i.id = g.item_id
            ORDER BY g.added_date DESC;
            """
        )
        rows = cur.fetchall()
        return [(r["barcode"], r["name"]) for r in rows]
    finally:
        conn.close()


# ============================================================
# SECTION: Add / Update Inventory
# ============================================================

def add_item(barcode: str, name: str, location: str):
    """
    Adds a new item (qty starts at 1).
    Raises ValueError on bad input.
    """
    barcode = (barcode or "").strip()
    name = (name or "").strip()
    location = (location or "").strip()

    if not barcode:
        raise ValueError("Barcode required")
    if not name:
        raise ValueError("Name required")
    if not location:
        raise ValueError("Location required")

    conn = _connect()
    try:
        cur = conn.cursor()

        # Insert new item with qty=1
        cur.execute(
            """
            INSERT INTO items (barcode, name, location, quantity)
            VALUES (?, ?, ?, 1);
            """,
            (barcode, name, location),
        )

        # Fetch id for grocery sync/logging
        cur.execute("SELECT id, quantity FROM items WHERE barcode = ?;", (barcode,))
        row = cur.fetchone()
        if row:
            _sync_grocery(cur, row["id"], row["quantity"])
        _log_event(cur, barcode, "add_new", delta=1, source="ui")

        conn.commit()
    finally:
        conn.close()


def increment_existing(barcode: str):
    """
    +1 quantity. Supports alias barcodes.
    """
    barcode = (barcode or "").strip()
    if not barcode:
        raise ValueError("Barcode required")

    canonical = resolve_barcode(barcode)
    if canonical:
        barcode = canonical

    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE items
            SET quantity = quantity + 1
            WHERE barcode = ?;
            """,
            (barcode,),
        )

        cur.execute("SELECT id, quantity FROM items WHERE barcode = ?;", (barcode,))
        row = cur.fetchone()
        if not row:
            raise ValueError("Item not found")

        _sync_grocery(cur, row["id"], row["quantity"])
        _log_event(cur, barcode, "add", delta=1, source="ui")

        conn.commit()
    finally:
        conn.close()


def remove_one(barcode: str):
    """
    -1 quantity, floor at 0. Supports alias barcodes.
    """
    barcode = (barcode or "").strip()
    if not barcode:
        raise ValueError("Barcode required")

    canonical = resolve_barcode(barcode)
    if canonical:
        barcode = canonical

    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE items
            SET quantity = CASE WHEN quantity > 0 THEN quantity - 1 ELSE 0 END
            WHERE barcode = ?;
            """,
            (barcode,),
        )

        cur.execute("SELECT id, quantity FROM items WHERE barcode = ?;", (barcode,))
        row = cur.fetchone()
        if not row:
            raise ValueError("Item not found")

        _sync_grocery(cur, row["id"], row["quantity"])
        _log_event(cur, barcode, "remove", delta=-1, source="ui")

        conn.commit()
    finally:
        conn.close()


def delete_item(barcode: str):
    """
    Deletes item entirely (also removes grocery entry + aliases).
    Supports alias barcodes.
    """
    barcode = (barcode or "").strip()
    if not barcode:
        raise ValueError("Barcode required")

    canonical = resolve_barcode(barcode)
    if canonical:
        barcode = canonical

    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM items WHERE barcode = ?;", (barcode,))
        row = cur.fetchone()
        if not row:
            raise ValueError("Item not found")

        item_id = row["id"]

        try:
            cur.execute("DELETE FROM grocery_list WHERE item_id = ?;", (item_id,))
        except Exception:
            pass

        try:
            cur.execute("DELETE FROM barcode_aliases WHERE item_id = ?;", (item_id,))
        except Exception:
            pass

        cur.execute("DELETE FROM items WHERE id = ?;", (item_id,))
        _log_event(cur, barcode, "delete_item", delta=0, source="ui")

        conn.commit()
    finally:
        conn.close()


def delete_grocery_only(barcode: str):
    """
    Removes item from grocery list ONLY (keeps inventory).
    Supports alias barcodes.
    """
    barcode = (barcode or "").strip()
    if not barcode:
        raise ValueError("Barcode required")

    canonical = resolve_barcode(barcode)
    if canonical:
        barcode = canonical

    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM items WHERE barcode = ?;", (barcode,))
        row = cur.fetchone()
        if not row:
            raise ValueError("Item not found")

        cur.execute("DELETE FROM grocery_list WHERE item_id = ?;", (row["id"],))
        _log_event(cur, barcode, "delete_grocery_only", delta=0, source="ui")

        conn.commit()
    finally:
        conn.close()


def move_location(barcode: str, new_location: str):
    """
    Moves an item to a new location string.
    Supports alias barcodes.
    """
    barcode = (barcode or "").strip()
    new_location = (new_location or "").strip()
    if not barcode:
        raise ValueError("Barcode required")
    if not new_location:
        raise ValueError("New location required")

    canonical = resolve_barcode(barcode)
    if canonical:
        barcode = canonical

    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE items
            SET location = ?
            WHERE barcode = ?;
            """,
            (new_location, barcode),
        )
        if cur.rowcount == 0:
            raise ValueError("Item not found")

        _log_event(cur, barcode, "move", delta=0, source="ui")
        conn.commit()
    finally:
        conn.close()


# ============================================================
# SECTION: Name Lookup (placeholder)
# ============================================================

def lookup_name_by_barcode(barcode: str):
    return None


# ============================================================
# SECTION: Low Stock Thresholds
# ============================================================

def set_low_threshold(barcode: str, threshold: int):
    barcode = (barcode or "").strip()
    try:
        threshold = int(threshold)
    except Exception:
        threshold = 0
    if threshold < 0:
        threshold = 0
    if not barcode:
        raise ValueError("Barcode required")

    canonical = resolve_barcode(barcode)
    if canonical:
        barcode = canonical

    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE items
            SET low_threshold = ?
            WHERE barcode = ?;
            """,
            (threshold, barcode),
        )
        if cur.rowcount == 0:
            raise ValueError("Item not found")
        _log_event(cur, barcode, "set_low_threshold", delta=0, source="ui")
        conn.commit()
    finally:
        conn.close()


def get_low_stock():
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT barcode, name, location, quantity, low_threshold
            FROM items
            WHERE low_threshold > 0
              AND quantity > 0
              AND quantity <= low_threshold
            ORDER BY name COLLATE NOCASE;
            """
        )
        rows = cur.fetchall()
        return [(r["barcode"], r["name"], r["location"], r["quantity"], r["low_threshold"]) for r in rows]
    finally:
        conn.close()


# ============================================================
# SECTION: Expiration Dates
# ============================================================

def update_item_expiration(barcode: str, expiration_date: str) -> None:
    """
    Sets (or clears, if expiration_date is blank) the expiration date for an item.
    Expected format: YYYY-MM-DD (from an <input type=date>). Supports alias barcodes.
    """
    barcode = (barcode or "").strip()
    if not barcode:
        raise ValueError("Barcode required")

    canonical = resolve_barcode(barcode)
    if canonical:
        barcode = canonical

    expiration_date = (expiration_date or "").strip() or None

    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE items SET expiration_date = ? WHERE barcode = ?;",
            (expiration_date, barcode),
        )
        if cur.rowcount == 0:
            raise ValueError("Item not found")
        _log_event(cur, barcode, "set_expiration", delta=0, source="ui")
        conn.commit()
    finally:
        conn.close()


def get_expiring_items(days_ahead=3):
    """
    Returns list of tuples (barcode, name, location, quantity, expiration_date)
    for items with an expiration date that has passed, is today, or falls
    within `days_ahead` days from now. Ordered soonest/most-overdue first.
    """
    try:
        days_ahead = int(days_ahead)
    except Exception:
        days_ahead = 3

    cutoff = (datetime.now() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT barcode, name, location, quantity, expiration_date
            FROM items
            WHERE expiration_date IS NOT NULL
              AND expiration_date != ''
              AND expiration_date <= ?
            ORDER BY expiration_date ASC;
            """,
            (cutoff,),
        )
        rows = cur.fetchall()
        return [(r["barcode"], r["name"], r["location"], r["quantity"], r["expiration_date"]) for r in rows]
    finally:
        conn.close()


# ============================================================
# SECTION: Stats + Debug
# ============================================================

def get_event_log(limit=200):
    try:
        limit = int(limit)
    except Exception:
        limit = 200
    if limit < 20:
        limit = 20
    if limit > 5000:
        limit = 5000

    conn = _connect()
    try:
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT created_at, barcode, event_type, delta, source
                FROM event_log
                ORDER BY id DESC
                LIMIT ?;
                """,
                (limit,),
            )
            return cur.fetchall()
        except sqlite3.OperationalError:
            return []
    finally:
        conn.close()


def get_item_stats(barcode: str, days=28):
    barcode = (barcode or "").strip()
    if not barcode:
        return {"found": False}

    canonical = resolve_barcode(barcode)
    if canonical:
        barcode = canonical

    item = get_item_by_barcode(barcode)
    if not item:
        return {"found": False}

    _barcode, name, location, qty, low, _expiration_date = item
    qty = int(qty)
    low = int(low) if low else 0

    try:
        days = int(days)
    except Exception:
        days = 28

    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

    conn = _connect()
    try:
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT event_type, delta
                FROM event_log
                WHERE barcode = ?
                  AND created_at >= ?;
                """,
                (barcode, cutoff),
            )
            rows = cur.fetchall()
        except sqlite3.OperationalError:
            rows = []
    finally:
        conn.close()

    adds = sum(1 for r in rows if int(r["delta"]) > 0)
    removes = sum(1 for r in rows if int(r["delta"]) < 0)
    removes_total = sum(abs(int(r["delta"])) for r in rows if int(r["delta"]) < 0)

    per_week = round((removes_total / max(1, days)) * 7, 2)

    removes_per_day = removes_total / max(1, days)
    est_days_left = None
    if removes_per_day > 0:
        est_days_left = int(round(qty / removes_per_day))

    return {
        "found": True,
        "barcode": barcode,
        "name": name,
        "location": location,
        "quantity": qty,
        "low_threshold": low,
        "adds_28": adds,
        "removes_28": removes,
        "per_week": per_week,
        "est_days_left": est_days_left,
    }


# ============================================================
# SECTION: Locations
# ============================================================

def get_locations():
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT name, has_shelves FROM locations ORDER BY name COLLATE NOCASE;")
        rows = cur.fetchall()
        return [{"name": r["name"], "has_shelves": bool(r["has_shelves"])} for r in rows]
    finally:
        conn.close()


def add_location(name: str, has_shelves: bool):
    name = (name or "").strip()
    if not name:
        raise ValueError("Location name required")

    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO locations (name, has_shelves) VALUES (?, ?);",
            (name, 1 if has_shelves else 0),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raise ValueError("Location already exists")
    finally:
        conn.close()


def delete_location(name: str):
    name = (name or "").strip()
    if not name:
        raise ValueError("Location name required")

    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM locations WHERE name = ?;", (name,))
        if cur.rowcount == 0:
            raise ValueError("Location not found")
        conn.commit()
    finally:
        conn.close()


# ============================================================
# SECTION: Edit Item
# ============================================================

def update_item_name(barcode: str, new_name: str) -> None:
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE items SET name = ? WHERE barcode = ?", (new_name.strip(), barcode))
        if cur.rowcount == 0:
            raise ValueError("Item not found")
        conn.commit()
    finally:
        conn.close()
