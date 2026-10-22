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
import re
import sqlite3
from datetime import datetime, timedelta, timezone

import db
import labels

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
            source TEXT NOT NULL DEFAULT 'ui',
            undone_by INTEGER
        );
        """
    )
    # (Existing installs get undone_by from db._migrate instead — CREATE
    # TABLE IF NOT EXISTS above never alters a table that's already there.)

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


def to_local_display(utc_str: str) -> str:
    """
    Event timestamps are stored in UTC (both event_log's own writes and
    SQLite's CURRENT_TIMESTAMP default are UTC, regardless of the system's
    configured timezone) so day-range comparisons stay unambiguous. This
    converts one back to the system's local timezone for display only —
    it defers to whatever timezone the OS is configured with (TZ /
    /etc/localtime), so setting the Pi's timezone correctly is what
    controls the actual displayed time.
    """
    if not utc_str:
        return utc_str
    try:
        dt = datetime.strptime(utc_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return utc_str


# ============================================================
# SECTION: Barcode Alias Resolution
# ============================================================

def _barcode_variants(barcode: str):
    """
    Same physical product can scan as either a 12-digit UPC-A or the
    equivalent 13-digit EAN-13 (EAN-13 is UPC-A with a leading zero) —
    some packages carry both symbologies, and a scanner can pick up
    whichever one it happens to focus on. Looking up all equivalent forms
    means a product doesn't silently fail to match (and get treated as a
    brand-new/unknown item) just because it scanned as the "other" form
    than it was first added under.
    """
    variants = [barcode]
    if len(barcode) == 13 and barcode.startswith("0"):
        variants.append(barcode[1:])
    elif len(barcode) == 12:
        variants.append("0" + barcode)
    return variants


def resolve_barcode(barcode: str):
    """
    Returns canonical barcode from items table.
    Checks, across all UPC-A/EAN-13 equivalent forms of the scanned code:
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
        variants = _barcode_variants(barcode)

        # Direct hit
        for v in variants:
            cur.execute("SELECT barcode FROM items WHERE barcode = ?;", (v,))
            row = cur.fetchone()
            if row:
                return row["barcode"]

        # Alias hit
        for v in variants:
            cur.execute(
                """
                SELECT i.barcode AS barcode
                FROM barcode_aliases a
                JOIN items i ON i.id = a.item_id
                WHERE a.barcode = ?;
                """,
                (v,),
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


def get_all_aliases_by_item():
    """
    Returns {canonical_barcode: [alias_barcode, ...]} for every item that
    has at least one linked alias, in a single query — used by pages like
    /inventory that list many items at once, so looking up aliases
    doesn't cost one extra query per row.
    """
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT i.barcode AS canonical, a.barcode AS alias
            FROM barcode_aliases a
            JOIN items i ON i.id = a.item_id
            ORDER BY a.created_at DESC;
            """
        )
        rows = cur.fetchall()
        result = {}
        for r in rows:
            result.setdefault(r["canonical"], []).append(r["alias"])
        return result
    finally:
        conn.close()


def remove_barcode_alias(alias_barcode: str) -> None:
    """Un-links an alias barcode. The canonical item and its history are
    untouched; the alias barcode just goes back to being unrecognized."""
    alias_barcode = (alias_barcode or "").strip()
    if not alias_barcode:
        raise ValueError("Barcode required")

    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM barcode_aliases WHERE barcode = ?;", (alias_barcode,))
        if cur.rowcount == 0:
            raise ValueError("Alias not found")
        conn.commit()
    finally:
        conn.close()


# ============================================================
# SECTION: Custom Barcodes (SH-<node code>-<NNNNN>)
# For items with no store barcode. The counter only advances when a
# code is actually used to create an item, so opening the create page
# and backing out doesn't burn a number.
# ============================================================
_NODE_CODE_KEY = "custom_barcode_node_code"
_COUNTER_KEY = "custom_barcode_counter"
_NODE_CODE_RE = re.compile(r"^[A-Z]{1,3}$")


def _default_node_code() -> str:
    words = re.findall(r"[A-Za-z]+", labels.get_node_label())
    return "".join(w[0] for w in words)[:3].upper() or "N"


def get_node_code() -> str:
    """Saved on first use so renaming the node later doesn't change it."""
    code = db.get_setting(_NODE_CODE_KEY)
    if not code:
        code = _default_node_code()
        db.set_setting(_NODE_CODE_KEY, code)
    return code


def set_node_code(code: str) -> None:
    code = (code or "").strip().upper()
    if not _NODE_CODE_RE.match(code):
        raise ValueError("Node code must be 1-3 letters (A-Z)")
    db.set_setting(_NODE_CODE_KEY, code)


def _custom_number(barcode: str):
    m = re.fullmatch(rf"SH-{re.escape(get_node_code())}-(\d{{5}})", (barcode or "").strip())
    return int(m.group(1)) if m else None


def is_custom_barcode(barcode: str) -> bool:
    return _custom_number(barcode) is not None


def _counter() -> int:
    try:
        return max(1, int(db.get_setting(_COUNTER_KEY, "1")))
    except ValueError:
        return 1


def next_custom_barcode() -> str:
    """Next unused code, skipping any already taken as a barcode or alias."""
    code = get_node_code()
    conn = _connect()
    try:
        cur = conn.cursor()
        for n in range(_counter(), 100000):
            candidate = f"SH-{code}-{n:05d}"
            cur.execute(
                "SELECT 1 FROM items WHERE barcode = ? "
                "UNION ALL SELECT 1 FROM barcode_aliases WHERE barcode = ? LIMIT 1;",
                (candidate, candidate),
            )
            if not cur.fetchone():
                return candidate
    finally:
        conn.close()
    raise ValueError("No custom barcodes left for this node code")


def note_custom_barcode_used(barcode: str) -> None:
    n = _custom_number(barcode)
    if n is not None and n + 1 > _counter():
        db.set_setting(_COUNTER_KEY, str(n + 1))


# ============================================================
# SECTION: Internal Helpers
# ============================================================

def _log_event(cur, barcode, event_type, delta=0, source="ui"):
    """
    Writes to event_log table. If table doesn't exist for some reason,
    silently skip (keeps app alive). Returns the new event's id (used by
    Undo), or None if the write was skipped.
    """
    try:
        cur.execute(
            """
            INSERT INTO event_log (created_at, barcode, event_type, delta, source)
            VALUES (?, ?, ?, ?, ?);
            """,
            (_now_utc_iso(), barcode, event_type, int(delta), source),
        )
        return cur.lastrowid
    except Exception:
        return None


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


def get_item_photo(barcode: str):
    """Returns an item's stored photo filename, or None. Kept separate
    from get_item_by_barcode()'s tuple — that tuple is unpacked
    positionally at many call sites in app.py, so adding a field there
    would break all of them."""
    barcode = (barcode or "").strip()
    if not barcode:
        return None

    canonical = resolve_barcode(barcode)
    if canonical:
        barcode = canonical

    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT photo FROM items WHERE barcode = ?;", (barcode,))
        row = cur.fetchone()
        return row["photo"] if row else None
    finally:
        conn.close()


def set_item_photo(barcode: str, filename) -> None:
    """Sets (or clears, if filename is None) the stored photo filename
    for an item. Doesn't touch the files themselves — callers delete the
    old file via photos.delete_photo() after this succeeds."""
    barcode = (barcode or "").strip()
    if not barcode:
        raise ValueError("Barcode required")

    canonical = resolve_barcode(barcode)
    if canonical:
        barcode = canonical

    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE items SET photo = ? WHERE barcode = ?;", (filename, barcode))
        if cur.rowcount == 0:
            raise ValueError("Item not found")
        conn.commit()
    finally:
        conn.close()


def get_photos_map() -> dict:
    """barcode -> photo filename, for every item that has one. One bulk
    query for the Inventory list's thumbnails instead of a lookup per
    row, and instead of adding photo to get_inventory()'s tuple shape."""
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT barcode, photo FROM items WHERE photo IS NOT NULL AND photo != '';")
        return {r["barcode"]: r["photo"] for r in cur.fetchall()}
    finally:
        conn.close()


def get_item_aisle(barcode: str):
    """Returns an item's stored aisle text, or None. Kept separate from
    get_item_by_barcode()'s tuple for the same reason as get_item_photo
    — that tuple is unpacked positionally at many call sites."""
    barcode = (barcode or "").strip()
    if not barcode:
        return None

    canonical = resolve_barcode(barcode)
    if canonical:
        barcode = canonical

    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT aisle FROM items WHERE barcode = ?;", (barcode,))
        row = cur.fetchone()
        return row["aisle"] if row else None
    finally:
        conn.close()


def set_item_aisle(barcode: str, aisle) -> None:
    """Sets (or clears, if aisle is None/blank) the stored aisle text for
    an item."""
    barcode = (barcode or "").strip()
    if not barcode:
        raise ValueError("Barcode required")

    canonical = resolve_barcode(barcode)
    if canonical:
        barcode = canonical

    aisle = (aisle or "").strip() or None

    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE items SET aisle = ? WHERE barcode = ?;", (aisle, barcode))
        if cur.rowcount == 0:
            raise ValueError("Item not found")
        conn.commit()
    finally:
        conn.close()


def get_item_made_date(barcode: str):
    """Returns an item's made date (YYYY-MM-DD, set for leftovers), or None."""
    barcode = (barcode or "").strip()
    if not barcode:
        return None

    canonical = resolve_barcode(barcode)
    if canonical:
        barcode = canonical

    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT made_date FROM items WHERE barcode = ?;", (barcode,))
        row = cur.fetchone()
        return row["made_date"] if row else None
    finally:
        conn.close()


def set_item_made_date(barcode: str, made_date) -> None:
    """Sets (or clears, if blank) an item's made date (YYYY-MM-DD)."""
    barcode = (barcode or "").strip()
    if not barcode:
        raise ValueError("Barcode required")

    canonical = resolve_barcode(barcode)
    if canonical:
        barcode = canonical

    made_date = (made_date or "").strip() or None

    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE items SET made_date = ? WHERE barcode = ?;", (made_date, barcode))
        if cur.rowcount == 0:
            raise ValueError("Item not found")
        conn.commit()
    finally:
        conn.close()


def get_aisle_map() -> dict:
    """barcode -> aisle text, for every item that has one. One bulk
    query for the Grocery List's sort/label instead of a lookup per
    row, and instead of adding aisle to get_grocery_list()'s tuple
    shape."""
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT barcode, aisle FROM items WHERE aisle IS NOT NULL AND aisle != '';")
        return {r["barcode"]: r["aisle"] for r in cur.fetchall()}
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
    Returns the logged event id (for Undo).
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
        event_id = _log_event(cur, barcode, "add", delta=1, source="ui")

        conn.commit()
        return event_id
    finally:
        conn.close()


def remove_one(barcode: str):
    """
    -1 quantity, floor at 0. Supports alias barcodes.
    Returns the logged event id (for Undo), or None when the item was
    already at 0 — nothing changed, so nothing is logged or undoable.
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
        cur.execute("SELECT id, quantity FROM items WHERE barcode = ?;", (barcode,))
        row = cur.fetchone()
        if not row:
            raise ValueError("Item not found")

        event_id = None
        if row["quantity"] > 0:
            cur.execute("UPDATE items SET quantity = quantity - 1 WHERE id = ?;", (row["id"],))
            event_id = _log_event(cur, barcode, "remove", delta=-1, source="ui")

        _sync_grocery(cur, row["id"], max(row["quantity"] - 1, 0))

        conn.commit()
        return event_id
    finally:
        conn.close()


# ============================================================
# SECTION: Undo (scan +1 / -1 tap)
# ============================================================

# The toast only offers Undo for ~10s; the server allows a bit longer
# so a slow tap right at the end (or a slow Pi) still goes through.
UNDO_WINDOW_SECONDS = 60
UNDOABLE_EVENT_TYPES = ("add", "remove", "correct")


def _undoable_event(cur, event_id):
    """Returns (event_row, item_row) if event_id can still be undone,
    else raises ValueError with a user-facing reason."""
    cur.execute(
        "SELECT id, created_at, barcode, event_type, delta, undone_by FROM event_log WHERE id = ?;",
        (event_id,),
    )
    ev = cur.fetchone()
    if not ev or ev["event_type"] not in UNDOABLE_EVENT_TYPES or not ev["delta"]:
        raise ValueError("Nothing to undo")
    if ev["undone_by"] is not None:
        raise ValueError("Already undone")
    try:
        created = datetime.strptime(ev["created_at"], "%Y-%m-%d %H:%M:%S")
    except Exception:
        raise ValueError("Nothing to undo")
    if datetime.utcnow() - created > timedelta(seconds=UNDO_WINDOW_SECONDS):
        raise ValueError("Too late to undo")

    cur.execute("SELECT id, name, quantity FROM items WHERE barcode = ?;", (ev["barcode"],))
    item = cur.fetchone()
    if not item:
        raise ValueError("Item no longer exists")
    return ev, item


def get_undo_offer(event_id):
    """For rendering the Undo toast: {"name", "delta"} if event_id is
    still undoable, else None (so a stale ?undo= link shows nothing)."""
    try:
        event_id = int(event_id)
    except (TypeError, ValueError):
        return None
    conn = _connect()
    try:
        ev, item = _undoable_event(conn.cursor(), event_id)
        return {"name": item["name"], "delta": ev["delta"]}
    except (ValueError, sqlite3.OperationalError):
        return None
    finally:
        conn.close()


def undo_event(event_id):
    """
    Reverses exactly the change event_id made (applies the opposite
    delta, floor 0), rather than restoring an absolute old quantity —
    so a scan that happened in between isn't clobbered. Logs an "undo"
    event and marks the original undone so it can't be undone twice.
    Returns {"name", "delta", "qty"}; raises ValueError if not undoable.
    """
    event_id = int(event_id)
    conn = _connect()
    try:
        cur = conn.cursor()
        # Write lock up front, so two quick taps can't both pass the
        # "not undone yet" check before either marks it.
        cur.execute("BEGIN IMMEDIATE;")
        ev, item = _undoable_event(cur, event_id)

        new_qty = max(item["quantity"] - ev["delta"], 0)
        applied = new_qty - item["quantity"]
        cur.execute("UPDATE items SET quantity = ? WHERE id = ?;", (new_qty, item["id"]))
        _sync_grocery(cur, item["id"], new_qty)
        undo_id = _log_event(cur, ev["barcode"], "undo", delta=applied, source="ui")
        cur.execute("UPDATE event_log SET undone_by = ? WHERE id = ?;", (undo_id or -1, ev["id"]))

        conn.commit()
        return {"name": item["name"], "delta": applied, "qty": new_qty}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def set_quantity(barcode: str, new_qty: int):
    """
    Sets an exact quantity (Edit page, for fixing a miscount). Logged as
    a "correct" event with delta = new - old, which usage stats ignore,
    so a 10 -> 4 fix doesn't read as 6 used. Supports alias barcodes.
    Returns the event id (for Undo), or None if the qty was unchanged —
    nothing is written or logged then.
    """
    barcode = (barcode or "").strip()
    if not barcode:
        raise ValueError("Barcode required")
    new_qty = int(new_qty)
    if new_qty < 0:
        raise ValueError("Quantity can't be negative")

    canonical = resolve_barcode(barcode)
    if canonical:
        barcode = canonical

    conn = _connect()
    try:
        cur = conn.cursor()
        # Lock before reading old qty, so a scan landing mid-save can't
        # make the logged delta disagree with what was actually changed.
        cur.execute("BEGIN IMMEDIATE;")
        cur.execute("SELECT id, quantity FROM items WHERE barcode = ?;", (barcode,))
        row = cur.fetchone()
        if not row:
            raise ValueError("Item not found")

        delta = new_qty - int(row["quantity"])
        if delta == 0:
            conn.rollback()
            return None

        cur.execute("UPDATE items SET quantity = ? WHERE id = ?;", (new_qty, row["id"]))
        _sync_grocery(cur, row["id"], new_qty)
        event_id = _log_event(cur, barcode, "correct", delta=delta, source="ui")

        conn.commit()
        return event_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def log_item_edit(barcode: str) -> None:
    """One "edit" entry for an Edit-page save that changed something other
    than quantity (a qty change logs its own "correct" event), for Recent
    Changes."""
    conn = _connect()
    try:
        _log_event(conn.cursor(), barcode, "edit", delta=0, source="ui")
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
            # A changed expiration date (including clearing it) means any
            # earlier dismissal no longer applies to whatever's there now.
            "UPDATE items SET expiration_date = ?, dismissed_expiration = 0 WHERE barcode = ?;",
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
    within `days_ahead` days from now, excluding ones whose alert has been
    dismissed. Ordered soonest/most-overdue first.
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
              AND dismissed_expiration = 0
            ORDER BY expiration_date ASC;
            """,
            (cutoff,),
        )
        rows = cur.fetchall()
        return [(r["barcode"], r["name"], r["location"], r["quantity"], r["expiration_date"]) for r in rows]
    finally:
        conn.close()


