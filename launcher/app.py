# ============================================================
# FILE: app.py
# StockHive Launcher — discovers StockPi nodes over mDNS, shows
# them as a tile grid, and owns the weather widget (the only
# feature carried over from the old InfoPanel/homepanel app).
# ============================================================

VERSION = "2.0.0"

import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from urllib.parse import quote

import requests
from flask import Flask, jsonify, redirect, render_template_string, request, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix

import discovery
import nodes_db
import weather_client
import storm_proximity

# The port the launcher is actually reachable on from other machines on
# the LAN (nginx listens here and proxies to gunicorn's internal bind
# port) — this is what gets advertised over mDNS so nodes can find their
# way back. Gunicorn's own bind port is set separately via its -b flag.
PUBLIC_PORT = int(os.environ.get("STOCKPI_PUBLIC_PORT", "80"))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(BASE_DIR)
VENV_PIP = os.path.join(BASE_DIR, "venv", "bin", "pip")
SERVICE_NAME = "stockpi-launcher.service"
NODE_SERVICE_NAME = "stockpi-node.service"

# Absolute paths, not bare command names — the systemd unit sets PATH to
# just this app's venv (so gunicorn resolves the right python/pip), which
# means a plain "git"/"sudo" can't be found via PATH lookup at all.
GIT_BIN = "/usr/bin/git"
SUDO_BIN = "/usr/bin/sudo"
SYSTEMCTL_BIN = "/bin/systemctl"  # must match the path in the sudoers rule exactly

# common_update.py lives at the repo root, a sibling of this launcher/
# folder and of node/ — every StockHive clone has both regardless of
# which systemd unit(s) are installed on this particular machine.
if REPO_DIR not in sys.path:
    sys.path.insert(0, REPO_DIR)
import common_update

nodes_db.init_db()
discovery.start(PUBLIC_PORT)

app = Flask(__name__, static_folder="static", static_url_path="")
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)


# ============================================================
# SECTION: Weather (ported from the old homepanel app)
# ============================================================

def _safe_get_weather_summary():
    try:
        hourly = weather_client.get_forecast_hourly()
        props = hourly.get("properties", {})
        periods = props.get("periods", []) or []
        if not periods:
            raise ValueError("No hourly periods returned")

        now = periods[0]
        temp_f = now.get("temperature")
        temp_u = now.get("temperatureUnit", "F")
        condition = now.get("shortForecast", "Unknown")

        precip = now.get("probabilityOfPrecipitation", {}) or {}
        precip_val = precip.get("value")
        precip_txt = f"{precip_val}%" if precip_val is not None else "—"

        temps = [float(p.get("temperature")) for p in periods[:24] if isinstance(p.get("temperature"), (int, float))]
        hi = f"{int(max(temps))}°{temp_u}" if temps else "—"
        lo = f"{int(min(temps))}°{temp_u}" if temps else "—"

        return {
            "ok": True,
            "location": weather_client.get_weather_zip(),
            "temp": f"{temp_f}°{temp_u}" if temp_f is not None else f"—°{temp_u}",
            "condition": condition,
            "feels": f"{temp_f}°{temp_u}" if temp_f is not None else "—",
            "hi": hi,
            "lo": lo,
            "precip": precip_txt,
            "updated": props.get("updated") or "—",
        }
    except Exception:
        return {
            "ok": False,
            "location": weather_client.get_weather_zip(),
            "temp": "—", "condition": "—", "feels": "—", "hi": "—", "lo": "—",
            "precip": "—", "updated": "—",
        }


