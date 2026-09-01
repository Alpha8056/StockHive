# ============================================================
# SECTION: Imports
# ============================================================
VERSION = "2.0.0"

import os
import subprocess
import threading
import time
import shutil
import io
import socket
from datetime import date, datetime
from urllib.parse import quote

from flask import Flask, request, redirect, send_file, Response, url_for, render_template

from werkzeug.middleware.proxy_fix import ProxyFix

import labels
import mdns_advertise
import identity

class PrefixMiddleware:
    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        prefix = environ.get("HTTP_X_FORWARDED_PREFIX", "")
        if prefix:
            environ["SCRIPT_NAME"] = prefix
        return self.app(environ, start_response)



# Optional dependency (for QR codes)
try:
    import qrcode
except Exception:
    qrcode = None

import db as _db
_db.init_db()

from inventory import (
    # Barcode alias support
    resolve_barcode,
    add_barcode_alias,

    # Inventory / Grocery
    get_item_by_barcode,
    add_item,
    increment_existing,
    remove_one,
    delete_item,
    delete_grocery_only,
    move_location,
    get_inventory,
    get_grocery_list,
    lookup_name_by_barcode,
    update_item_name,
    update_item_expiration,
    get_expiring_items,

    # Smart / Debug
    set_low_threshold,
    get_low_stock,
    get_item_stats,
    get_event_log,
    to_local_display,

    # Locations
    get_locations,
    add_location,
    delete_location,
)

# ============================================================
# SECTION: App Setup
# ============================================================
app = Flask(__name__)

app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.wsgi_app = PrefixMiddleware(app.wsgi_app)

@app.context_processor
def inject_script_root():
    return {"script_root": request.script_root}


# =========================
# GLOBAL "APPS" BUTTON INJECTION (ALL HTML PAGES)
# =========================
from flask import Response
import re

def _apps_button_html() -> str:
    launcher_url = identity.snapshot().get("launcher_url") or "/"
    return (
        f'<a href="{launcher_url}" id="appsBtn" style="'
        'position:fixed;top:12px;left:12px;'
        'padding:10px 14px;'
        'border-radius:12px;'
        'background:rgba(0,0,0,.65);'
        'color:#fff;'
        'text-decoration:none;'
        'font-weight:800;'
        'z-index:2147483647;'
        'border:1px solid rgba(255,255,255,.25);'
        '">Apps</a>'
        '<div style="height:44px"></div>'
    )


@app.after_request
def _inject_apps_button(resp: Response):
    try:
        # Don't touch streamed / passthrough responses
        if getattr(resp, "direct_passthrough", False):
            return resp

        ct = (resp.headers.get("Content-Type") or "").lower()

        # Only touch HTML responses
        if "text/html" not in ct:
            return resp

        # Don't touch redirects or empty bodies
        if resp.status_code in (301, 302, 303, 304) or not resp.get_data():
            return resp

        html = resp.get_data(as_text=True)

        # Avoid double-injecting if already present
        if 'id="appsBtn"' in html:
            return resp

        btn = _apps_button_html()

        # Inject right after <body ...> if possible
        m = re.search(r"<body[^>]*>", html, flags=re.IGNORECASE)
        if m:
            insert_at = m.end()
            html = html[:insert_at] + btn + html[insert_at:]
        else:
            # Fallback: just prepend to the document
            html = btn + html

        resp.set_data(html)

        # Content length may be wrong after modifying the body
        if "Content-Length" in resp.headers:
            del resp.headers["Content-Length"]

        return resp

    except Exception:
        # If anything goes wrong, fail open (serve the page normally)
        return resp



# ============================================================
# SECTION: Constants / Config
# ============================================================

# ------------------------------------------------------------
# SUBSECTION: App Port
# ------------------------------------------------------------
# The port this node is actually reachable on from other machines on the
# LAN (nginx listens here and proxies to gunicorn's internal bind port).
# This is what gets advertised over mDNS and baked into QR/share links —
# gunicorn's own bind port (set via its -b flag in the systemd unit) is
# never reachable directly from outside the Pi.
PUBLIC_PORT = int(os.environ.get("STOCKPI_PUBLIC_PORT", "80"))

# Announce this node on mDNS and start listening for the launcher.
mdns_advertise.start(PUBLIC_PORT)

# ------------------------------------------------------------
# SUBSECTION: Shelves
# ------------------------------------------------------------
SHELVES = [1, 2, 3, 4]
DEFAULT_SHELF = 1

# ------------------------------------------------------------
# SUBSECTION: Expiration Warning Window
# ------------------------------------------------------------
EXPIRATION_WARN_DAYS = int(os.environ.get("STOCKPI_EXPIRATION_WARN_DAYS", "3"))

# ------------------------------------------------------------
# SUBSECTION: Paths
# ------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "inventory.db")
UPLOAD_TMP = os.path.join(BASE_DIR, "inventory.restore.tmp")
REPO_DIR = os.path.dirname(BASE_DIR)
VENV_PIP = os.path.join(BASE_DIR, "venv", "bin", "pip")
SERVICE_NAME = "stockpi-node.service"

# ============================================================
# SECTION: Build Info + Device Controls (Settings > Device)
# ============================================================

def _get_git_build() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_DIR, capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return "unknown"


def _delayed_restart(delay: float = 1.0) -> None:
    """Restarts this node's own systemd service from a background thread
    after a short delay, so the HTTP response announcing the restart has
    time to actually reach the browser first — doing this inline in the
    request thread would have systemctl kill the very process trying to
    run it. Relies on the passwordless sudoers entry setup.sh installs
    for exactly this command."""
    def _run():
        time.sleep(delay)
        subprocess.run(["sudo", "systemctl", "restart", SERVICE_NAME])
    threading.Thread(target=_run, daemon=True).start()


def _delayed_poweroff(delay: float = 1.0) -> None:
    def _run():
        time.sleep(delay)
        subprocess.run(["sudo", "systemctl", "poweroff"])
    threading.Thread(target=_run, daemon=True).start()


# ============================================================
# SECTION: UI Helpers
# ============================================================

