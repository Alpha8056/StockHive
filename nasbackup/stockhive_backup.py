#!/usr/bin/env python3
# ============================================================
# FILE: stockhive_backup.py
# StockHive nightly backups — runs on a NAS or any Linux box.
#
# Asks the launcher for the node list (/api/nodes), downloads each
# online node's /backup zip (inventory.db + photos) into one folder
# per node, and prunes backups older than KEEP_DAYS — never a node's
# newest one. Python 3.8+ standard library only.
#
# Config: KEY=VALUE file (default /etc/stockhive-backup.conf), see
# stockhive-backup.conf.example. Exit code 1 only if a node FAILed;
# offline nodes are logged as SKIP and don't count as failures.
# ============================================================

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import date, datetime, timedelta

DEFAULT_CONFIG = "/etc/stockhive-backup.conf"
DEFAULTS = {"KEEP_DAYS": "14", "LOG_FILE": ""}
REQUIRED = ("LAUNCHER_URL", "BACKUP_DIR")

LAUNCHER_TIMEOUT = 10      # seconds
NODE_TIMEOUT = 60          # per network step (connect, each read) — the node zips before sending
MAX_DOWNLOAD = 500 * 1024 * 1024
RESTORE_WARN_BYTES = 45 * 1024 * 1024   # node nginx allows 50 MB uploads
# Nodes record "last automatic backup" only for requests with this agent.
USER_AGENT = "StockHive-NASBackup/1"
CHUNK = 1024 * 1024

_log_file = None


# ============================================================
# SECTION: Logging / config
# ============================================================