def _safe_hourly_rows(limit: int = 12):
    rows = []
    try:
        hourly = weather_client.get_forecast_hourly()
        periods = hourly.get("properties", {}).get("periods", []) or []
        for p in periods[:limit]:
            start = str(p.get("startTime", ""))
            time_short = start[11:16] if len(start) >= 16 else start

            t = p.get("temperature")
            u = p.get("temperatureUnit", "F")
            temp = f"{t}°{u}" if t is not None else "—"

            pop = p.get("probabilityOfPrecipitation", {}) or {}
            popv = pop.get("value")
            precip = f"{popv}%" if popv is not None else "—"

            ws = p.get("windSpeed", "—")
            wd = p.get("windDirection", "")
            wind = f"{ws} {wd}".strip()

            rows.append({
                "time": time_short, "temp": temp, "cond": p.get("shortForecast", "—"),
                "precip": precip, "wind": wind,
            })
    except Exception:
        pass
    return rows


def _safe_tomorrow_periods():
    rows = []
    try:
        forecast = weather_client.get_forecast()
        periods = forecast.get("properties", {}).get("periods", []) or []
        tomorrow_periods = [p for p in periods if p.get("name", "").lower() not in ("today", "tonight")]
        for p in tomorrow_periods[:2]:
            t = p.get("temperature")
            u = p.get("temperatureUnit", "F")
            temp = f"{t}°{u}" if t is not None else "—"
            pop = p.get("probabilityOfPrecipitation", {}) or {}
            popv = pop.get("value")
            precip = f"{popv}%" if popv is not None else "—"
            ws = p.get("windSpeed", "—")
            wd = p.get("windDirection", "")
            wind = f"{ws} {wd}".strip()
            detail = p.get("detailedForecast", "")
            if len(detail) > 200:
                detail = detail[:200].rstrip() + "…"
            rows.append({
                "name": p.get("name", "—"), "temp": temp, "cond": p.get("shortForecast", "—"),
                "precip": precip, "wind": wind, "detail": detail,
            })
    except Exception:
        pass
    return rows


def _safe_alerts(limit: int = 5):
    items = []
    try:
        a = weather_client.get_alerts()
        feats = a.get("features", []) or []
        for f in feats[:limit]:
            prop = f.get("properties", {}) or {}
            headline = prop.get("headline") or prop.get("event") or "Alert"
            onset = prop.get("onset") or ""
            ends = prop.get("ends") or ""
            when = f"{onset} → {ends}".strip(" →")
            desc = prop.get("description") or ""
            if len(desc) > 350:
                desc = desc[:350].rstrip() + "…"
            items.append({"headline": headline, "when": when or "—", "desc": desc})
    except Exception:
        pass
    return items


@app.get("/api/weather")
def api_weather():
    summary = _safe_get_weather_summary()
    try:
        points = weather_client.get_points()
        radar_station = points.get("properties", {}).get("radarStation", "KDDC")
    except Exception:
        radar_station = "KDDC"

    try:
        storm_banner = storm_proximity.get_storm_banner()
    except Exception:
        storm_banner = None

    return jsonify({
        "weather": summary,
        # No "current" card here — carousel page 3 already shows current
        # conditions from the "weather" summary above.
        "weather_sections": ["hourly", "alerts", "forecast", "radar"],
        "hourly": _safe_hourly_rows(12),
        "alerts": _safe_alerts(5),
        "forecast": _safe_tomorrow_periods(),
        "radar_station": radar_station,
        "storm_banner": storm_banner,
    })


# ============================================================
# SECTION: Node grid API
# ============================================================

def _format_node(row: dict) -> dict:
    offline_since = None
    if row.get("offline_since"):
        offline_since = time.strftime("%Y-%m-%d %H:%M", time.localtime(row["offline_since"]))
    return {
        "id": row["id"],
        "label": row["label"],
        "theme": row["theme"],
        "ip": row.get("ip"),
        "port": row.get("port"),
        "is_online": bool(row["is_online"]),
        "offline_since": offline_since,
        "url": f"http://{row['ip']}:{row['port']}" if row.get("ip") and row.get("port") else None,
    }


@app.get("/api/nodes")
def api_nodes():
    rows = nodes_db.list_nodes(include_deleted=False)
    return jsonify([_format_node(r) for r in rows])


