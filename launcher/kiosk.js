// ============================================================
// FILE: kiosk.js (served by the launcher at /kiosk.js)
// Kiosk-only display behavior: the night-mode dimming overlay and
// "return to launcher when idle". Loaded by the launcher's own pages
// and injected into every node page (next to the "Apps" button).
//
// Does nothing unless this browser has been marked as the kiosk
// (Launcher Settings > Display). That flag lives in localStorage,
// which is per-origin — the launcher and every node are different
// origins — so launcher tile links carry it across as #kiosk=1 /
// #kiosk=0, and this script saves it on the node's origin and strips
// the hash.
//
// Settings are baked in per request (the placeholder below) and the
// file is served no-cache, so a change reaches each page on its next
// load with no cross-origin fetch needed from node pages.
// ============================================================
(function () {
  "use strict";
  if (window.StockHiveKiosk) return; // already loaded on this page

  var settings = __DISPLAY_SETTINGS__;

  var FLAG_KEY = "stockhive.kiosk";
  var WAKE_MS = 60 * 1000;              // a tap keeps the screen bright this long
  var TICK_MS = 15 * 1000;              // schedule / idle check interval
  var SETTINGS_REFRESH_MS = 5 * 60 * 1000;

  var script = document.currentScript;
  var src = (script && script.src) || (location.origin + "/kiosk.js");
  var launcherHome = src.replace(/\/kiosk\.js(?:[?#].*)?$/, "/");
  var onLauncher = new URL(launcherHome, location.href).origin === location.origin;

  function readFlag() {
    try { return window.localStorage.getItem(FLAG_KEY) === "1"; } catch (e) { return false; }
  }
  function writeFlag(on) {
    try { window.localStorage.setItem(FLAG_KEY, on ? "1" : "0"); } catch (e) {}
  }

  var handoff = /^#kiosk=([01])$/.exec(location.hash);
  if (handoff) {
    writeFlag(handoff[1] === "1");
    try { history.replaceState(history.state, "", location.pathname + location.search); } catch (e) {}
  }

  window.StockHiveKiosk = {
    isKiosk: readFlag,
    setKiosk: writeFlag,
    tileHash: function () { return readFlag() ? "#kiosk=1" : "#kiosk=0"; }
  };

  if (!readFlag()) return;

  // ===== Activity tracking =====
  var lastActivity = Date.now();
  var typedIn = typeof WeakSet === "function" ? new WeakSet() : null;

  function onActivity(e) {
    lastActivity = Date.now();
    if (e.type === "input" && typedIn && e.target) typedIn.add(e.target);
    // A barcode scan / keypress wakes the screen immediately. Taps on the
    // overlay are handled by its own click handler instead, so the tap
    // that wakes the screen never also lands on whatever is underneath.
    if (e.type === "keydown") render();
  }
  ["pointerdown", "touchstart", "keydown", "input", "change", "wheel"].forEach(function (type) {
    document.addEventListener(type, onActivity, { capture: true, passive: true });
  });

  // ===== Night mode overlay =====
  var overlay = null;

  function ensureOverlay() {
    if (overlay || !document.body) return overlay;
    overlay = document.createElement("div");
    overlay.id = "stockhiveNightOverlay";
    overlay.setAttribute("aria-hidden", "true");
    // Same max z-index as the node's Apps button, but appended later, so
    // it stacks above it.
    overlay.style.cssText =
      "position:fixed;top:0;left:0;right:0;bottom:0;background:#000;" +
      "z-index:2147483647;display:none;touch-action:none;";
    overlay.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      lastActivity = Date.now();
      render();
    });
    document.body.appendChild(overlay);
    return overlay;
  }

  function minutesOf(hhmm) {
    var m = /^(\d{1,2}):(\d{2})$/.exec(hhmm || "");
    return m ? Number(m[1]) * 60 + Number(m[2]) : null;
  }

  function inNightWindow(d) {
    if (!settings.night_enabled) return false;
    var start = minutesOf(settings.night_start), end = minutesOf(settings.night_end);
    if (start === null || end === null || start === end) return false;
    var cur = d.getHours() * 60 + d.getMinutes();
    // end < start means the window crosses midnight (e.g. 22:00-06:00)
    return start < end ? (cur >= start && cur < end) : (cur >= start || cur < end);
  }

  function render() {
    if (!ensureOverlay()) return;
    var dim = inNightWindow(new Date()) && Date.now() - lastActivity >= WAKE_MS;
    var pct = Math.min(90, Math.max(10, Number(settings.dim_percent) || 70));
    overlay.style.opacity = String(pct / 100);
    overlay.style.display = dim ? "block" : "none";
  }

  // ===== Return to launcher when idle =====
  function fieldIsDirty(el) {
    var type = (el.type || "").toLowerCase();
    if (type === "hidden" || type === "submit" || type === "button" || type === "reset" || type === "image") return false;
    if (type === "checkbox" || type === "radio") return el.checked !== el.defaultChecked;
    if (type === "file") return !!(el.files && el.files.length);
    if (el.tagName === "SELECT") {
      var opts = el.options, hasDefault = false, i;
      for (i = 0; i < opts.length; i++) if (opts[i].defaultSelected) hasDefault = true;
      // No explicit default: the browser preselects the first option.
      if (!hasDefault && !el.multiple) return el.selectedIndex > 0;
      for (i = 0; i < opts.length; i++) if (opts[i].selected !== opts[i].defaultSelected) return true;
      return false;
    }
    return el.value !== el.defaultValue;
  }

  // Never navigate away from an in-progress edit: any field changed from
  // how the page loaded, or a focused field the user has typed into. (A
  // focused-but-untouched field doesn't count — node pages autofocus the
  // scan/search box on every load, which would otherwise block this
  // forever.)
  function hasUnsavedWork() {
    var active = document.activeElement;
    if (active && typedIn && typedIn.has(active)) return true;
    var fields = document.querySelectorAll("input, textarea, select");
    for (var i = 0; i < fields.length; i++) if (fieldIsDirty(fields[i])) return true;
    return false;
  }

  function maybeReturnToLauncher() {
    var minutes = Number(settings.idle_return_minutes) || 0;
    if (onLauncher || minutes <= 0) return;
    if (Date.now() - lastActivity < minutes * 60 * 1000) return;
    if (hasUnsavedWork()) return;
    location.href = launcherHome;
  }

  // ===== Start =====
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", render);
  else render();

  setInterval(function () {
    render();
    maybeReturnToLauncher();
  }, TICK_MS);

  // The launcher page stays open for days, so it re-reads settings itself;
  // node pages just get the current ones on their next load.
  if (onLauncher) {
    setInterval(function () {
      fetch("/api/display-settings")
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (s) { if (s) { settings = s; render(); } })
        .catch(function () {});
    }, SETTINGS_REFRESH_MS);
  }
})();