def log(level: str, message: str) -> None:
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {level:<7} {message}"
    print(line, flush=True)
    if _log_file:
        try:
            with open(_log_file, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError as e:
            print(f"(could not write LOG_FILE: {e})", file=sys.stderr)


def load_config(path: str) -> dict:
    cfg = dict(DEFAULTS)
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            cfg[key.strip()] = value.strip().strip('"').strip("'")
    missing = [k for k in REQUIRED if not cfg.get(k)]
    if missing:
        raise ValueError(f"missing {', '.join(missing)} in {path}")
    cfg["LAUNCHER_URL"] = cfg["LAUNCHER_URL"].rstrip("/")
    cfg["KEEP_DAYS"] = max(1, int(cfg["KEEP_DAYS"]))
    return cfg


# ============================================================
# SECTION: Helpers
# ============================================================

def slug(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", text or "").strip("-")
    return s[:40] or "node"


def node_folder(node: dict) -> str:
    """<name>-<first 8 of id>: readable, and unique even if two nodes share a name."""
    return f"{slug(node.get('label'))}-{slug(str(node.get('id', '')))[:8]}"


def fetch_nodes(launcher_url: str) -> list:
    req = urllib.request.Request(f"{launcher_url}/api/nodes", headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=LAUNCHER_TIMEOUT) as resp:
        nodes = json.loads(resp.read().decode("utf-8"))
    if not isinstance(nodes, list):
        raise ValueError("unexpected /api/nodes response")
    return nodes


def download(url: str, dest: str) -> int:
    """Streams url to dest; returns bytes written. Raises on any problem."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=NODE_TIMEOUT) as resp:
        total = 0
        with open(dest, "wb") as out:
            while True:
                chunk = resp.read(CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_DOWNLOAD:
                    raise ValueError(f"backup larger than {MAX_DOWNLOAD // (1024 * 1024)} MB")
                out.write(chunk)
    return total


def check_backup_zip(path: str) -> None:
    """A real node backup is a zip containing inventory.db (a missing DB
    makes /backup redirect to an HTML page, which must not be kept)."""
    if not zipfile.is_zipfile(path):
        raise ValueError("response was not a zip (node may have no database)")
    with zipfile.ZipFile(path) as zf:
        if "inventory.db" not in zf.namelist():
            raise ValueError("zip has no inventory.db")
        bad = zf.testzip()
        if bad:
            raise ValueError(f"corrupt member in zip: {bad}")


_DATED = re.compile(r"_(\d{4}-\d{2}-\d{2})\.zip$")


def prune(folder: str, keep_days: int, dry_run: bool) -> int:
    """Delete this tool's dated zips older than keep_days, never the newest one."""
    dated = []
    for name in os.listdir(folder):
        m = _DATED.search(name)
        if not m:
            continue
        try:
            dated.append((datetime.strptime(m.group(1), "%Y-%m-%d").date(), name))
        except ValueError:
            continue
    if len(dated) <= 1:
        return 0
    dated.sort()
    cutoff = date.today() - timedelta(days=keep_days)
    removed = 0
    for d, name in dated[:-1]:          # the newest is always kept
        if d < cutoff:
            if not dry_run:
                os.remove(os.path.join(folder, name))
            removed += 1
            log("INFO", f"  {'would prune' if dry_run else 'pruned'} {name}")
    return removed


# ============================================================
# SECTION: Main
# ============================================================

def back_up_node(node: dict, cfg: dict, today: str, dry_run: bool) -> str:
    label = node.get("label") or node.get("id") or "?"
    if not node.get("is_online") or not node.get("url"):
        log("SKIP", f"{label}: offline")
        return "skip"

    folder = os.path.join(cfg["BACKUP_DIR"], node_folder(node))
    final = os.path.join(folder, f"{slug(node.get('label'))}_{today}.zip")
    tmp = os.path.join(folder, f".{os.path.basename(final)}.tmp")
    url = f"{node['url'].rstrip('/')}/backup"

    if dry_run:
        log("DRYRUN", f"{label}: would download {url} -> {final}")
        if os.path.isdir(folder):
            prune(folder, cfg["KEEP_DAYS"], dry_run=True)
        return "ok"

    try:
        os.makedirs(folder, exist_ok=True)
        size = download(url, tmp)
        check_backup_zip(tmp)
        os.replace(tmp, final)      # atomic: a failed run never replaces a good file
    except (urllib.error.URLError, OSError, ValueError, zipfile.BadZipFile) as e:
        log("FAIL", f"{label}: {e}")
        try:
            os.remove(tmp)
        except OSError:
            pass
        return "fail"

    log("OK", f"{label}: {final} ({size / (1024 * 1024):.1f} MB)")
    if size > RESTORE_WARN_BYTES:
        log("WARNING", f"{label}: backup is over 45 MB — the node's 50 MB upload limit "
                       "will block restoring it through Tools > Restore (see README)")
    prune(folder, cfg["KEEP_DAYS"], dry_run=False)
    return "ok"


def main() -> int:
    global _log_file
    parser = argparse.ArgumentParser(description="Back up every StockHive node via the launcher")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help=f"config file (default {DEFAULT_CONFIG})")
    parser.add_argument("--dry-run", action="store_true", help="list what would happen, download nothing")
    args = parser.parse_args()

    try:
        cfg = load_config(args.config)
    except (OSError, ValueError) as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 2
    _log_file = cfg.get("LOG_FILE") or None

    started = time.time()
    log("INFO", f"Backup run starting (launcher {cfg['LAUNCHER_URL']}, keep {cfg['KEEP_DAYS']} days)")
    try:
        nodes = fetch_nodes(cfg["LAUNCHER_URL"])
    except (urllib.error.URLError, OSError, ValueError) as e:
        log("FAIL", f"launcher unreachable: {e}")
        return 1

    today = date.today().isoformat()
    counts = {"ok": 0, "skip": 0, "fail": 0}
    for node in nodes:
        try:
            counts[back_up_node(node, cfg, today, args.dry_run)] += 1
        except Exception as e:          # one bad node never stops the rest
            log("FAIL", f"{node.get('label', '?')}: unexpected error: {e!r}")
            counts["fail"] += 1

    log("INFO", f"Done in {time.time() - started:.0f}s: {counts['ok']} ok, "
                f"{counts['skip']} skipped (offline), {counts['fail']} failed")
    return 1 if counts["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