@app.delete("/api/nodes/<node_id>")
def api_delete_node(node_id):
    nodes_db.delete_node(node_id)
    return jsonify({"ok": True})


EXPIRING_FETCH_TIMEOUT = 2.0  # seconds, per node


def _fetch_node_expiring(node: dict):
    """Fetches one node's /api/expiring via the exact same url _format_node
    already builds for its tile link (not a raw ip:port guess — the node's
    gunicorn itself only binds 127.0.0.1, so the reachable address is
    whatever nginx/mDNS actually advertised, same as everything else here
    uses). Returns [] on any failure — an unreachable/slow node should
    never break the carousel for everyone else."""
    if not node.get("url"):
        return []
    try:
        r = requests.get(f"{node['url']}/api/expiring", params={"days": 7}, timeout=EXPIRING_FETCH_TIMEOUT)
        r.raise_for_status()
        items = r.json()
    except Exception:
        return []
    for item in items:
        item["node_label"] = node["label"]
    return items


@app.get("/api/expiring")
def api_expiring():
    nodes = [_format_node(r) for r in nodes_db.list_nodes(include_deleted=False) if r["is_online"]]
    if not nodes:
        return jsonify([])

    # Queried in parallel so total wait is ~EXPIRING_FETCH_TIMEOUT
    # regardless of node count, not that timeout multiplied by each node
    # (this runs on gunicorn's single sync worker — a sequential fan-out
    # over several nodes would otherwise stall every other request on the
    # launcher for the whole sum).
    with ThreadPoolExecutor(max_workers=max(1, len(nodes))) as pool:
        results = list(pool.map(_fetch_node_expiring, nodes))

    merged = [item for items in results for item in items]
    merged.sort(key=lambda it: it.get("expiration_date") or "9999-99-99")
    return jsonify(merged)


VERSION_FETCH_TIMEOUT = 2.0  # seconds, per node


def _fetch_node_version(node: dict):
    """Fetches one node's /api/version the same way _fetch_node_expiring
    fetches /api/expiring — via the node's advertised url, swallowing any
    failure so one slow/unreachable node can't hold up the others or the
    page that's waiting on this."""
    if not node.get("url"):
        return node["id"], None
    try:
        r = requests.get(f"{node['url']}/api/version", timeout=VERSION_FETCH_TIMEOUT)
        r.raise_for_status()
        return node["id"], r.json()
    except Exception:
        return node["id"], None


@app.get("/api/node-versions")
def api_node_versions():
    """Per-node build/update status for the Manage Nodes table. Split out
    from /settings itself (which stays local-DB-only and fast) so this
    page always renders immediately — the table's Version column fills
    in afterwards via this endpoint, same as each node's own Settings
    page fills in its "update available" line after load."""
    nodes = [_format_node(r) for r in nodes_db.list_nodes(include_deleted=False) if r["is_online"]]
    if not nodes:
        return jsonify({})

    with ThreadPoolExecutor(max_workers=max(1, len(nodes))) as pool:
        results = dict(pool.map(_fetch_node_version, nodes))
    return jsonify(results)


@app.get("/api/version")
def api_version():
    """Launcher's own current build + update-available status — fetched
    asynchronously by the Settings page's Device card so a slow/offline
    git fetch never delays the page itself."""
    return jsonify(common_update.check_remote_update(REPO_DIR, GIT_BIN))


@app.get("/api/saved-dates")
def api_saved_dates():
    """Raw Settings > Saved Dates entries for carousel page 3. Which ones
    fall in the next 7 days is worked out client-side alongside the
    built-in holidays, so both use the browser's same notion of today."""
    return jsonify(nodes_db.list_saved_dates())


@app.get("/debug/mdns")
def debug_mdns():
    return jsonify(discovery.get_debug_state())


# ============================================================
# SECTION: Build Info + Device Controls (Settings > Device)
# ============================================================

