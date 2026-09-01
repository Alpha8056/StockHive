# ============================================================
# FILE: db.py
# StockHive Node — Database init + safe upgrades (migrations)
# ============================================================

# ============================================================
# SECTION: Imports
# ============================================================
import sqlite3
import uuid

# ============================================================
# SECTION: Constants
# ============================================================
DB_NAME = "inventory.db"

DEFAULT_LOCATIONS = [
    ("Pantry", 1),
    ("Cabinet", 1),
    ("Fridge", 0),
    ("Freezer", 0),
    ("Deep Freeze", 0),
]

# ============================================================
# SECTION: Internal Helpers
# ============================================================

# ------------------------------------------------------------
# SUBSECTION: connect
# ------------------------------------------------------------
def _connect():
    conn = sqlite3.connect(DB_NAME)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

# ------------------------------------------------------------
# SUBSECTION: table_exists
# ------------------------------------------------------------
def _table_exists(conn, table_name: str) -> bool:
    cur = conn.cursor()
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?;",
        (table_name,),
    )
    return cur.fetchone() is not None

# ------------------------------------------------------------
# SUBSECTION: column_exists
# ------------------------------------------------------------
def _column_exists(conn, table: str, column: str) -> bool:
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table});")
    cols = [r[1] for r in cur.fetchall()]
    return column in cols

# ------------------------------------------------------------
# SUBSECTION: ensure_column
# ------------------------------------------------------------
def _ensure_column(conn, table: str, column: str, ddl_fragment: str) -> None:
    """
    Adds a column if missing.
    ddl_fragment example: "low_threshold INTEGER NOT NULL DEFAULT 0"
    """
    if not _column_exists(conn, table, column):
        cur = conn.cursor()
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {ddl_fragment};")
        conn.commit()

# ============================================================
# SECTION: Schema Creation (first install)
# ============================================================

# ------------------------------------------------------------
# SUBSECTION: create_tables
# ------------------------------------------------------------
def _create_tables(conn):
    cur = conn.cursor()

    # Items
    cur.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            barcode TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            location TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 0,
            low_threshold INTEGER NOT NULL DEFAULT 0
        );
    """)

    # Grocery list
    cur.execute("""
        CREATE TABLE IF NOT EXISTS grocery_list (
            item_id INTEGER UNIQUE NOT NULL,
            added_date TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (item_id) REFERENCES items(id)
        );
    """)

    # Locations (zones)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS locations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            has_shelves INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # Barcode cache (offline naming help)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS barcode_cache (
            barcode TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            source TEXT DEFAULT 'unknown',
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # Event log (consumption tracking / debugging)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER,
            barcode TEXT NOT NULL,
            delta INTEGER NOT NULL,              -- +1 add, -1 remove, 0 misc
            event_type TEXT NOT NULL,            -- add/remove/create/move/delete/threshold/grocery_remove
            source TEXT DEFAULT 'ui',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (item_id) REFERENCES items(id)
        );
    """)

    # Node identity / settings (key-value): node_id, label, theme, labels_json, launcher_url
    cur.execute("""
        CREATE TABLE IF NOT EXISTS node_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    """)

    conn.commit()

# ------------------------------------------------------------
# SUBSECTION: create_indexes
# ------------------------------------------------------------
def _create_indexes(conn):
    cur = conn.cursor()
    cur.execute("CREATE INDEX IF NOT EXISTS idx_items_name ON items(name);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_items_location ON items(location);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_events_barcode_time ON events(barcode, created_at);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_events_time ON events(created_at);")
    conn.commit()

# ------------------------------------------------------------
# SUBSECTION: seed_default_locations
# ------------------------------------------------------------
def _seed_default_locations(conn):
    cur = conn.cursor()
    for name, has_shelves in DEFAULT_LOCATIONS:
        cur.execute(
            "INSERT OR IGNORE INTO locations(name, has_shelves) VALUES (?, ?);",
            (name, has_shelves),
        )
    conn.commit()

# ============================================================
# SECTION: Upgrades / Migrations (existing installs)
# ============================================================

# ------------------------------------------------------------
# SUBSECTION: ensure_events_table
# ------------------------------------------------------------
def _ensure_events_table(conn):
    if not _table_exists(conn, "events"):
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER,
                barcode TEXT NOT NULL,
                delta INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                source TEXT DEFAULT 'ui',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (item_id) REFERENCES items(id)
            );
        """)
        conn.commit()

# ------------------------------------------------------------
# SUBSECTION: ensure_node_settings_table
# ------------------------------------------------------------
def _ensure_node_settings_table(conn):
    if not _table_exists(conn, "node_settings"):
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS node_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
        """)
        conn.commit()

# ------------------------------------------------------------
# SUBSECTION: seed_node_id
# ------------------------------------------------------------
def _seed_node_id(conn):
    """Generate a stable node_id once; never overwrite an existing one."""
    cur = conn.cursor()
    cur.execute("SELECT value FROM node_settings WHERE key = 'node_id';")
    if cur.fetchone() is None:
        cur.execute(
            "INSERT INTO node_settings(key, value) VALUES ('node_id', ?);",
            (str(uuid.uuid4()),),
        )
        conn.commit()

# ------------------------------------------------------------
# SUBSECTION: migrate
# ------------------------------------------------------------
def _migrate(conn):
    # Ensure new column exists
    _ensure_column(conn, "items", "low_threshold", "low_threshold INTEGER NOT NULL DEFAULT 0")

    # Ensure expiration date column exists
    _ensure_column(conn, "items", "expiration_date", "expiration_date TEXT")

    # Ensure events table exists
    _ensure_events_table(conn)

    # Ensure node identity/settings table exists, and a node_id is seeded
    _ensure_node_settings_table(conn)
    _seed_node_id(conn)

# ============================================================
# SECTION: Public Entry
# ============================================================

# ------------------------------------------------------------
# SUBSECTION: init_db
# ------------------------------------------------------------
def init_db():
    conn = _connect()
    try:
        _create_tables(conn)
        _create_indexes(conn)
        _seed_default_locations(conn)
        _migrate(conn)
        print("Database initialized / upgraded successfully.")
    finally:
        conn.close()

# ============================================================
# SECTION: Node Settings (public accessors)
# ============================================================

# ------------------------------------------------------------
# SUBSECTION: get_setting / set_setting
# ------------------------------------------------------------
def get_setting(key: str, default=None):
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT value FROM node_settings WHERE key = ?;", (key,))
        row = cur.fetchone()
        return row[0] if row is not None else default
    finally:
        conn.close()


def set_setting(key: str, value: str) -> None:
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO node_settings(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value;",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()


def get_node_id() -> str:
    return get_setting("node_id")


# ============================================================
# SECTION: Main
# ============================================================
if __name__ == "__main__":
    init_db()