# ------------------------------------------------------------
# SUBSECTION: Base URL helpers (LAN-safe QR links)
# ------------------------------------------------------------
def _get_lan_ip_fallback():
    """
    Tries to determine the Pi's LAN IP without requiring internet.
    This is used so QR codes work from the touchscreen even if the
    kiosk browser is on 127.0.0.1.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Doesn't need to be reachable; no packets are actually sent.
        s.connect(("1.1.1.1", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _get_base_url():
    """
    Returns a base URL suitable for sharing (QR/export links).
    Priority:
      1) STOCKPI_BASE_URL env var
      2) LAN IP + PUBLIC_PORT
    """
    env_base = (os.environ.get("STOCKPI_BASE_URL") or "").strip()
    if env_base:
        return env_base.rstrip("/")

    ip = _get_lan_ip_fallback()
    if PUBLIC_PORT == 80:
        return f"http://{ip}"
    return f"http://{ip}:{PUBLIC_PORT}"


# ------------------------------------------------------------
# SUBSECTION: Styles
# ------------------------------------------------------------
_KITCHEN_THEMES = {
    "dark":  {"bg":"#0f1115","panel":"#151922","panel2":"#111520","text":"#e7e9ee","muted":"#a8b0c2","border":"#2a3142","btn":"#1b2231","btnHover":"#232c3f","input":"#0f1420","shadow":"rgba(0,0,0,0.35)"},
    "light": {"bg":"#f0f2f5","panel":"#ffffff","panel2":"#f7f9fc","text":"#111827","muted":"#6b7280","border":"#d1d5db","btn":"#e5e7eb","btnHover":"#d1d5db","input":"#f9fafb","shadow":"rgba(0,0,0,.12)"},
    "dim":   {"bg":"#1e2433","panel":"#252d3d","panel2":"#202840","text":"#cdd5e0","muted":"#7d8ea8","border":"#334155","btn":"#2a3347","btnHover":"#303d54","input":"#181f2d","shadow":"rgba(0,0,0,.35)"},
}

def _get_kitchen_theme():
    return _KITCHEN_THEMES.get(labels.get_theme(), _KITCHEN_THEMES["dark"])

def _styles():
    t = _get_kitchen_theme()
    root_block = (
        f"--bg:{t['bg']};--panel:{t['panel']};--panel2:{t['panel2']};"
        f"--text:{t['text']};--muted:{t['muted']};--border:{t['border']};"
        f"--danger:#ff4d4d;--ok:#39d98a;--warn:#f7c948;"
        f"--btn:{t['btn']};--btnHover:{t['btnHover']};--input:{t['input']};"
        f"--shadow:{t['shadow']};"
    )
    return """
    <style>
      :root{""" + root_block + """}
      * { box-sizing: border-box; }
      body { margin:0; background: var(--bg); color: var(--text);
             font-family: system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif; }
      .wrap{ min-height:100vh; display:flex; justify-content:center; padding:18px; }
      .container{ width:100%; max-width:820px; }
      header{ display:flex; align-items:baseline; justify-content:space-between; gap:12px; margin-bottom:14px; flex-wrap:wrap; }
      h1{ font-size:28px; margin:0; }
      .sub{ color:var(--muted); font-size:14px; }
      .row{ margin:12px 0; }
      .card{ background: linear-gradient(180deg,var(--panel) 0%,var(--panel2) 100%);
             border:1px solid var(--border); border-radius:16px; padding:14px; margin:12px 0; box-shadow:0 10px 30px var(--shadow); }
      .card h2{ margin:0 0 10px 0; font-size:18px; }
      .status{ padding:10px 12px; border-radius:12px; border:1px solid var(--border); background:rgba(255,255,255,0.03);
               margin:10px 0 14px 0; font-weight:800; opacity:1; transition:opacity 220ms ease, transform 220ms ease; }
      .status.ok{ color:var(--ok); } .status.danger{ color:var(--danger); } .status.warn{ color:var(--warn); }
      .status.hide{ opacity:0; transform: translateY(-4px); }
      .chip{ display:inline-block; padding:7px 11px; border:1px solid var(--border); border-radius:999px;
             background:rgba(255,255,255,0.03); margin-left:8px; font-size:14px; }
      .muted{ color:var(--muted); font-size:14px; }
      .btn{ appearance:none; border:1px solid var(--border); background:var(--btn); color:var(--text);
            padding:12px 14px; border-radius:14px; font-size:16px; cursor:pointer; text-decoration:none;
            display:inline-flex; align-items:center; justify-content:center; gap:8px;
            transition:transform .05s ease, background .15s ease, border-color .15s ease; }
      .btn:hover{ background:var(--btnHover); border-color:#39425a; } .btn:active{ transform: translateY(1px); }
      .btn-wide{ width:220px; max-width:100%; } .zone-btn{ min-width:150px; }
      .btn-danger{ border-color: rgba(255,77,77,0.35); background: rgba(255,77,77,0.10); }
      .btn-danger:hover{ background: rgba(255,77,77,0.16); border-color: rgba(255,77,77,0.55); }
      .btn-warn{ border-color: rgba(247,201,72,0.35); background: rgba(247,201,72,0.10); }
      .btn-warn:hover{ background: rgba(247,201,72,0.16); border-color: rgba(247,201,72,0.55); }
      .fieldRow{ display:flex; gap:10px; flex-wrap:wrap; align-items:center; }
      input[type=text]{ width:min(440px,100%); font-size:18px; padding:12px; border-radius:14px; border:1px solid var(--border);
                        background:var(--input); color:var(--text); outline:none; }
      input[type=text]:focus{ border-color: rgba(90,162,255,0.6); box-shadow:0 0 0 3px rgba(90,162,255,0.18); }
      input[type=file]{ color: var(--muted); }
      select, input[type=number]{ font-size:16px; padding:10px 12px; border-radius:12px; border:1px solid var(--border);
              background:var(--input); color:var(--text); outline:none; }
      select:focus, input[type=number]:focus{ border-color: rgba(90,162,255,0.6); box-shadow:0 0 0 3px rgba(90,162,255,0.18); }
      table{ width:100%; border-collapse:collapse; border:1px solid var(--border); background:rgba(255,255,255,0.02);
             border-radius:14px; overflow:hidden; }
      th,td{ padding:10px; border-bottom:1px solid var(--border); }
      th{ text-align:left; color:var(--muted); font-weight:900; background:rgba(255,255,255,0.03); }
      tr:last-child td{ border-bottom:none; }
      .qty-zero{ color:var(--danger); font-weight:900; }
      .qty-low{ color:var(--warn); font-weight:900; }
      .navRow{ display:flex; gap:10px; flex-wrap:wrap; margin-top:10px; }
      .twoCol{ display:grid; grid-template-columns: 1fr; gap:12px; }
      @media (min-width: 860px){ .twoCol{ grid-template-columns: 1fr 1fr; } }
      .exp-badge{ text-decoration:none; }
      form.inline{ display:inline; }
      .mono{ font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace; }
      @media (max-width:520px){ h1{font-size:24px;} .btn-wide{width:100%;} .zone-btn{min-width:46%;} }
    </style>
    """


# ============================================================
# SECTION: Banner timing (env overrides)
# ============================================================
BANNER_MS = int(os.environ.get("STOCKPI_BANNER_MS", "6000"))
BANNER_MS_ERROR = int(os.environ.get("STOCKPI_BANNER_MS_ERROR", "9000"))



# ------------------------------------------------------------
# SUBSECTION: Banner auto-hide
# ------------------------------------------------------------
def _auto_hide_banner_js():
    """
    Auto-hides the top status banner after a delay so kiosk users can read it.
    Uses longer delay for error banners.
    Controlled by env vars:
      - STOCKPI_BANNER_MS (default 10000)
      - STOCKPI_BANNER_MS_ERROR (default 20000)
    """
    return f"""
    <script>
    (function() {{
      function hide(el) {{
        if (!el) return;
        el.style.opacity = "0";
        el.style.transform = "translateY(-6px)";
        setTimeout(function() {{
          if (el && el.parentNode) el.parentNode.removeChild(el);
        }}, 350);
      }}

      window.addEventListener("load", function() {{
        var el = document.querySelector(".status");
        if (!el) return;

        var isError = el.classList.contains("danger") || el.classList.contains("error");
        var delay = isError ? {BANNER_MS_ERROR} : {BANNER_MS};

        el.style.cursor = "pointer";
        el.title = "Tap to dismiss";
        el.addEventListener("click", function() {{ hide(el); }});

        setTimeout(function() {{ hide(el); }}, delay);
      }});
    }})();
    </script>
    """


# ------------------------------------------------------------
# SUBSECTION: Banner HTML (reads msg/msgtype from querystring)
# ------------------------------------------------------------
def _page_status_html():
    msg = request.args.get("msg", "")
    msgtype = request.args.get("msgtype", "ok")
    if not msg:
        return ""
    cls = "ok" if msgtype == "ok" else ("warn" if msgtype == "warn" else "danger")
    return f"<div id='statusBanner' class='status {cls}'>{msg}</div>"


# ============================================================
# SECTION: Expiration Helpers
# ============================================================

def _parse_date(s):
    try:
        return datetime.strptime((s or "").strip(), "%Y-%m-%d").date()
    except Exception:
        return None


def _expiration_status(expiration_date):
    """
    Returns None if there's no date or it's outside the warning window.
    Otherwise a dict: {date, days, level, label}
      level: "expired" | "today" | "soon"
      days: negative if passed, 0 if today, positive if upcoming
    """
    d = _parse_date(expiration_date)
    if not d:
        return None

    delta = (d - date.today()).days
    if delta > EXPIRATION_WARN_DAYS:
        return None

    if delta < 0:
        level = "expired"
        label = f"Expired {abs(delta)}d ago"
    elif delta == 0:
        level = "today"
        label = "Expires today"
    else:
        level = "soon"
        label = f"Expires in {delta}d"

    return {"date": d, "days": delta, "level": level, "label": label}


def _expiration_badge_html(expiration_date):
    status = _expiration_status(expiration_date)
    if not status:
        return ""
    cls = "qty-zero" if status["level"] in ("expired", "today") else "qty-low"
    icon = "⚠️" if status["level"] in ("expired", "today") else "⏰"
    return f"<span class='{cls}' title='{status['label']}'>{icon} {status['label']}</span>"


def _expiring_summary():
    """
    Returns (items, urgent) where items is the list from get_expiring_items()
    and urgent is True if any item is expired or due today.
    """
    items = get_expiring_items(EXPIRATION_WARN_DAYS)
    urgent = any(
        (_expiration_status(it[4]) or {}).get("level") in ("expired", "today")
        for it in items
    )
    return items, urgent


def _expiring_nav_html():
    items, urgent = _expiring_summary()
    if not items:
        return ""
    icon = "⚠️" if urgent else "⏰"
    btn_cls = "btn-danger" if urgent else "btn-warn"
    return f'<div class="row"><a class="btn exp-badge {btn_cls}" href="/expiring">{icon} {len(items)} Expiring</a></div>'


# ============================================================
# SECTION: Location Helpers
# ============================================================

def _locations_map():
    locs = get_locations()
    if not locs:
        locs = [{"name": "Pantry", "has_shelves": True}]
    return {l["name"]: bool(l["has_shelves"]) for l in locs}


def _selected_zone_shelf():
    loc_map = _locations_map()
    zone = request.args.get("zone") or next(iter(loc_map.keys()))
    if zone not in loc_map:
        zone = next(iter(loc_map.keys()))

    shelf = request.args.get("shelf", str(DEFAULT_SHELF))
    try:
        shelf_i = int(shelf)
    except Exception:
        shelf_i = DEFAULT_SHELF
    if shelf_i not in SHELVES:
        shelf_i = DEFAULT_SHELF

    return zone, shelf_i, loc_map


def _build_location(zone, shelf, loc_map):
    if loc_map.get(zone, False):
        return f"{zone} Shelf {shelf}"
    return zone


def _zone_buttons(current_zone, current_shelf, loc_map):
    html = ""
    for name in sorted(loc_map.keys()):
        if loc_map.get(name, False):
            html += f"""<a class="btn zone-btn" href="/?zone={name.replace(' ', '%20')}&shelf={current_shelf}">{name}</a>"""
        else:
            html += f"""<a class="btn zone-btn" href="/?zone={name.replace(' ', '%20')}">{name}</a>"""
    return html


def _shelf_selector(current_zone, current_shelf, loc_map):
    if not loc_map.get(current_zone, False):
        return "<div class='muted'>Shelf: <span class='chip'>N/A</span></div>"

    options = ""
    for s in SHELVES:
        selected = "selected" if s == current_shelf else ""
        options += f"<option value='{s}' {selected}>{s}</option>"

    return f"""
      <div class="row">
        <form method="get" action="/" class="fieldRow">
          <input type="hidden" name="zone" value="{current_zone}">
          <b>Shelf #:</b>
          <select name="shelf" onchange="this.form.submit()">
            {options}
          </select>
          <span class="muted">(this zone uses shelves)</span>
        </form>
      </div>
    """

def _home_url(zone, shelf, focus='scan', msg="", msgtype="ok"):
    url = f"/?zone={zone.replace(' ', '%20')}&shelf={shelf}&focus={focus}&msgtype={msgtype}"
    if msg:
        url += f"&msg={msg.replace(' ', '%20')}"
    return url

def _home_href(zone, shelf, focus='scan', msg="", msgtype="ok"):
    url = f"/?zone={zone.replace(' ', '%20')}&shelf={shelf}&focus={focus}&msgtype={msgtype}"
    if msg:
        url += f"&msg={msg.replace(' ', '%20')}"
    return url


# ============================================================
# SECTION: Routes — Home + Scan
# ============================================================

@app.route("/")
def home():
    zone, shelf, loc_map = _selected_zone_shelf()
    location = _build_location(zone, shelf, loc_map)
    focus = request.args.get("focus", "scan")
    status_html = _page_status_html()

    focus_js = f"""
    <script>
      window.onload = function() {{
        var focusTarget = "{focus}";
        var el = null;
        if (focusTarget === "remove") el = document.getElementById("remove_barcode");
        else el = document.getElementById("scan_barcode");
        if (el) {{ el.focus(); el.select(); }}
      }};
    </script>
    """

    page_loaded_at = time.strftime("%Y-%m-%d %H:%M:%S")
    node_label = labels.get_node_label()
    return f"""
    {_styles()}{_auto_hide_banner_js()}{focus_js}
    <div class="wrap"><div class="container">
      <header>
        <div><h1>{node_label} <span style="font-size:13px;font-weight:400;opacity:0.6">v{VERSION}</span></h1><div class="sub">Fast scan, local-first, touchscreen-friendly</div></div>
        <div class="muted" style="text-align:right">Location <span class="chip">{location}</span><br><span style="font-size:12px">Data as of {page_loaded_at}</span></div>
      </header>

      {status_html}
      {_expiring_nav_html()}

      <div class="card">
        <h2>Location</h2>
        <div class="muted">Pick a zone. Some zones have shelves.</div>
        <div class="row">{_zone_buttons(zone, shelf, loc_map)}</div>
        {_shelf_selector(zone, shelf, loc_map)}
      </div>

      <div class="card">
        <h2>Scan (+1)</h2>
        <form method="post" action="/scan?zone={zone.replace(' ', '%20')}&shelf={shelf}">
          <div class="fieldRow">
            <input id="scan_barcode" type="text" name="barcode" placeholder="Scan barcode">
            <button class="btn btn-wide" type="submit">Scan</button>
          </div>
        </form>
        <div class="muted row">If item exists: auto +1. If new: link to existing OR enter name once.</div>
      </div>

      <div class="card">
        <h2>Remove (-1)</h2>
        <form method="post" action="/remove-one?zone={zone.replace(' ', '%20')}&shelf={shelf}">
          <div class="fieldRow">
            <input id="remove_barcode" type="text" name="barcode" placeholder="Scan to remove">
            <button class="btn btn-wide btn-danger" type="submit">Remove</button>
          </div>
        </form>
      </div>

      <div class="navRow">
        <a class="btn btn-wide" href="/move?zone={zone.replace(' ', '%20')}&shelf={shelf}">Move Location</a>
        <a class="btn btn-wide" href="/inventory">Inventory</a>
        <a class="btn btn-wide" href="/grocery-list">{labels.get_labels()['grocery_low_stock']}</a>
        <a class="btn btn-wide" href="/tools">Tools</a>
      </div>

    </div></div>
    """


@app.route("/scan", methods=["POST"])
def scan():
    zone, shelf, loc_map = _selected_zone_shelf()
    _location = _build_location(zone, shelf, loc_map)

    barcode = (request.form.get("barcode", "") or "").strip()
    if not barcode:
        return redirect(_home_url(zone, shelf, focus='scan', msg="Barcode required", msgtype="danger"))

    item = get_item_by_barcode(barcode)
    if item:
        increment_existing(barcode)
        new_qty = int(item[3]) + 1
        return redirect(_home_url(zone, shelf, focus='scan', msg=f"{item[1]} +1({new_qty})", msgtype="ok"))

    # Unknown barcode → go to resolver UI (alias or new item)
    return redirect(url_for("resolve_barcode_page", barcode=barcode, zone=zone, shelf=shelf))


@app.route("/new-item", methods=["POST"])
def new_item():
    zone, shelf, _loc_map = _selected_zone_shelf()
    barcode = (request.form.get("barcode", "") or "").strip()
    name = (request.form.get("name", "") or "").strip()
    location = (request.form.get("location", "") or "").strip()

    add_item(barcode, name, location)
    return redirect(_home_url(zone, shelf, focus='scan', msg=f"{name} +1(1)", msgtype="ok"))


# ============================================================
# SECTION: Routes — Resolve Barcode (Alias vs New Item)
# ============================================================

@app.route("/resolve_barcode", methods=["GET", "POST"])
def resolve_barcode_page():
    zone, shelf, loc_map = _selected_zone_shelf()
    location = _build_location(zone, shelf, loc_map)

    barcode = (request.args.get("barcode") or request.form.get("barcode") or "").strip()
    if not barcode:
        return redirect(_home_url(zone, shelf, focus='scan'))

    # If it already resolves, treat it as known and just add +1
    canonical = resolve_barcode(barcode)
    if canonical:
        increment_existing(canonical)
        item = get_item_by_barcode(canonical)
        name = item[1] if item else canonical
        new_qty = int(item[3]) if item else "?"
        return redirect(_home_url(zone, shelf, focus='scan', msg=f"{name} +1({new_qty})", msgtype="ok"))

    error = None

    if request.method == "POST":
        action = (request.form.get("action") or "").strip()

        if action == "alias":
            canonical_barcode = (request.form.get("canonical_barcode") or "").strip()
            if not canonical_barcode:
                error = "Pick an existing item to link this barcode to."
            else:
                try:
                    add_barcode_alias(barcode, canonical_barcode)
                    # After linking, add +1 to the canonical item immediately
                    increment_existing(canonical_barcode)
                    item = get_item_by_barcode(canonical_barcode)
                    name = item[1] if item else canonical_barcode
                    new_qty = int(item[3]) if item else "?"
                    return redirect(_home_url(zone, shelf, focus='scan', msg=f"Linked: {name} +1({new_qty})", msgtype="ok"))
                except Exception as e:
                    error = str(e)

        elif action == "new":
            name = (request.form.get("name") or "").strip()
            new_location = (request.form.get("location") or location).strip()
            expiration_date = (request.form.get("expiration_date") or "").strip()

            if not name:
                error = "Name is required."
            elif not new_location:
                error = "Location is required."
            else:
                try:
                    add_item(barcode, name, new_location)
                    if expiration_date:
                        update_item_expiration(barcode, expiration_date)
                    return redirect(_home_url(zone, shelf, focus='scan', msg=f"{name} +1(1)", msgtype="ok"))
                except Exception as e:
                    error = str(e)

    items = get_inventory()
    return render_template(
        "resolve_barcode.html",
        barcode=barcode,
        items=items,
        error=error,
        zone=zone,
        shelf=shelf,
        location=location,
    )


# ============================================================
# SECTION: Routes — Remove + Move
# ============================================================

@app.route("/remove-one", methods=["POST"])
def remove_one_route():
    # Pull zone/shelf from the remove form action querystring so redirects land back
    # on the same kitchen scanning location you were using.
    zone = request.args.get("zone") or _selected_zone_shelf()[0]
    shelf_raw = request.args.get("shelf")
    try:
        shelf = int(shelf_raw) if shelf_raw is not None else _selected_zone_shelf()[1]
    except Exception:
        shelf = _selected_zone_shelf()[1]
    barcode = (request.form.get("barcode", "") or "").strip()
    if not barcode:
        return redirect(_home_url(zone, shelf, focus="remove", msg="Barcode required", msgtype="danger"))

    item = get_item_by_barcode(barcode)
    if not item:
        return redirect(_home_url(zone, shelf, focus="remove", msg="Item not found", msgtype="danger"))

    remove_one(barcode)
    new_qty = max(int(item[3]) - 1, 0)
    return redirect(_home_url(zone, shelf, focus="remove", msg=f"{item[1]} -1({new_qty})", msgtype="danger"))


@app.route("/move")
def move_page():
    zone, shelf, loc_map = _selected_zone_shelf()
    location = _build_location(zone, shelf, loc_map)
    status_html = _page_status_html()

    return f"""
    {_styles()}{_auto_hide_banner_js()}
    <div class="wrap"><div class="container">
      <div class="card">
        <h2>Move Location</h2>
        {status_html}
        <div class="muted">Pick the new location, then scan the item you want to move.</div>
        <div class="row"><b>New target:</b> <span class="chip">{location}</span></div>
        <div class="row">{_zone_buttons(zone, shelf, loc_map)}</div>
        {_shelf_selector(zone, shelf, loc_map)}

        <form method="post" action="/move-scan?zone={zone.replace(' ', '%20')}&shelf={shelf}">
          <div class="fieldRow">
            <input type="text" name="barcode" placeholder="Scan item to move" autofocus>
            <button class="btn btn-wide" type="submit">Scan</button>
          </div>
        </form>

        <div class="row">
          <a class="btn" href="{_home_href(zone, shelf, focus='scan')}">Back</a>
        </div>
      </div>
    </div></div>
    """


@app.route("/move-scan", methods=["POST"])
def move_scan():
    zone, shelf, loc_map = _selected_zone_shelf()
    new_location = _build_location(zone, shelf, loc_map)

    barcode = (request.form.get("barcode", "") or "").strip()
    if not barcode:
        return redirect(f"/move?zone={zone.replace(' ', '%20')}&shelf={shelf}&msgtype=danger&msg=Barcode%20required")

    item = get_item_by_barcode(barcode)
    if not item:
        return redirect(f"/move?zone={zone.replace(' ', '%20')}&shelf={shelf}&msgtype=danger&msg=Item%20not%20found")

    current_location = item[2]
    name = item[1]

    return f"""
    {_styles()}{_auto_hide_banner_js()}
    <div class="wrap"><div class="container">
      <div class="card">
        <h2>Confirm Move</h2>
        <div class="row"><b>{name}</b></div>
        <div class="row muted">From: <span class="chip">{current_location}</span></div>
        <div class="row muted">To: <span class="chip">{new_location}</span></div>

        <form method="post" action="/move-save?zone={zone.replace(' ', '%20')}&shelf={shelf}">
          <input type="hidden" name="barcode" value="{barcode}">
          <input type="hidden" name="new_location" value="{new_location}">
          <div class="fieldRow">
            <button class="btn btn-wide" type="submit">Move</button>
            <a class="btn" href="/move?zone={zone.replace(' ', '%20')}&shelf={shelf}">Cancel</a>
          </div>
        </form>
      </div>
    </div></div>
    """


@app.route("/move-save", methods=["POST"])
def move_save():
    zone, shelf, _loc_map = _selected_zone_shelf()
    barcode = (request.form.get("barcode", "") or "").strip()
    new_location = (request.form.get("new_location", "") or "").strip()

    move_location(barcode, new_location)
    return redirect(
        f"/move?zone={zone.replace(' ', '%20')}&shelf={shelf}&msgtype=ok&msg=Moved%20item%20to%20{new_location.replace(' ', '%20')}"
    )


# ============================================================
# SECTION: Routes — Inventory (search/filter/thresholds/stats)
# ============================================================

@app.route("/inventory")
def inventory_page():
    status_html = _page_status_html()
    loc_map = _locations_map()

    q = (request.args.get("q", "") or "").strip().lower()
    zone_filter = (request.args.get("zone", "All") or "All").strip()

    items = get_inventory()

    def matches(row):
        barcode, name, location, qty, low, expiration_date = row
        if q and (q not in name.lower()) and (q not in barcode.lower()):
            return False
        if zone_filter != "All":
            if not str(location).startswith(zone_filter):
                return False
        return True

    filtered = [r for r in items if matches(r)]

    zone_options = "<option value='All'>All</option>"
    for z in sorted(loc_map.keys()):
        sel = "selected" if z == zone_filter else ""
        zone_options += f"<option value='{z}' {sel}>{z}</option>"

    rows = ""
    for barcode, name, location, qty, low, expiration_date in filtered:
        qty = int(qty)
        low = int(low) if low is not None else 0

        if qty == 0:
            qty_cell = f"<span class='qty-zero'>{qty}</span>"
        elif low > 0 and qty <= low:
            qty_cell = f"<span class='qty-low'>{qty}</span>"
        else:
            qty_cell = str(qty)

        low_cell = str(low) if low else "-"
        exp_cell = _expiration_badge_html(expiration_date) or (expiration_date or "-")

        rows += f"""
        <tr>
          <td>{name}</td>
          <td>{location}</td>
          <td>{qty_cell}</td>
          <td>{low_cell}</td>
          <td>{exp_cell}</td>
          <td>
            <form class="inline" method="post" action="/inventory-remove">
              <input type="hidden" name="barcode" value="{barcode}">
              <button class="btn btn-danger">-1</button>
            </form>

            <form class="inline" method="get" action="/stats">
              <input type="hidden" name="barcode" value="{barcode}">
              <button class="btn btn-warn">Stats</button>
            </form>

            <form class="inline" method="post" action="/threshold-set">
              <input type="hidden" name="barcode" value="{barcode}">
              <input class="mono" style="width:86px;" type="number" min="0" name="threshold" value="{low}" title="Low threshold">
              <button class="btn">Set</button>
            </form>

            <a class="btn" href="/edit?barcode={barcode}">Edit</a>

            <form class="inline" method="post" action="/inventory-delete">
              <input type="hidden" name="barcode" value="{barcode}">
              <button class="btn btn-danger">Delete</button>
            </form>
          </td>
        </tr>
        """

    export_txt = "/export/inventory.txt"
    print_view = "/print/inventory"

    return f"""
    {_styles()}{_auto_hide_banner_js()}
    <div class="wrap"><div class="container">
      <header>
        <div><h1>Inventory</h1><div class="sub">Search + filter by zone • Yellow = low stock • Red = out</div></div>
        <a class="btn" href="/">Home</a>
      </header>

      {status_html}

      <div class="card">
        <div class="fieldRow">
          <a class="btn btn-wide" href="/grocery-list">{labels.get_labels()['grocery_low_stock']}</a>
          <a class="btn btn-wide" href="/expiring">Expiring Items</a>
          <a class="btn btn-wide" href="{export_txt}">Export as Text</a>
          <a class="btn btn-wide" href="{print_view}">Print / Save as PDF</a>
        </div>
      </div>

      <div class="card">
        <h2>Search</h2>
        <form method="get" action="/inventory">
          <div class="fieldRow">
            <input type="text" name="q" placeholder="Search by name or barcode" value="{request.args.get('q','')}">
            <select name="zone">{zone_options}</select>
            <button class="btn btn-wide" type="submit">Apply</button>
            <a class="btn" href="/inventory">Clear</a>
          </div>
        </form>
        <div class="muted row">Showing {len(filtered)} of {len(items)} items</div>
      </div>

      <div class="card">
        <table>
          <tr><th>Item</th><th>Location</th><th>Qty</th><th>Low</th><th>Expires</th><th>Actions</th></tr>
          {rows if rows else "<tr><td colspan='6' class='muted'>No results.</td></tr>"}
        </table>
      </div>

    </div></div>
    """


@app.route("/threshold-set", methods=["POST"])
def threshold_set():
    barcode = (request.form.get("barcode", "") or "").strip()
    thr = (request.form.get("threshold", "0") or "0").strip()
    try:
        thr_i = int(thr)
    except Exception:
        thr_i = 0
    if thr_i < 0:
        thr_i = 0

    if not barcode:
        return redirect("/inventory?msgtype=danger&msg=Barcode%20required")

    set_low_threshold(barcode, thr_i)
    item = get_item_by_barcode(barcode)
    name = item[1] if item else "item"
    return redirect(f"/inventory?msgtype=ok&msg=Set%20low%20threshold%20for%20{name.replace(' ', '%20')}")


@app.route("/inventory-remove", methods=["POST"])
def inventory_remove():
    barcode = (request.form.get("barcode", "") or "").strip()
    if not barcode:
        return redirect("/inventory?msgtype=danger&msg=Barcode%20required")

    item = get_item_by_barcode(barcode)
    if not item:
        return redirect("/inventory?msgtype=danger&msg=Item%20not%20found")

    remove_one(barcode)
    new_qty = max(int(item[3]) - 1, 0)
    return redirect(f"/inventory?msgtype=danger&msg={item[1].replace(' ', '%20')}%20-1({new_qty})")


@app.route("/inventory-delete", methods=["POST"])
def inventory_delete():
    barcode = (request.form.get("barcode", "") or "").strip()
    if not barcode:
        return redirect("/inventory?msgtype=danger&msg=Barcode%20required")

    item = get_item_by_barcode(barcode)
    if not item:
        return redirect("/inventory?msgtype=danger&msg=Item%20not%20found")

    delete_item(barcode)
    return redirect(f"/inventory?msgtype=danger&msg=Deleted%20{item[1].replace(' ', '%20')}")


@app.route("/edit")
def edit_item_page():
    barcode = (request.args.get("barcode", "") or "").strip()
    if not barcode:
        return redirect("/inventory?msgtype=danger&msg=Barcode%20required")
    item = get_item_by_barcode(barcode)
    if not item:
        return redirect("/inventory?msgtype=danger&msg=Item%20not%20found")
    _, name, location, qty, low, expiration_date = item
    status_html = _page_status_html()
    return f"""
    {_styles()}{_auto_hide_banner_js()}
    <div class="wrap"><div class="container">
      <header>
        <div><h1>Edit Item</h1><div class="sub">Update name &amp; expiration date</div></div>
        <a class="btn" href="/inventory">Back</a>
      </header>
      {status_html}
      <div class="card">
        <form method="post" action="/edit-save">
          <input type="hidden" name="barcode" value="{barcode}">
          <div class="fieldRow" style="margin-bottom:14px;">
            <label style="color:var(--muted);font-size:14px;">Item Name</label>
          </div>
          <div class="fieldRow">
            <input type="text" name="name" value="{name}" placeholder="Item name" required>
          </div>
          <div class="fieldRow" style="margin-top:14px;margin-bottom:14px;">
            <label style="color:var(--muted);font-size:14px;">Expiration Date (optional)</label>
          </div>
          <div class="fieldRow">
            <input type="date" name="expiration_date" value="{expiration_date or ''}">
          </div>
          <div class="fieldRow" style="margin-top:16px;">
            <button class="btn btn-wide" type="submit">Save</button>
          </div>
          <div class="muted" style="margin-top:10px;">Barcode: <span class="chip mono">{barcode}</span> &nbsp; Location: <span class="chip">{location}</span></div>
        </form>
      </div>
    </div></div>
    """


@app.route("/edit-save", methods=["POST"])
def edit_item_save():
    barcode = (request.form.get("barcode", "") or "").strip()
    new_name = (request.form.get("name", "") or "").strip()
    expiration_date = (request.form.get("expiration_date", "") or "").strip()
    if not barcode or not new_name:
        return redirect("/inventory?msgtype=danger&msg=Name%20required")
    try:
        update_item_name(barcode, new_name)
        update_item_expiration(barcode, expiration_date)
        return redirect(f"/inventory?msgtype=ok&msg=Saved%20{new_name.replace(' ', '%20')}")
    except Exception as e:
        return redirect(f"/inventory?msgtype=danger&msg=Error%3A%20{str(e).replace(' ', '%20')}")


# ============================================================
# SECTION: Routes — Low Stock + Stats
# ============================================================

@app.route("/low-stock")
def low_stock_page():
    # Low Stock now lives on the combined Grocery & Low Stock page.
    return redirect("/grocery-list")


@app.route("/stats")
def stats_page():
    barcode = (request.args.get("barcode", "") or "").strip()
    if not barcode:
        return redirect("/inventory?msgtype=danger&msg=Barcode%20required")

    stats = get_item_stats(barcode)
    if not stats.get("found"):
        return redirect("/inventory?msgtype=danger&msg=Item%20not%20found")

    name = stats["name"]
    location = stats["location"]
    qty = stats["quantity"]
    low = stats["low_threshold"]
    adds_28 = stats["adds_28"]
    rem_28 = stats["removes_28"]
    per_week = stats["per_week"]
    days_left = stats["est_days_left"]

    days_left_text = f"{days_left} days" if days_left is not None else "Not enough data yet"

    return f"""
    {_styles()}
    <div class="wrap"><div class="container">
      <header>
        <div><h1>Stats</h1><div class="sub">Last 28 days (simple local math)</div></div>
        <div class="fieldRow">
          <a class="btn" href="/inventory">Back</a>
          <a class="btn" href="/">Home</a>
        </div>
      </header>

      <div class="card">
        <h2>{name}</h2>
        <div class="muted">Barcode: <span class="chip mono">{barcode}</span></div>
        <div class="row">
          <div class="muted">Location <span class="chip">{location}</span></div>
          <div class="muted">Qty <span class="chip">{qty}</span></div>
          <div class="muted">Low threshold <span class="chip">{low if low else 0}</span></div>
        </div>
      </div>

      <div class="card">
        <h2>Consumption (last 28 days)</h2>
        <div class="row muted">Adds: <span class="chip">{adds_28}</span> Removes: <span class="chip">{rem_28}</span></div>
        <div class="row muted">Estimated removes per week: <span class="chip">{per_week}</span></div>
        <div class="row muted">Estimated days left (based on removes/day): <span class="chip">{days_left_text}</span></div>
        <div class="muted">Tip: This becomes more accurate after you've used it for a few weeks.</div>
      </div>
    </div></div>
    """


# ============================================================
# SECTION: Routes — Grocery List
# ============================================================

@app.route("/grocery-list")
def grocery_list_page():
    zone, shelf, _loc_map = _selected_zone_shelf()
    status_html = _page_status_html()
    grocery_items = get_grocery_list()
    low_items = get_low_stock()

    export_txt = "/export/grocery.txt"
    print_view = "/print/grocery"
    L = labels.get_labels()

    lis = ""
    for row in grocery_items:
        barcode = row[0]
        name = row[1]
        lis += f"""
        <li style="margin: 10px 0; display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
          <span>{name}</span>
          <form class="inline" method="post" action="/grocery-remove">
            <input type="hidden" name="barcode" value="{barcode}">
            <button class="btn btn-danger">Remove</button>
          </form>
        </li>
        """

    low_rows = ""
    for barcode, name, location, qty, low in low_items:
        low_rows += f"""
        <tr>
          <td>{name}</td>
          <td>{location}</td>
          <td><span class="qty-low">{int(qty)}</span></td>
          <td>{int(low)}</td>
          <td>
            <a class="btn btn-warn" href="/stats?barcode={barcode}">Stats</a>
          </td>
        </tr>
        """

    return f"""
    {_styles()}{_auto_hide_banner_js()}
    <div class="wrap"><div class="container">
      <header>
        <div><h1>{L['grocery_low_stock']}</h1><div class="sub">Everything you need to restock, in one place</div></div>
        <a class="btn" href="{_home_href(zone, shelf, focus='scan')}">Home</a>
      </header>

      {status_html}

      <div class="twoCol">
        <div class="card">
          <h2>{L['grocery_list']}</h2>
          <div class="muted">Items that hit 0 quantity</div>
          <div class="fieldRow" style="margin-top:10px;">
            <a class="btn" href="{export_txt}">Export</a>
            <a class="btn" href="{print_view}">Print / PDF</a>
          </div>
          <ul style="padding-left: 18px; margin: 14px 0 0 0;">
            {lis if lis else "<li class='muted'>Nothing on the grocery list right now.</li>"}
          </ul>
        </div>

        <div class="card">
          <h2>Low Stock</h2>
          <div class="muted">Items where 0 &lt; qty &le; low threshold</div>
          <table style="margin-top:10px;">
            <tr><th>Item</th><th>Location</th><th>Qty</th><th>Low</th><th></th></tr>
            {low_rows if low_rows else "<tr><td colspan='5' class='muted'>Nothing is currently low.</td></tr>"}
          </table>
        </div>
      </div>

    </div></div>
    """


@app.route("/grocery-remove", methods=["POST"])
def grocery_remove():
    barcode = (request.form.get("barcode", "") or "").strip()
    if not barcode:
        return redirect("/grocery-list?msgtype=danger&msg=Barcode%20required")

    item = get_item_by_barcode(barcode)
    if not item:
        return redirect("/grocery-list?msgtype=danger&msg=Item%20not%20found")

    delete_grocery_only(barcode)
    return redirect(
        f"/grocery-list?msgtype=ok&msg=Removed%20{item[1].replace(' ', '%20')}%20from%20grocery%20list"
    )


# ============================================================
# SECTION: Routes — Expiring Items
# ============================================================

@app.route("/expiring")
def expiring_page():
    zone, shelf, _loc_map = _selected_zone_shelf()
    status_html = _page_status_html()
    items = get_expiring_items(EXPIRATION_WARN_DAYS)

    rows = ""
    for barcode, name, location, qty, expiration_date in items:
        status = _expiration_status(expiration_date) or {}
        level = status.get("level", "soon")
        cls = "qty-zero" if level in ("expired", "today") else "qty-low"
        label = status.get("label", "")
        rows += f"""
        <tr>
          <td>{name}</td>
          <td>{location}</td>
          <td>{int(qty)}</td>
          <td>{expiration_date}</td>
          <td><span class="{cls}">{label}</span></td>
          <td><a class="btn" href="/edit?barcode={barcode}">Edit</a></td>
        </tr>
        """

    return f"""
    {_styles()}{_auto_hide_banner_js()}
    <div class="wrap"><div class="container">
      <header>
        <div><h1>Expiring Items</h1><div class="sub">Expired, due today, or within {EXPIRATION_WARN_DAYS} days</div></div>
        <a class="btn" href="{_home_href(zone, shelf, focus='scan')}">Home</a>
      </header>

      {status_html}

      <div class="card">
        <table>
          <tr><th>Item</th><th>Location</th><th>Qty</th><th>Date</th><th>Status</th><th></th></tr>
          {rows if rows else "<tr><td colspan='6' class='muted'>Nothing expiring soon.</td></tr>"}
        </table>
      </div>
    </div></div>
    """


@app.route("/api/expiring-count")
def api_expiring_count():
    items, urgent = _expiring_summary()
    return {"count": len(items), "urgent": urgent}


# ============================================================
# SECTION: Routes — Tools (backup/restore/locations/debug)
# ============================================================

@app.route("/tools")
def tools_page():
    status_html = _page_status_html()
    locs = get_locations()

    rows = ""
    for l in locs:
        name = l["name"]
        has_shelves = "Yes" if bool(l["has_shelves"]) else "No"
        rows += f"""
        <tr>
          <td>{name}</td>
          <td>{has_shelves}</td>
          <td>
            <form class="inline" method="post" action="/locations-delete">
              <input type="hidden" name="name" value="{name}">
              <button class="btn btn-danger">Delete</button>
            </form>
          </td>
        </tr>
        """

    return f"""
    {_styles()}{_auto_hide_banner_js()}
    <div class="wrap"><div class="container">
      <header>
        <div><h1>Tools</h1><div class="sub">Backup / Restore / Locations / Debug</div></div>
        <a class="btn" href="/">Home</a>
      </header>

      {status_html}

      <div class="card">
        <h2>Backup</h2>
        <div class="muted">Downloads your current inventory database.</div>
        <div class="row"><a class="btn btn-wide" href="/backup">Download Backup</a></div>
      </div>

      <div class="card">
        <h2>Restore</h2>
        <div class="muted">Upload an inventory.db backup file. After restore, restart the app/service.</div>
        <form method="post" action="/restore" enctype="multipart/form-data">
          <div class="fieldRow">
            <input type="file" name="dbfile" accept=".db">
            <button class="btn btn-wide btn-danger" type="submit">Restore</button>
          </div>
        </form>
      </div>

      <div class="card">
        <h2>Locations</h2>
        <div class="muted">Add zones here so you never edit code to add a new pantry/cabinet/etc.</div>

        <form method="post" action="/locations-add">
          <div class="fieldRow">
            <input type="text" name="name" placeholder="New location name (ex: Snack Cabinet)" required>
            <select name="has_shelves">
              <option value="1">Has shelves (shows Shelf 1-4)</option>
              <option value="0">No shelves</option>
            </select>
            <button class="btn btn-wide" type="submit">Add</button>
          </div>
        </form>

        <div class="row"></div>

        <table>
          <tr><th>Name</th><th>Has shelves?</th><th>Action</th></tr>
          {rows if rows else "<tr><td colspan='3' class='muted'>No locations found.</td></tr>"}
        </table>

        <div class="muted row">Deleting a location here does NOT delete existing items; it only removes the button/filter option.</div>
      </div>

      <div class="card">
        <h2>Debug</h2>
        <div class="muted">View recent scan events (adds/removes/moves) for troubleshooting.</div>
        <div class="fieldRow">
          <a class="btn btn-wide" href="/debug/events">View Event Log</a>
          <a class="btn btn-wide" href="/export/events.txt">Export Events</a>
        </div>
      </div>

      <div class="card">
        <h2>Settings</h2>
        <div class="muted">Instance name, theme color, launcher link, and page label text.</div>
        <div class="row"><a class="btn btn-wide" href="/settings">Open Settings</a></div>
      </div>

    </div></div>
    """


@app.route("/settings", methods=["GET"])
def settings_page():
    status_html = _page_status_html()
    L = labels.get_labels()
    ident = identity.snapshot()

    theme_options = "".join(
        f'<option value="{key}" {"selected" if key == ident["theme"] else ""}>{key.capitalize()}</option>'
        for key in _KITCHEN_THEMES
    )

    return f"""
    {_styles()}{_auto_hide_banner_js()}
    <div class="wrap"><div class="container">
      <header>
        <div><h1>Settings</h1><div class="sub">Live &mdash; changes apply immediately, no restart</div></div>
        <a class="btn" href="/tools">Back</a>
      </header>

      {status_html}

      <div class="card">
        <h2>Instance</h2>
        <form method="post" action="/settings/update">
          <div class="fieldRow">
            <label class="muted" style="min-width:110px;">Name</label>
            <input type="text" name="node_label" value="{ident['label']}" placeholder="Kitchen Inventory">
          </div>
          <div class="fieldRow" style="margin-top:10px;">
            <label class="muted" style="min-width:110px;">Theme</label>
            <select name="theme">{theme_options}</select>
          </div>
          <div class="row"><button class="btn btn-wide" type="submit">Save</button></div>
        </form>
        <div class="muted" style="margin-top:8px;">Node ID: <span class="mono">{ident['id']}</span></div>
      </div>

      <div class="card">
        <h2>Advertised Address</h2>
        <div class="muted">The IP this node broadcasts to the launcher (used to build its tile link). Auto-detected: <b>{mdns_advertise.detected_lan_ip()}</b> &mdash; usually the LAN, which isn't reachable if you're opening the launcher over a VPN like Tailscale from off that LAN.</div>
        <form method="post" action="/settings/update">
          <div class="fieldRow" style="margin-top:10px;">
            <label class="muted" style="min-width:110px;">Manual override</label>
            <input type="text" name="advertise_ip" value="{ident['advertise_ip_override']}" placeholder="100.x.x.x (Tailscale IP)">
          </div>
          <div class="row"><button class="btn btn-wide" type="submit">Save</button></div>
        </form>
        <div class="muted" style="margin-top:8px;">Currently broadcasting: <b>{ident['advertised_ip']}</b>. Leave blank to auto-detect.</div>
      </div>

      <div class="card">
        <h2>Launcher</h2>
        <div class="muted">Auto-discovered via mDNS: <b>{ident['launcher_url'] or 'not found yet'}</b></div>
        <form method="post" action="/settings/update">
          <div class="fieldRow" style="margin-top:10px;">
            <label class="muted" style="min-width:110px;">Manual override</label>
            <input type="text" name="launcher_url" value="{labels.get_launcher_url()}" placeholder="http://launcher.local">
          </div>
          <div class="row"><button class="btn btn-wide" type="submit">Save</button></div>
        </form>
        <div class="muted" style="margin-top:8px;">Leave blank to use auto-discovery.</div>
      </div>

      <div class="card">
        <h2>Page Labels</h2>
        <div class="muted">Rename this instance's wording, e.g. "Grocery List" &rarr; "Part List" for a tote-storage node.</div>
        <form method="post" action="/settings/labels">
          <div class="fieldRow" style="margin-top:10px;">
            <label class="muted" style="min-width:180px;">Grocery List</label>
            <input type="text" name="grocery_list" value="{L['grocery_list']}">
          </div>
          <div class="fieldRow" style="margin-top:10px;">
            <label class="muted" style="min-width:180px;">Grocery &amp; Low Stock</label>
            <input type="text" name="grocery_low_stock" value="{L['grocery_low_stock']}">
          </div>
          <div class="row"><button class="btn btn-wide" type="submit">Save Labels</button></div>
        </form>
      </div>

      <div class="card">
        <h2>Device</h2>
        <div class="muted">Build: <span class="mono">{_get_git_build()}</span></div>
        <div class="btnRow" style="margin-top:12px;">
          <form class="inline" method="post" action="/system/restart" onsubmit="return confirm('Restart the StockHive service on this device?');">
            <button class="btn btn-wide" type="submit">Restart Service</button>
          </form>
          <form class="inline" method="post" action="/system/update" onsubmit="return confirm('Pull the latest build from GitHub and restart? This can take a minute.');">
            <button class="btn btn-wide btn-warn" type="submit">Update</button>
          </form>
          <form class="inline" method="post" action="/system/shutdown" onsubmit="return confirm('Shut down this device? You will need physical or hypervisor access to turn it back on.');">
            <button class="btn btn-wide btn-danger" type="submit">Shutdown Device</button>
          </form>
        </div>
      </div>

    </div></div>
    """


@app.route("/system/restart", methods=["POST"])
def system_restart():
    _delayed_restart()
    return redirect("/settings?msgtype=ok&msg=Restarting%20service%E2%80%A6")


@app.route("/system/update", methods=["POST"])
def system_update():
    try:
        subprocess.run(
            ["git", "pull"], cwd=REPO_DIR, check=True,
            capture_output=True, text=True, timeout=60,
        )
        subprocess.run(
            [VENV_PIP, "install", "-r", os.path.join(BASE_DIR, "requirements.txt"), "--quiet"],
            check=True, capture_output=True, text=True, timeout=180,
        )
    except Exception as e:
        detail = (getattr(e, "stderr", None) or str(e) or "").replace("\n", " ").strip()
        return redirect(f"/settings?msgtype=danger&msg=Update%20failed%3A%20{quote(detail[:200])}")

    _delayed_restart()
    return redirect("/settings?msgtype=ok&msg=Updated%20to%20latest%20build%20%E2%80%94%20restarting%E2%80%A6")


@app.route("/system/shutdown", methods=["POST"])
def system_shutdown():
    _delayed_poweroff()
    return redirect("/settings?msgtype=ok&msg=Shutting%20down%E2%80%A6")


@app.route("/settings/update", methods=["POST"])
def settings_update():
    node_label = request.form.get("node_label")
    theme = request.form.get("theme")
    launcher_url = request.form.get("launcher_url")
    advertise_ip = request.form.get("advertise_ip")

    if launcher_url is not None:
        labels.set_launcher_url(launcher_url)

    if advertise_ip is not None:
        advertise_ip = advertise_ip.strip()
        if advertise_ip:
            # Validated here rather than at mDNS broadcast time — an
            # invalid value there fails silently deep inside a background
            # thread and looks exactly like the mDNS registration bugs
            # already chased down earlier, instead of telling the user
            # immediately what's wrong.
            try:
                socket.inet_aton(advertise_ip)
            except OSError:
                return redirect(f"/settings?msgtype=danger&msg=%22{advertise_ip}%22%20is%20not%20a%20valid%20IPv4%20address")

    if node_label is not None or theme is not None or advertise_ip is not None:
        identity.update_identity(label=node_label, theme=theme, advertise_ip=advertise_ip)

    return redirect("/settings?msgtype=ok&msg=Settings%20saved")


@app.route("/settings/labels", methods=["POST"])
def settings_labels():
    for key in labels.DEFAULT_LABELS:
        if key in request.form:
            labels.set_label(key, request.form.get(key, ""))
    return redirect("/settings?msgtype=ok&msg=Labels%20saved")


@app.route("/locations-add", methods=["POST"])
def locations_add():
    name = (request.form.get("name", "") or "").strip()
    has_shelves = (request.form.get("has_shelves", "0") or "0").strip()
    hs = True if has_shelves == "1" else False

    add_location(name, hs)
    return redirect(f"/tools?msgtype=ok&msg=Added%20location%20{name.replace(' ', '%20')}")


@app.route("/locations-delete", methods=["POST"])
def locations_delete():
    name = (request.form.get("name", "") or "").strip()
    delete_location(name)
    return redirect(f"/tools?msgtype=danger&msg=Deleted%20location%20{name.replace(' ', '%20')}")


@app.route("/backup")
def backup_db():
    if not os.path.exists(DB_PATH):
        return redirect("/tools?msgtype=danger&msg=Database%20not%20found")
    return send_file(DB_PATH, as_attachment=True, download_name="inventory.db")


@app.route("/restore", methods=["POST"])
def restore_db():
    f = request.files.get("dbfile")
    if not f:
        return redirect("/tools?msgtype=danger&msg=No%20file%20selected")

    try:
        f.save(UPLOAD_TMP)
    except Exception:
        return redirect("/tools?msgtype=danger&msg=Upload%20failed")

    try:
        if os.path.getsize(UPLOAD_TMP) < 1000:
            os.remove(UPLOAD_TMP)
            return redirect("/tools?msgtype=danger&msg=Uploaded%20file%20looks%20invalid")
    except Exception:
        pass

    try:
        shutil.copy2(UPLOAD_TMP, DB_PATH)
        os.remove(UPLOAD_TMP)
        # A backup from an older install can be missing tables/columns
        # this version added (e.g. node_settings) — every page reads from
        # those on every request, so re-running the migration immediately
        # (instead of only at next process start) keeps the app from
        # 500ing on the very next page load after a restore.
        _db.init_db()
    except Exception:
        return redirect("/tools?msgtype=danger&msg=Restore%20failed")

    return redirect("/tools?msgtype=ok&msg=Restore%20complete")


# ============================================================
# SECTION: Routes — Debug Event Log
# ============================================================

@app.route("/debug/events")
def debug_events():
    status_html = _page_status_html()
    limit = (request.args.get("limit", "200") or "200").strip()
    try:
        limit_i = int(limit)
    except Exception:
        limit_i = 200
    if limit_i < 20:
        limit_i = 20
    if limit_i > 2000:
        limit_i = 2000

    rows = ""
    events = get_event_log(limit_i)
    for e in events:
        created_at = to_local_display(e["created_at"])
        barcode = e["barcode"]
        etype = e["event_type"]
        delta = e["delta"]
        source = e["source"]
        dclass = "qty-low" if int(delta) > 0 else ("qty-zero" if int(delta) < 0 else "muted")
        rows += f"""
        <tr>
          <td class="mono">{created_at}</td>
          <td class="mono">{barcode}</td>
          <td>{etype}</td>
          <td><span class="{dclass}">{delta}</span></td>
          <td class="muted">{source}</td>
        </tr>
        """

    return f"""
    {_styles()}{_auto_hide_banner_js()}
    <div class="wrap"><div class="container">
      <header>
        <div><h1>Event Log</h1><div class="sub">Most recent events (debug)</div></div>
        <div class="fieldRow">
          <a class="btn" href="/tools">Back</a>
          <a class="btn" href="/export/events.txt">Export</a>
        </div>
      </header>

      {status_html}

      <div class="card">
        <form method="get" action="/debug/events">
          <div class="fieldRow">
            <span class="muted">Show last</span>
            <input class="mono" style="width:120px;" type="number" name="limit" min="20" max="2000" value="{limit_i}">
            <span class="muted">events</span>
            <button class="btn" type="submit">Apply</button>
          </div>
        </form>
      </div>

      <div class="card">
        <table>
          <tr><th>Time</th><th>Barcode</th><th>Type</th><th>Δ</th><th>Source</th></tr>
          {rows if rows else "<tr><td colspan='5' class='muted'>No events yet.</td></tr>"}
        </table>
      </div>
    </div></div>
    """


# ============================================================
# SECTION: Routes — Export + QR + Print
# ============================================================

@app.route("/share/grocery")
def share_grocery():
    L = labels.get_labels()
    if qrcode is None:
        return f"""
        {_styles()}
        <div class="wrap"><div class="container">
          <header>
            <div><h1>Send {L['grocery_list']} to Phone</h1><div class="sub">QR code</div></div>
            <a class="btn" href="/grocery-list">Back</a>
          </header>

          <div class="card">
            <div class="muted">QR feature is not installed.</div>
            <div class="muted">Fix:</div>
            <pre style="white-space:pre-wrap;">cd ~/node
source venv/bin/activate
pip install qrcode[pil]</pre>
          </div>
        </div></div>
        """

    base_url = _get_base_url()
    share_url = base_url + "/grocery-list"

    return f"""
    {_styles()}
    <div class="wrap"><div class="container">
      <header>
        <div><h1>Send {L['grocery_list']} to Phone</h1><div class="sub">Scan this with your phone camera</div></div>
        <a class="btn" href="/grocery-list">Back</a>
      </header>

      <div class="card" style="text-align:center;">
        <div style="display:inline-block; background:#fff; padding:10px; border-radius:14px;">
          <img alt="QR" src="/qr?path=/grocery-list" style="width:180px; height:180px;">
        </div>

        <div class="row"></div>

        <div class="muted">If QR doesn't work, open this link on your phone:</div>
        <div class="chip" style="user-select:all; display:inline-block; margin-top:10px;">{share_url}</div>
      </div>
    </div></div>
    """


@app.route("/export/grocery.txt")
def export_grocery_txt():
    L = labels.get_labels()
    items = get_grocery_list()
    lines = [L['grocery_list'], "==================", ""]
    if not items:
        lines.append("(Empty)")
    else:
        for row in items:
            lines.append(f"- {row[1]}")
    content = "\n".join(lines) + "\n"

    return f"""
    {_styles()}
    <div class="wrap"><div class="container">
      <header>
        <div><h1>Export: {L['grocery_list']}</h1><div class="sub">Plain text view</div></div>
        <div class="fieldRow">
          <a class="btn" href="/">Home</a>
          <a class="btn" href="/grocery-list">Back</a>
          <a class="btn" href="/export/grocery.raw">Download .txt</a>
        </div>
      </header>

      <div class="card">
        <pre style="white-space:pre-wrap;">{content}</pre>
      </div>
    </div></div>
    """


@app.route("/export/grocery.raw")
def export_grocery_raw():
    items = get_grocery_list()
    lines = [labels.get_labels()['grocery_list'], "==================", ""]
    if not items:
        lines.append("(Empty)")
    else:
        for row in items:
            lines.append(f"- {row[1]}")
    content = "\n".join(lines) + "\n"
    return Response(content, mimetype="text/plain")


# ------------------------------------------------------------
# Inventory export (readable columns)
# ------------------------------------------------------------
@app.route("/export/inventory.txt")
def export_inventory_txt():
    items = get_inventory()

    def cap(s, n):
        s = (s or "").strip()
        return s if len(s) <= n else (s[: n - 1] + "…")

    NAME_W = 30
    LOC_W = 22
    QTY_W = 3

    lines = []
    lines.append("StockPi Inventory (Readable Export)")
    lines.append("=================================")
    lines.append("")
    header = f"{'ITEM':<{NAME_W}}  {'LOCATION':<{LOC_W}}  {'QTY':>{QTY_W}}  {'LOW':>3}  BARCODE"
    lines.append(header)
    lines.append("-" * len(header))

    if not items:
        lines.append("(Empty)")
    else:
        for barcode, name, location, qty, low, expiration_date in items:
            low_i = int(low) if low else 0
            lines.append(
                f"{cap(name, NAME_W):<{NAME_W}}  {cap(location, LOC_W):<{LOC_W}}  {int(qty):>{QTY_W}}  {low_i:>3}  {barcode}"
            )

    content = "\n".join(lines) + "\n"

    return f"""
    {_styles()}
    <div class="wrap"><div class="container">
      <header>
        <div><h1>Export: Inventory</h1><div class="sub">Readable table</div></div>
        <div class="fieldRow">
          <a class="btn" href="/">Home</a>
          <a class="btn" href="/inventory">Back</a>
          <a class="btn" href="/export/inventory.raw">Download .txt</a>
        </div>
      </header>

      <div class="card">
        <pre class="mono" style="white-space:pre; overflow-x:auto;">{content}</pre>
      </div>
    </div></div>
    """


@app.route("/export/inventory.raw")
def export_inventory_raw():
    items = get_inventory()

    def cap(s, n):
        s = (s or "").strip()
        return s if len(s) <= n else (s[: n - 1] + "…")

    NAME_W = 30
    LOC_W = 22
    QTY_W = 3

    lines = []
    lines.append("StockPi Inventory (Readable Export)")
    lines.append("=================================")
    lines.append("")
    header = f"{'ITEM':<{NAME_W}}  {'LOCATION':<{LOC_W}}  {'QTY':>{QTY_W}}  {'LOW':>3}  BARCODE"
    lines.append(header)
    lines.append("-" * len(header))

    if not items:
        lines.append("(Empty)")
    else:
        for barcode, name, location, qty, low, expiration_date in items:
            low_i = int(low) if low else 0
            lines.append(
                f"{cap(name, NAME_W):<{NAME_W}}  {cap(location, LOC_W):<{LOC_W}}  {int(qty):>{QTY_W}}  {low_i:>3}  {barcode}"
            )

    content = "\n".join(lines) + "\n"
    return Response(content, mimetype="text/plain")


# ------------------------------------------------------------
# Export events (debug)
# ------------------------------------------------------------
@app.route("/export/events.txt")
def export_events_txt():
    limit = (request.args.get("limit", "500") or "500").strip()
    try:
        limit_i = int(limit)
    except Exception:
        limit_i = 500
    if limit_i < 50:
        limit_i = 50
    if limit_i > 5000:
        limit_i = 5000

    events = get_event_log(limit_i)

    lines = []
    lines.append("StockPi Event Log (Debug Export)")
    lines.append("================================")
    lines.append("")
    lines.append("time | barcode | type | delta | source")
    lines.append("--------------------------------------")

    for e in events:
        lines.append(f"{to_local_display(e['created_at'])} | {e['barcode']} | {e['event_type']} | {e['delta']} | {e['source']}")

    content = "\n".join(lines) + "\n"

    return f"""
    {_styles()}
    <div class="wrap"><div class="container">
      <header>
        <div><h1>Export: Events</h1><div class="sub">Debug log (most recent first)</div></div>
        <div class="fieldRow">
          <a class="btn" href="/tools">Back</a>
          <a class="btn" href="/export/events.raw?limit={limit_i}">Download .txt</a>
        </div>
      </header>

      <div class="card">
        <pre style="white-space:pre-wrap;" class="mono">{content}</pre>
      </div>
    </div></div>
    """


@app.route("/export/events.raw")
def export_events_raw():
    limit = (request.args.get("limit", "500") or "500").strip()
    try:
        limit_i = int(limit)
    except Exception:
        limit_i = 500
    if limit_i < 50:
        limit_i = 50
    if limit_i > 5000:
        limit_i = 5000

    events = get_event_log(limit_i)

    lines = []
    lines.append("StockPi Event Log (Debug Export)")
    lines.append("================================")
    lines.append("")
    lines.append("time | barcode | type | delta | source")
    lines.append("--------------------------------------")

    for e in events:
        lines.append(f"{to_local_display(e['created_at'])} | {e['barcode']} | {e['event_type']} | {e['delta']} | {e['source']}")

    content = "\n".join(lines) + "\n"
    return Response(content, mimetype="text/plain")


@app.route("/print/grocery")
def print_grocery():
    items = get_grocery_list()
    rows = ""
    for row in items:
        rows += f"<li style='margin:10px 0;font-size:20px;'>{row[1]}</li>"
    if not rows:
        rows = "<li style='margin:10px 0;font-size:20px;'>(Empty)</li>"


    return f"""
    {_styles()}
    <div class="wrap"><div class="container">
      <h1 style="margin-bottom:8px;">{labels.get_labels()['grocery_list']}</h1>
      <div class="sub" style="margin-bottom:20px;">Print this page or save as PDF.</div>
      <div class="card">
        <ul style="padding-left:22px;">
          {rows}
        </ul>
      </div>
    </div></div>"""



@app.route("/print/inventory")
def print_inventory():
    items = get_inventory()
    rows = ""
    for barcode, name, location, qty, low, expiration_date in items:
        rows += f"""
          <tr>
            <td>{name}</td>
            <td>{location}</td>
            <td><b>{qty}</b></td>
            <td class="muted">{low if low else 0}</td>
            <td class="muted">{barcode}</td>
          </tr>
        """
    if not rows:
        rows = "<tr><td colspan='5'>(Empty)</td></tr>"

    return f"""
    {_styles()}
    <div class="wrap"><div class="container">
      <h1 style="margin-bottom:8px;">StockPi Inventory</h1>
      <div class="sub" style="margin-bottom:14px;">Print this page or Save as PDF on your phone.</div>
      <div class="card">
        <table>
          <tr>
            <th>Item</th>
            <th>Location</th>
            <th>Qty</th>
            <th>Low</th>
            <th>Barcode</th>
          </tr>
          {rows}
        </table>
      </div>
    </div></div>"""


@app.route("/qr")
def qr_png():
    if qrcode is None:
        return Response("QR feature requires: pip install qrcode[pil]", mimetype="text/plain", status=500)

    path = (request.args.get("path") or "/").strip()
    if not path.startswith("/"):
        path = "/" + path

    base = _get_base_url()
    full_url = base + path

    img = qrcode.make(full_url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return Response(buf.getvalue(), mimetype="image/png")


# ============================================================
# SECTION: Main Runner
# ============================================================
if __name__ == "__main__":
    # Local/dev runner only — binds directly to STOCKPI_PORT (5000 by
    # default) since port 80 typically needs root. Production deploys run
    # under gunicorn behind nginx (see systemd/stockpi-node.service) and
    # never hit this branch.
    dev_port = int(os.environ.get("STOCKPI_PORT", "5000"))
    app.run(host="0.0.0.0", port=dev_port, debug=False)