def _get_git_build() -> str:
    return common_update.get_current_build(REPO_DIR, GIT_BIN)


def _sibling_node_service():
    """If a node is also installed on this machine out of this same git
    clone (the StockHiveTest-style combo setup), pulling here changes
    code it's running too."""
    return common_update.find_sibling_service(REPO_DIR, NODE_SERVICE_NAME, "node")


def _delayed_restart(delay: float = 1.0, also_restart=None) -> None:
    """Restarts the launcher's own systemd service (and, when given, any
    sibling service sharing this same repo clone) from a background
    thread after a short delay, so the HTTP response announcing the
    restart has time to actually reach the browser first — doing this
    inline in the request thread would have systemctl kill the very
    process trying to run it. Relies on the passwordless sudoers entry
    setup.sh installs for exactly this command."""
    services = [SERVICE_NAME] + (also_restart or [])
    common_update.schedule_restart(services, SUDO_BIN, SYSTEMCTL_BIN, delay=delay)


def _delayed_poweroff(delay: float = 1.0) -> None:
    def _run():
        time.sleep(delay)
        subprocess.run([SUDO_BIN, SYSTEMCTL_BIN, "poweroff"])
    threading.Thread(target=_run, daemon=True).start()


@app.post("/system/restart")
def system_restart():
    _delayed_restart()
    return redirect("/settings?msgtype=ok&msg=Restarting%20service%E2%80%A6")


@app.post("/system/update")
def system_update():
    ok, detail = common_update.pull_latest(REPO_DIR, GIT_BIN)
    if not ok:
        return redirect(f"/settings?msgtype=danger&msg=Update%20failed%3A%20{quote(detail[:300])}")

    ok, detail = common_update.pip_install_requirements(VENV_PIP, os.path.join(BASE_DIR, "requirements.txt"))
    if not ok:
        return redirect(f"/settings?msgtype=danger&msg=Dependency%20install%20failed%3A%20{quote(detail[:300])}")

    common_update.check_remote_update(REPO_DIR, GIT_BIN, force=True)

    sibling = _sibling_node_service()
    if sibling:
        _delayed_restart(also_restart=[sibling])
        return redirect(
            "/settings?msgtype=ok&msg=Updated%20to%20latest%20build%20%E2%80%94%20this%20machine%20also%20runs%20"
            "a%20node%20from%20the%20same%20code%2C%20restarting%20both%E2%80%A6"
        )

    _delayed_restart()
    return redirect("/settings?msgtype=ok&msg=Updated%20to%20latest%20build%20%E2%80%94%20restarting%E2%80%A6")


@app.post("/system/shutdown")
def system_shutdown():
    _delayed_poweroff()
    return redirect("/settings?msgtype=ok&msg=Shutting%20down%E2%80%A6")


# ============================================================
# SECTION: Pages
# ============================================================

@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