def get_dismissed_expiring_items():
    """
    Items whose expiration alert was dismissed but still have a
    past/near-term expiration date — surfaced so a dismissal can be
    reviewed/undone later instead of disappearing for good.
    """
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT barcode, name, location, quantity, expiration_date
            FROM items
            WHERE expiration_date IS NOT NULL
              AND expiration_date != ''
              AND dismissed_expiration = 1
            ORDER BY expiration_date ASC;
            """
        )
        rows = cur.fetchall()
        return [(r["barcode"], r["name"], r["location"], r["quantity"], r["expiration_date"]) for r in rows]
    finally:
        conn.close()


def set_expiration_dismissed(barcode: str, dismissed: bool) -> None:
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
            "UPDATE items SET dismissed_expiration = ? WHERE barcode = ?;",
            (1 if dismissed else 0, barcode),
        )
        if cur.rowcount == 0:
            raise ValueError("Item not found")
        _log_event(cur, barcode, "dismiss_expiration" if dismissed else "undismiss_expiration", delta=0, source="ui")
        conn.commit()
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


RECENT_CHANGE_TYPES = ("add_new", "add", "remove", "undo", "edit", "correct")


def get_recent_changes(limit=10):
    """Latest inventory changes for the Inventory page: scans (+1, new
    items), -1 taps, undos and edits. Item name comes from items (None
    if the item has since been deleted); created_at is converted to
    local time for display."""
    conn = _connect()
    try:
        cur = conn.cursor()
        placeholders = ",".join("?" for _ in RECENT_CHANGE_TYPES)
        try:
            cur.execute(
                f"""
                SELECT e.id, e.created_at, e.barcode, e.event_type, e.delta, e.undone_by, i.name
                FROM event_log e
                LEFT JOIN items i ON i.barcode = e.barcode
                WHERE e.event_type IN ({placeholders})
                ORDER BY e.id DESC
                LIMIT ?;
                """,
                (*RECENT_CHANGE_TYPES, int(limit)),
            )
            rows = cur.fetchall()
        except sqlite3.OperationalError:
            return []
        return [dict(r, created_at=to_local_display(r["created_at"])) for r in rows]
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
                  AND created_at >= ?
                  -- an undone change and its undo cancel out; counting
                  -- both would show a phantom add *and* remove.
                  -- Corrections (Edit-page qty fixes) aren't usage
                  -- either: a 10 -> 4 fix isn't 6 used.
                  AND event_type NOT IN ('undo', 'correct')
                  AND undone_by IS NULL;
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
