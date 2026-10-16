# ============================================================
# FILE: common_update.py
# Shared git-update + service-restart logic for both the launcher
# and every node's own Settings > Device panel, so "check for
# updates" / "pull + restart" isn't implemented twice. Lives at the
# repo root (not inside launcher/ or node/) because every StockHive
# clone contains both subfolders regardless of which systemd unit(s)
# are actually installed on this machine, so this file is reachable
# at the same relative path from either app.
# ============================================================

import os
import re
import subprocess
import threading
import time

FETCH_TIMEOUT = 10       # seconds — read-only "is there an update" check
PULL_TIMEOUT = 30        # seconds — git pull --ff-only (fetch + fast-forward merge)
PIP_TIMEOUT = 120        # seconds — pip install -r requirements.txt
GIT_CONFIG_TIMEOUT = 5   # seconds — local-only git config write
RESTART_TIMEOUT = 30     # seconds — sudo systemctl restart

# Single sync worker on both apps — a hung network call in here must
# never freeze the whole app, so every subprocess call in this module
# carries an explicit timeout.

_update_cache = {}          # repo_dir -> (checked_at, result dict)
_UPDATE_CACHE_SECONDS = 300

_restart_errors = {}        # service_name -> detail string


def _run(args, cwd=None, timeout=None):
    """subprocess.run wrapper that never raises — every caller here needs
    to turn a failure (including a timeout) into a UI message, not an
    unhandled exception that 500s the request."""
    try:
        result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return result.returncode == 0, (result.stdout or ""), (result.stderr or "")
    except subprocess.TimeoutExpired:
        return False, "", f"Timed out after {timeout}s running: {' '.join(args)}"
    except Exception as e:
        return False, "", str(e)


def get_current_build(repo_dir: str, git_bin: str) -> str:
    ok, out, _ = _run([git_bin, "rev-parse", "--short", "HEAD"], cwd=repo_dir, timeout=5)
    return out.strip() if ok and out.strip() else "unknown"


def _current_branch(repo_dir: str, git_bin: str) -> str:
    ok, out, _ = _run([git_bin, "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_dir, timeout=5)
    branch = out.strip()
    return branch if ok and branch and branch != "HEAD" else "main"


def check_remote_update(repo_dir: str, git_bin: str, force: bool = False) -> dict:
    """
    Checks origin for a newer commit on the current branch via a
    read-only `git fetch` — never touches the working tree. Cached for
    _UPDATE_CACHE_SECONDS so repeated page loads don't each cost a
    network round-trip; pass force=True right after a pull to refresh it
    immediately instead of waiting out the cache.

    Returns {"current", "latest", "update_available", "error"} — "error"
    is set (and update_available is None, not False) whenever the check
    itself couldn't complete, e.g. the device has no internet right now.
    That's a different, non-alarming state from "checked and you're
    current" and the UI should say so rather than silently claiming
    everything's up to date.
    """
    now = time.time()
    cached = _update_cache.get(repo_dir)
    if cached and not force and (now - cached[0]) < _UPDATE_CACHE_SECONDS:
        return cached[1]

    branch = _current_branch(repo_dir, git_bin)
    current = get_current_build(repo_dir, git_bin)

    fetch_ok, _, fetch_err = _run([git_bin, "fetch", "--quiet", "origin", branch], cwd=repo_dir, timeout=FETCH_TIMEOUT)
    if not fetch_ok:
        result = {"current": current, "latest": None, "update_available": None, "error": fetch_err.strip() or "git fetch failed"}
        _update_cache[repo_dir] = (now, result)
        return result

    ok, out, err = _run([git_bin, "rev-parse", "--short", f"origin/{branch}"], cwd=repo_dir, timeout=5)
    if not ok:
        result = {"current": current, "latest": None, "update_available": None, "error": err.strip() or "could not read origin HEAD"}
        _update_cache[repo_dir] = (now, result)
        return result

    latest = out.strip()
    result = {"current": current, "latest": latest, "update_available": latest != current, "error": None}
    _update_cache[repo_dir] = (now, result)
    return result


def pull_latest(repo_dir: str, git_bin: str):
    """
    Fast-forward-only pull, so a diverged/locally-modified tree fails
    loudly — returned to the caller as (False, detail) — instead of
    silently merging or leaving the tree in a weird state.

    core.fileMode is reset to false right before every pull, not just at
    install time, so existing clones get the fix without anyone having to
    re-run setup.sh: without it, a checkout from a different filesystem
    (e.g. this repo's Windows dev clone) can flip the executable bit on
    tracked files, which `git pull --ff-only` then treats as a local
    change blocking the fast-forward.
    """
    _run([git_bin, "config", "core.fileMode", "false"], cwd=repo_dir, timeout=GIT_CONFIG_TIMEOUT)

    ok, out, err = _run([git_bin, "pull", "--ff-only"], cwd=repo_dir, timeout=PULL_TIMEOUT)
    if not ok:
        detail = (err or out or "git pull failed").strip()
        return False, detail
    return True, (out or "").strip()


def pip_install_requirements(pip_bin: str, requirements_path: str):
    ok, out, err = _run([pip_bin, "install", "-r", requirements_path, "--quiet"], timeout=PIP_TIMEOUT)
    if not ok:
        return False, (err or out or "pip install failed").strip()
    return True, ""


def find_sibling_service(repo_dir: str, other_unit_name: str, other_subdir: str):
    """
    Detects the StockHiveTest-style combo case: this machine also runs
    `other_unit_name` (e.g. the launcher, from a node's point of view)
    out of the *same* git clone, so pulling here changes code that
    service is running too. Confirmed by reading the sibling's installed
    unit file rather than just assuming — two independent clones on one
    disk would otherwise be mistaken for a shared one.
    """
    unit_path = f"/etc/systemd/system/{other_unit_name}"
    if not os.path.exists(unit_path):
        return None
    try:
        with open(unit_path) as f:
            content = f.read()
    except Exception:
        return None
    m = re.search(r"^WorkingDirectory=(.+)$", content, re.MULTILINE)
    if not m:
        return None
    working_dir = os.path.normpath(m.group(1).strip())
    expected = os.path.normpath(os.path.join(repo_dir, other_subdir))
    return other_unit_name if working_dir == expected else None


def pop_restart_error(service_name: str):
    """Get-and-clear so a restart failure banner is shown exactly once,
    the next time that service's own Settings page loads."""
    return _restart_errors.pop(service_name, None)


def schedule_restart(service_names, sudo_bin: str, systemctl_bin: str, delay: float = 1.0) -> None:
    """
    Restarts each service in `service_names` from a background thread
    after `delay` seconds, so the HTTP response announcing the restart
    has time to reach the browser first — doing this inline would have
    systemctl kill the very request handling it. Any failure (e.g. a
    stale/missing sudoers rule) is stashed per-service rather than
    raised, since by the time it happens the request is long finished;
    pop_restart_error(service_name) surfaces it later.
    """
    def _run_restart():
        time.sleep(delay)
        for name in service_names:
            ok, out, err = _run([sudo_bin, systemctl_bin, "restart", name], timeout=RESTART_TIMEOUT)
            if not ok:
                _restart_errors[name] = (err or out or "restart failed").strip()
    threading.Thread(target=_run_restart, daemon=True).start()