SETTINGS_HTML = """
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Launcher Settings</title>
<style>
  :root{ --bg:#0f1115; --panel:#151922; --panel2:#101521; --text:#e7e9ee; --muted:#a8b0c2; --border:#2a3142; --btn:#1b2231; --btnHover:#232c3f; --shadow:rgba(0,0,0,.35); }
  *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--text);font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;padding:20px;}
  .wrap{max-width:640px;margin:0 auto;}
  h1{margin-bottom:20px;}
  .card{background:linear-gradient(180deg,var(--panel),var(--panel2));border:1px solid var(--border);border-radius:16px;padding:20px;margin:16px 0;box-shadow:0 10px 30px var(--shadow);}
  .row{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:10px 0;border-bottom:1px solid var(--border);}
  .row:last-child{border-bottom:none;}
  input[type=text]{background:#0f1420;border:1px solid var(--border);border-radius:8px;padding:8px 12px;color:var(--text);font-size:15px;}
  .btn{padding:10px 18px;background:var(--btn);color:var(--text);border:1px solid var(--border);border-radius:10px;cursor:pointer;text-decoration:none;display:inline-block;font-weight:700;}
  .btn:hover{background:var(--btnHover);}
  .btnDanger{border-color:rgba(248,113,113,.5);}
  table{width:100%;border-collapse:collapse;}
  th,td{padding:10px;border-bottom:1px solid var(--border);text-align:left;font-size:14px;}
  th{color:var(--muted);}
  .pill{display:inline-block;padding:4px 10px;border-radius:999px;border:1px solid var(--border);font-size:12px;font-weight:700;}
  .up{color:#34d399;} .down{color:#f87171;}
  .btnRow{display:flex;gap:10px;flex-wrap:wrap;}
  .btnWarn{border-color:rgba(247,201,72,.5);}
  .banner{padding:12px 16px;border-radius:10px;border:1px solid var(--border);margin-bottom:16px;font-weight:700;}
  .banner.ok{color:#34d399;border-color:rgba(52,211,153,.4);}
  .banner.danger{color:#f87171;border-color:rgba(248,113,113,.4);}
  .mono{font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,"Liberation Mono",monospace;}
  input[type=date]{background:#0f1420;border:1px solid var(--border);border-radius:8px;padding:7px 10px;color:var(--text);font-size:15px;color-scheme:dark;}
  .dateRow{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:10px 0;border-bottom:1px solid var(--border);}
  .dateRow:last-child{border-bottom:none;}
  .dateRow form{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:0;}
  .dateRow input[type=text]{flex:1;min-width:160px;}
  .dateRow .editForm{flex:1;}
  .check{display:inline-flex;align-items:center;gap:6px;color:var(--muted);font-size:14px;white-space:nowrap;}
  .passed{color:var(--muted);font-size:12px;font-weight:700;}
</style>
</head>
<body>
<div class="wrap">
  <h1>Launcher Settings</h1>

  {% if request.args.get('msg') %}
  <div class="banner {{ request.args.get('msgtype', 'ok') }}">{{ request.args.get('msg') }}</div>
  {% endif %}

  {% if restart_error %}
  <div class="banner danger">Restart/update did not take effect — the service is still running the old build. {{ restart_error }}</div>
  {% endif %}

  <div class="card">
    <h2 style="margin-bottom:10px;">Weather Location</h2>
    <form method="post" action="/settings/weather">
      <div class="row">
        <span>ZIP Code</span>
        <input type="text" name="zip" value="{{ weather_zip }}" maxlength="5" placeholder="67601">
      </div>
      <div style="margin-top:12px;"><button class="btn" type="submit">Save</button></div>
    </form>
  </div>

  <div class="card">
    <h2 style="margin-bottom:10px;">Saved Dates</h2>
    <div style="color:var(--muted);font-size:13px;">Shown on the launcher's "Today at a Glance" page from 7 days before, alongside holidays.</div>
    <div class="dateRow">
      <form method="post" action="/settings/dates" class="editForm">
        <input type="text" name="name" maxlength="80" placeholder="e.g. Mom's Birthday" required>
        <input type="date" name="date" required>
        <label class="check"><input type="checkbox" name="repeats_yearly" value="1" checked> Repeats yearly</label>
        <button class="btn" type="submit">Add</button>
      </form>
    </div>
    {% for d in saved_dates %}
    <div class="dateRow">
      <form method="post" action="/settings/dates/{{ d.id }}" class="editForm">
        <input type="text" name="name" maxlength="80" value="{{ d.name }}" required>
        <input type="date" name="date" value="{{ d.date }}" required>
        <label class="check"><input type="checkbox" name="repeats_yearly" value="1" {{ 'checked' if d.repeats_yearly }}> Repeats yearly</label>
        {% if d.is_past %}<span class="passed">(passed)</span>{% endif %}
        <button class="btn" type="submit">Save</button>
      </form>
      <form method="post" action="/settings/dates/{{ d.id }}/delete" onsubmit="return confirm('Delete this saved date?');">
        <button class="btn btnDanger" type="submit">Delete</button>
      </form>
    </div>
    {% else %}
    <div class="dateRow" style="color:var(--muted);">No saved dates yet.</div>
    {% endfor %}
  </div>

  <div class="card">
    <h2 style="margin-bottom:10px;">Manage Nodes</h2>
    <div class="row" style="border-bottom:none;padding-bottom:0;">
      <span style="color:var(--muted);font-size:13px;">Deleting a node hides its tile permanently, even if it comes back online.</span>
    </div>
    <table>
      <thead><tr><th>Label</th><th>Status</th><th>IP</th><th>Version</th><th></th><th></th></tr></thead>
      <tbody>
        {% for n in nodes %}
        <tr>
          <td>{{ n.label }}</td>
          <td><span class="pill {{ 'up' if n.is_online else 'down' }}">{{ 'Online' if n.is_online else 'Offline since ' + (n.offline_since or '—') }}</span></td>
          <td>{{ n.ip or '—' }}</td>
          <td id="version-{{ n.id }}" style="color:var(--muted);">{{ '—' if n.is_online else 'offline' }}</td>
          <td>{% if n.url %}<a class="btn" href="{{ n.url }}/settings">Settings</a>{% endif %}</td>
          <td>
            <form method="post" action="/settings/nodes/{{ n.id }}/delete" onsubmit="return confirm('Permanently remove this node from the launcher?');">
              <button class="btn btnDanger" type="submit">Delete</button>
            </form>
          </td>
        </tr>
        {% else %}
        <tr><td colspan="6" style="color:var(--muted);">No nodes discovered yet.</td></tr>
        {% endfor %}
      </tbody>
    </table>
  </div>

  <div class="card">
    <h2 style="margin-bottom:10px;">Device</h2>
    <div style="color:var(--muted);">Build: <span class="mono">{{ build }}</span></div>
    <div style="color:var(--muted);margin-top:4px;" id="updateStatus">Checking for updates&hellip;</div>
    <div class="btnRow" style="margin-top:12px;">
      <form method="post" action="/system/restart" onsubmit="return confirm('Restart the StockHive service on this device?');">
        <button class="btn" type="submit">Restart Service</button>
      </form>
      <form method="post" action="/system/update" onsubmit="return confirm('Pull the latest build from GitHub and restart? This can take a minute.');">
        <button class="btn btnWarn" type="submit">Update</button>
      </form>
      <form method="post" action="/system/shutdown" onsubmit="return confirm('Shut down this device? You will need physical or hypervisor access to turn it back on.');">
        <button class="btn btnDanger" type="submit">Shutdown Device</button>
      </form>
    </div>
  </div>

  <a class="btn" href="/">Back to Launcher</a>
</div>
<script>
(function() {
  var el = document.getElementById('updateStatus');
  if (el) {
    fetch('/api/version').then(function(r) { return r.json(); }).then(function(v) {
      if (v.error) {
        el.textContent = 'Unable to check for updates (' + v.error + ')';
      } else if (v.update_available) {
        el.textContent = 'Update available: ' + v.latest + ' (currently on ' + v.current + ')';
        el.style.color = '#f7c948';
        el.style.fontWeight = '700';
      } else {
        el.textContent = 'Up to date.';
      }
    }).catch(function() {
      el.textContent = 'Unable to check for updates.';
    });
  }

  fetch('/api/node-versions').then(function(r) { return r.json(); }).then(function(versions) {
    Object.keys(versions).forEach(function(nodeId) {
      var cell = document.getElementById('version-' + nodeId);
      if (!cell) return;
      var v = versions[nodeId];
      if (!v) {
        cell.textContent = 'unreachable';
        return;
      }
      if (v.update_available) {
        cell.textContent = v.current + ' (update available)';
        cell.style.color = '#f7c948';
      } else {
        cell.textContent = v.current || 'unknown';
      }
    });
  }).catch(function() {});
})();
</script>
</body>
</html>
"""


