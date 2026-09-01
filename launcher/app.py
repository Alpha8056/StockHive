# ============================================================
# FILE: app.py
# StockHive Launcher — discovers StockPi nodes over mDNS, shows
# them as a tile grid, and owns the weather widget (the only
# feature carried over from the old InfoPanel/homepanel app).
# ============================================================

VERSION = "2.0.0"

import os
import subprocess
import threading
import time
from urllib.parse import quote

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
        "weather_sections": ["current", "hourly", "alerts", "forecast", "radar"],
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


@app.get("/debug/mdns")
def debug_mdns():
    return jsonify(discovery.get_debug_state())


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
    """Restarts the launcher's own systemd service from a background
    thread after a short delay, so the HTTP response announcing the
    restart has time to actually reach the browser first — doing this
    inline in the request thread would have systemctl kill the very
    process trying to run it. Relies on the passwordless sudoers entry
    setup.sh installs for exactly this command."""
    def _run():
        time.sleep(delay)
        subprocess.run(["sudo", "systemctl", "restart", SERVICE_NAME])
    threading.Thread(target=_run, daemon=True).start()


def _delayed_poweroff(delay: float = 1.0) -> None:
    def _run():
        time.sleep(delay)
        subprocess.run(["sudo", "systemctl", "poweroff"])
    threading.Thread(target=_run, daemon=True).start()


@app.post("/system/restart")
def system_restart():
    _delayed_restart()
    return redirect("/settings?msgtype=ok&msg=Restarting%20service%E2%80%A6")


@app.post("/system/update")
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
</style>
</head>
<body>
<div class="wrap">
  <h1>Launcher Settings</h1>

  {% if request.args.get('msg') %}
  <div class="banner {{ request.args.get('msgtype', 'ok') }}">{{ request.args.get('msg') }}</div>
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
    <h2 style="margin-bottom:10px;">Manage Nodes</h2>
    <div class="row" style="border-bottom:none;padding-bottom:0;">
      <span style="color:var(--muted);font-size:13px;">Deleting a node hides its tile permanently, even if it comes back online.</span>
    </div>
    <table>
      <thead><tr><th>Label</th><th>Status</th><th>IP</th><th></th></tr></thead>
      <tbody>
        {% for n in nodes %}
        <tr>
          <td>{{ n.label }}</td>
          <td><span class="pill {{ 'up' if n.is_online else 'down' }}">{{ 'Online' if n.is_online else 'Offline since ' + (n.offline_since or '—') }}</span></td>
          <td>{{ n.ip or '—' }}</td>
          <td>
            <form method="post" action="/settings/nodes/{{ n.id }}/delete" onsubmit="return confirm('Permanently remove this node from the launcher?');">
              <button class="btn btnDanger" type="submit">Delete</button>
            </form>
          </td>
        </tr>
        {% else %}
        <tr><td colspan="4" style="color:var(--muted);">No nodes discovered yet.</td></tr>
        {% endfor %}
      </tbody>
    </table>
  </div>

  <div class="card">
    <h2 style="margin-bottom:10px;">Device</h2>
    <div style="color:var(--muted);">Build: <span class="mono">{{ build }}</span></div>
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
</body>
</html>
"""


@app.get("/settings")
def settings_page():
    return render_template_string(
        SETTINGS_HTML,
        build=_get_git_build(),
        weather_zip=weather_client.get_weather_zip(),
        nodes=[_format_node(r) for r in nodes_db.list_nodes(include_deleted=False)],
    )


@app.post("/settings/weather")
def settings_weather_update():
    weather_client.set_weather_zip(request.form.get("zip", ""))
    return redirect("/settings")


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