@app.get("/settings")
def settings_page():
    # Checked for both this service and the sibling node: when a combo
    # update restarts both, it's this same process that actually ran the
    # sibling's restart command and is the only one that would know if
    # that specific call failed — the node's own process never sees it,
    # since each service is a separate OS process with its own in-memory
    # common_update state.
    restart_failures = []
    for svc in (SERVICE_NAME, NODE_SERVICE_NAME):
        err = common_update.pop_restart_error(svc)
        if err:
            restart_failures.append(f"{svc}: {err}")
    restart_error = " | ".join(restart_failures) if restart_failures else None

    # One-time dates that have passed no longer show on the carousel but
    # stay listed here (tagged) so they can still be edited or deleted.
    today_iso = date.today().isoformat()
    saved_dates = [
        dict(d, is_past=(not d["repeats_yearly"] and d["date"] < today_iso))
        for d in nodes_db.list_saved_dates()
    ]
    return render_template_string(
        SETTINGS_HTML,
        build=_get_git_build(),
        weather_zip=weather_client.get_weather_zip(),
        saved_dates=saved_dates,
        nodes=[_format_node(r) for r in nodes_db.list_nodes(include_deleted=False)],
        restart_error=restart_error,
    )


@app.post("/settings/weather")
def settings_weather_update():
    weather_client.set_weather_zip(request.form.get("zip", ""))
    return redirect("/settings")


SAVED_DATE_NAME_MAX = 80


def _parse_saved_date_form():
    """Returns (name, date_iso, repeats_yearly, error). An unchecked
    checkbox isn't submitted at all, so its absence means "one-time"."""
    name = (request.form.get("name") or "").strip()
    raw_date = (request.form.get("date") or "").strip()
    repeats_yearly = request.form.get("repeats_yearly") == "1"
    if not name:
        return None, None, None, "Name is required."
    if len(name) > SAVED_DATE_NAME_MAX:
        return None, None, None, f"Name must be {SAVED_DATE_NAME_MAX} characters or fewer."
    try:
        date_iso = date.fromisoformat(raw_date).isoformat()
    except ValueError:
        return None, None, None, "Please enter a valid date."
    return name, date_iso, repeats_yearly, None


@app.post("/settings/dates")
def settings_add_date():
    name, date_iso, repeats_yearly, error = _parse_saved_date_form()
    if error:
        return redirect(f"/settings?msgtype=danger&msg={quote(error)}")
    nodes_db.add_saved_date(name, date_iso, repeats_yearly)
    return redirect(f"/settings?msgtype=ok&msg={quote('Saved ' + name)}")


@app.post("/settings/dates/<int:date_id>")
def settings_update_date(date_id):
    name, date_iso, repeats_yearly, error = _parse_saved_date_form()
    if error:
        return redirect(f"/settings?msgtype=danger&msg={quote(error)}")
    nodes_db.update_saved_date(date_id, name, date_iso, repeats_yearly)
    return redirect(f"/settings?msgtype=ok&msg={quote('Updated ' + name)}")


@app.post("/settings/dates/<int:date_id>/delete")
def settings_delete_date(date_id):
    nodes_db.delete_saved_date(date_id)
    return redirect("/settings?msgtype=ok&msg=Saved%20date%20deleted")


@app.post("/settings/nodes/<node_id>/delete")
def settings_delete_node(node_id):
    nodes_db.delete_node(node_id)
    return redirect("/settings")


if __name__ == "__main__":
    # Local/dev runner only — binds directly to STOCKPI_PORT (8000 by
    # default) since port 80 typically needs root. Production deploys run
    # under gunicorn behind nginx (see systemd/stockpi-launcher.service).
    dev_port = int(os.environ.get("STOCKPI_PORT", "8000"))
    app.run(host="0.0.0.0", port=dev_port)
