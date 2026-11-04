// ============================================================
// FILE: camscan.js (served at /static/camscan/camscan.js)
// Phone camera barcode scanning for the node's scan boxes.
//
// Adds a camera button next to every <input data-camscan>, but only on
// a phone/tablet that isn't the kiosk. On desktops and the kiosk this
// script checks, finds nothing to do and exits: no DOM, no CSS, and the
// ZXing decoder is never downloaded.
//
// A decoded code is typed into the same input and the form is submitted,
// exactly like a USB scanner (which types the code, then Enter), so it
// goes through the normal /scan, /remove-one, /move-scan or
// /edit/alias-add route.
//
//   HTTPS: live camera view. Needs a camera reported by the browser.
//   HTTP:  phones hide the camera API on plain HTTP, so the button opens
//          the photo picker instead and decodes the photo.
//
// Decoding uses the browser's BarcodeDetector when it supports every
// format below (Android Chrome), otherwise the vendored ZXing library
// (iPhone Safari, Firefox).
// ============================================================
(function () {
  "use strict";
  if (window.StockHiveCamScan) return;
  window.StockHiveCamScan = {};

  var script = document.currentScript;
  var base = (script && script.src)
    ? script.src.replace(/camscan\.js(?:[?#].*)?$/, "")
    : "/static/camscan/";
  var ZXING_SRC = base + "vendor/zxing-library-0.21.3.min.js";
  var CSS_HREF = base + "camscan.css";

  var NATIVE_FORMATS = ["upc_a", "upc_e", "ean_13", "ean_8", "code_128", "code_39", "qr_code"];
  var SCAN_INTERVAL_MS = 120;
  var LIVE_MAX_W = 900;      // crop is scaled down to this before decoding
  var PHOTO_MAX = 1600;      // photos are scaled down to this (longest side)
  var CAMERA_SVG =
    '<svg viewBox="0 0 24 24" width="26" height="26" aria-hidden="true" focusable="false" ' +
    'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M4 8h3l2-3h6l2 3h3a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1z"/>' +
    '<circle cx="12" cy="14" r="3.5"/></svg>';

  // ----------------------------------------------------------
  // Should this device get a camera button?
  // ----------------------------------------------------------
  function isKiosk() {
    // Launcher tiles hand the kiosk flag over as #kiosk=1 / #kiosk=0;
    // kiosk.js saves it to localStorage (and may already have stripped it).
    var m = /^#kiosk=([01])$/.exec(location.hash || "");
    if (m) return m[1] === "1";
    try { return window.localStorage.getItem("stockhive.kiosk") === "1"; } catch (e) { return false; }
  }

  // User agent only. Touch and screen size are NOT used: the kiosk is a
  // touchscreen too.
  function isPhoneOrTablet() {
    var uad = navigator.userAgentData;
    if (uad && uad.mobile) return true;
    var ua = navigator.userAgent || "";
    if (/Android|iPhone|iPad|iPod|Mobile|Silk|Kindle/i.test(ua)) return true;
    // iPadOS Safari reports itself as a Mac; real Macs have no touch points.
    if (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1) return true;
    return false;
  }

  function hasCamera() {
    var md = navigator.mediaDevices;
    if (!md || !md.enumerateDevices || !md.getUserMedia) return Promise.resolve(false);
    return md.enumerateDevices().then(function (list) {
      for (var i = 0; i < list.length; i++) if (list[i].kind === "videoinput") return true;
      return false;
    }, function () { return false; });
  }

  // ----------------------------------------------------------
  // Decoders (native BarcodeDetector, else vendored ZXing)
  // Each returns { detect(canvas) -> Promise<{text, format} | null> }
  // ----------------------------------------------------------
  var detectorPromise = null;
  function getDetector() {
    if (!detectorPromise) {
      detectorPromise = nativeDetector().then(function (d) { return d || zxingDetector(); });
      detectorPromise.catch(function () { detectorPromise = null; }); // let a later tap retry
    }
    return detectorPromise;
  }

  function nativeDetector() {
    var BD = window.BarcodeDetector;
    if (!BD || !BD.getSupportedFormats) return Promise.resolve(null);
    return BD.getSupportedFormats().then(function (supported) {
      for (var i = 0; i < NATIVE_FORMATS.length; i++) {
        if (supported.indexOf(NATIVE_FORMATS[i]) < 0) return null;
      }
      var bd = new BD({ formats: NATIVE_FORMATS });
      return {
        detect: function (source) {
          return bd.detect(source).then(function (r) {
            return (r && r.length) ? { text: r[0].rawValue, format: r[0].format } : null;
          }, function () { return null; });
        }
      };
    }).catch(function () { return null; });
  }

  function loadScript(src) {
    return new Promise(function (resolve, reject) {
      if (window.ZXing) return resolve();
      var s = document.createElement("script");
      s.src = src;
      s.onload = function () { window.ZXing ? resolve() : reject(new Error("ZXing missing")); };
      s.onerror = function () { s.remove(); reject(new Error("ZXing failed to load")); };
      document.head.appendChild(s);
    });
  }

  function zxingDetector() {
    return loadScript(ZXING_SRC).then(function () {
      var Z = window.ZXing;
      var F = Z.BarcodeFormat;
      var names = {};
      names[F.UPC_A] = "upc_a"; names[F.UPC_E] = "upc_e";
      names[F.EAN_13] = "ean_13"; names[F.EAN_8] = "ean_8";
      names[F.CODE_128] = "code_128"; names[F.CODE_39] = "code_39";
      names[F.QR_CODE] = "qr_code";
      var hints = new Map();
      hints.set(Z.DecodeHintType.POSSIBLE_FORMATS,
        [F.UPC_A, F.UPC_E, F.EAN_13, F.EAN_8, F.CODE_128, F.CODE_39, F.QR_CODE]);
      hints.set(Z.DecodeHintType.TRY_HARDER, true);
      var reader = new Z.MultiFormatReader();
      reader.setHints(hints);
      return {
        detect: function (canvas) {
          try {
            var lum = new Z.HTMLCanvasElementLuminanceSource(canvas);
            var bmp = new Z.BinaryBitmap(new Z.HybridBinarizer(lum));
            var r = reader.decodeWithState(bmp);
            return Promise.resolve({ text: r.getText(), format: names[r.getBarcodeFormat()] || "" });
          } catch (e) {
            return Promise.resolve(null); // NotFound / checksum / format: just no read
          } finally {
            try { reader.reset(); } catch (e2) {}
          }
        }
      };
    });
  }

  // ----------------------------------------------------------
  // Feedback: beep (Web Audio, no file), vibrate, toast
  // ----------------------------------------------------------
  var audioCtx = null;
  function primeAudio() {
    // iOS only allows audio from a context created/resumed in a tap.
    try {
      var AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return;
      if (!audioCtx) audioCtx = new AC();
      if (audioCtx.state === "suspended") audioCtx.resume();
    } catch (e) {}
  }
  function beep() {
    try {
      if (!audioCtx) return;
      var o = audioCtx.createOscillator(), g = audioCtx.createGain();
      o.type = "square"; o.frequency.value = 1800; g.gain.value = 0.06;
      o.connect(g); g.connect(audioCtx.destination);
      var t = audioCtx.currentTime;
      o.start(t); o.stop(t + 0.12);
    } catch (e) {}
  }
  function vibrate() {
    try { if (navigator.vibrate) navigator.vibrate(80); } catch (e) {}
  }

  var toastEl = null, toastTimer = null;
  function toast(text, sticky) {
    if (!toastEl) {
      toastEl = document.createElement("div");
      toastEl.className = "camscan-toast";
      toastEl.setAttribute("role", "status");
      toastEl.setAttribute("aria-live", "polite");
      document.body.appendChild(toastEl);
    }
    toastEl.textContent = text;
    toastEl.classList.add("show");
    clearTimeout(toastTimer);
    if (!sticky) toastTimer = setTimeout(hideToast, 3500);
  }
  function hideToast() {
    clearTimeout(toastTimer);
    if (toastEl) toastEl.classList.remove("show");
  }

  // ----------------------------------------------------------
  // Hand the code to the normal scan flow
  // ----------------------------------------------------------
  var submitting = false;
  function submitCode(input, code) {
    if (submitting) return;
    submitting = true;
    input.value = code;
    try { input.dispatchEvent(new Event("input", { bubbles: true })); } catch (e) {}
    var form = input.form;
    if (!form) { submitting = false; return; }
    // Short pause so the beep is heard before the page navigates.
    setTimeout(function () {
      if (typeof form.requestSubmit === "function") form.requestSubmit();
      else form.submit();
    }, 150);
  }
  // Coming back with the Back button (page restored from cache).
  window.addEventListener("pageshow", function (e) { if (e.persisted) submitting = false; });

  // ----------------------------------------------------------
  // Live mode (HTTPS): full-screen camera with a scan box
  // ----------------------------------------------------------
  var live = null;

  function openLive(input) {
    if (live) return;
    primeAudio();
    getDetector().catch(function () {}); // start loading ZXing while the camera starts

    var ov = document.createElement("div");
    ov.className = "camscan-overlay";
    ov.setAttribute("role", "dialog");
    ov.setAttribute("aria-modal", "true");
    ov.setAttribute("aria-label", "Scan with camera");
    ov.innerHTML =
      '<video class="camscan-video" playsinline muted autoplay></video>' +
      '<div class="camscan-shade"><div class="camscan-frame"></div></div>' +
      '<div class="camscan-msg">Starting camera…</div>' +
      '<div class="camscan-actions">' +
      '<button type="button" class="camscan-photo" hidden>Take a photo instead</button>' +
      '<button type="button" class="camscan-cancel">Cancel</button></div>';
    document.body.appendChild(ov);
    document.documentElement.classList.add("camscan-lock");

    var video = ov.querySelector("video");
    video.muted = true;
    video.setAttribute("playsinline", "");
    var frame = ov.querySelector(".camscan-frame");
    var msg = ov.querySelector(".camscan-msg");
    var photoBtn = ov.querySelector(".camscan-photo");
    var cancelBtn = ov.querySelector(".camscan-cancel");

    live = { ov: ov, video: video, stream: null, stopped: false, timer: null, input: input };
    cancelBtn.addEventListener("click", closeLive);
    photoBtn.addEventListener("click", function () { closeLive(); openPhoto(input); });
    cancelBtn.focus({ preventScroll: true });

    var session = live;
    navigator.mediaDevices.getUserMedia({
      audio: false,
      video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } }
    }).then(function (stream) {
      if (session.stopped) { stopStream(stream); return; }
      session.stream = stream;
      video.srcObject = stream;
      var p = video.play();
      if (p && p.catch) p.catch(function () {});
      return getDetector().then(function (det) {
        if (session.stopped) return;
        msg.textContent = "Point the camera at a barcode";
        scanLoop(session, det, frame, msg, input);
      }, function () {
        msg.textContent = "The scanner couldn't load. Reload the page and try again.";
      });
    }).catch(function (err) {
      if (session.stopped) return;
      var denied = err && (err.name === "NotAllowedError" || err.name === "SecurityError");
      msg.textContent = denied
        ? "Camera is blocked. Allow camera access for this site in your browser settings."
        : "Couldn't start the camera.";
      photoBtn.hidden = false;
    });
  }

  function stopStream(stream) {
    try { stream.getTracks().forEach(function (t) { t.stop(); }); } catch (e) {}
  }

  function closeLive() {
    if (!live) return;
    var s = live;
    live = null;
    s.stopped = true;
    clearTimeout(s.timer);
    if (s.stream) stopStream(s.stream);
    try { s.video.srcObject = null; } catch (e) {}
    if (s.ov.parentNode) s.ov.parentNode.removeChild(s.ov);
    document.documentElement.classList.remove("camscan-lock");
  }

  // The part of the video that sits under the on-screen scan box
  // (the video is shown with object-fit: cover), padded a little.
  function cropRect(video, frame) {
    var vw = video.videoWidth, vh = video.videoHeight;
    var vr = video.getBoundingClientRect(), fr = frame.getBoundingClientRect();
    if (!vw || !vh || !vr.width || !vr.height) return null;
    var scale = Math.max(vr.width / vw, vr.height / vh);
    var dx = (vr.width - vw * scale) / 2, dy = (vr.height - vh * scale) / 2;
    var pad = 0.1;
    var x = (fr.left - vr.left - dx) / scale - fr.width / scale * pad;
    var y = (fr.top - vr.top - dy) / scale - fr.height / scale * pad;
    var w = fr.width / scale * (1 + 2 * pad), h = fr.height / scale * (1 + 2 * pad);
    x = Math.max(0, x); y = Math.max(0, y);
    w = Math.min(vw - x, w); h = Math.min(vh - y, h);
    return (w > 0 && h > 0) ? { x: x, y: y, w: w, h: h } : null;
  }

  function scanLoop(session, det, frame, msg, input) {
    var canvas = document.createElement("canvas");
    var ctx = canvas.getContext("2d", { willReadFrequently: true });
    var last = null;

    function tick() {
      if (session.stopped) return;
      var r = (session.video.readyState >= 2) ? cropRect(session.video, frame) : null;
      if (!r) { session.timer = setTimeout(tick, SCAN_INTERVAL_MS); return; }
      var k = Math.min(1, LIVE_MAX_W / r.w);
      canvas.width = Math.round(r.w * k);
      canvas.height = Math.round(r.h * k);
      ctx.drawImage(session.video, r.x, r.y, r.w, r.h, 0, 0, canvas.width, canvas.height);
      det.detect(canvas).then(function (res) {
        if (session.stopped) return;
        if (res && res.text) {
          // A 1D barcode must read the same twice in a row (guards against
          // a misread digit). QR codes carry their own error correction.
          if (res.format === "qr_code" || last === res.text) {
            frame.classList.add("hit");
            beep(); vibrate();
            closeLive();
            submitCode(input, res.text);
            return;
          }
          last = res.text;
        }
        session.timer = setTimeout(tick, SCAN_INTERVAL_MS);
      });
    }
    tick();
  }

  // ----------------------------------------------------------
  // Photo mode (plain HTTP, or camera blocked): decode a still photo
  // ----------------------------------------------------------
  var fileInput = null, photoTarget = null;

  function openPhoto(input) {
    primeAudio();
    getDetector().catch(function () {}); // start loading while the camera app is open
    photoTarget = input;
    if (!fileInput) {
      fileInput = document.createElement("input");
      fileInput.type = "file";
      fileInput.accept = "image/*";
      fileInput.setAttribute("capture", "environment");
      fileInput.className = "camscan-file";
      fileInput.tabIndex = -1;
      fileInput.setAttribute("aria-hidden", "true");
      fileInput.addEventListener("change", onPhoto);
      document.body.appendChild(fileInput);
    }
    fileInput.value = "";
    fileInput.click();
  }

  function onPhoto() {
    var file = fileInput.files && fileInput.files[0];
    var input = photoTarget;
    if (!file || !input) return;
    toast("Reading barcode…", true);
    Promise.all([getDetector(), loadImage(file)]).then(function (both) {
      return decodeStill(both[0], both[1]);
    }).then(function (res) {
      if (res && res.text) {
        hideToast();
        beep(); vibrate();
        submitCode(input, res.text);
      } else {
        toast("Couldn't read barcode, try again");
      }
    }, function () {
      toast("Couldn't read barcode, try again");
    });
  }

  function loadImage(file) {
    return new Promise(function (resolve, reject) {
      var url = URL.createObjectURL(file);
      var img = new Image();
      img.onload = function () { URL.revokeObjectURL(url); resolve(img); };
      img.onerror = function () { URL.revokeObjectURL(url); reject(new Error("bad image")); };
      img.src = url;
    });
  }

  function drawScaled(img, maxSide, rotate) {
    var w = img.naturalWidth, h = img.naturalHeight;
    var k = Math.min(1, maxSide / Math.max(w, h));
    var sw = Math.round(w * k), sh = Math.round(h * k);
    var c = document.createElement("canvas");
    var ctx = c.getContext("2d", { willReadFrequently: true });
    if (rotate) {
      c.width = sh; c.height = sw;
      ctx.translate(sh, 0);
      ctx.rotate(Math.PI / 2);
    } else {
      c.width = sw; c.height = sh;
    }
    ctx.drawImage(img, 0, 0, sw, sh);
    return c;
  }

  // Try the photo as-is, turned sideways, then smaller (helps blurry shots).
  function decodeStill(det, img) {
    var attempts = [[PHOTO_MAX, false], [PHOTO_MAX, true], [900, false]];
    var i = 0;
    function next() {
      if (i >= attempts.length) return Promise.resolve(null);
      var a = attempts[i++];
      return det.detect(drawScaled(img, a[0], a[1])).then(function (res) {
        return (res && res.text) ? res : next();
      });
    }
    return next();
  }

  // ----------------------------------------------------------
  // Buttons
  // ----------------------------------------------------------
  function addButton(input, mode) {
    var hadFocus = document.activeElement === input;
    var group = document.createElement("span");
    group.className = "camscan-group";
    input.parentNode.insertBefore(group, input);
    group.appendChild(input);
    if (hadFocus) { try { input.focus({ preventScroll: true }); } catch (e) {} }

    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn camscan-btn";
    btn.title = "Scan with camera";
    btn.setAttribute("aria-label", "Scan with camera");
    btn.innerHTML = CAMERA_SVG;
    btn.addEventListener("click", function () {
      if (mode === "live") openLive(input); else openPhoto(input);
    });
    group.appendChild(btn);
  }

  function setup(inputs, mode) {
    var link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = CSS_HREF;
    document.head.appendChild(link);
    document.documentElement.classList.add("camscan-on");
    for (var i = 0; i < inputs.length; i++) addButton(inputs[i], mode);

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && live) closeLive();
    });
    // Never leave the camera running in the background.
    window.addEventListener("pagehide", closeLive);
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "hidden") closeLive();
    });
  }

  function init() {
    try {
      if (isKiosk() || !isPhoneOrTablet()) return;
      var inputs = document.querySelectorAll("input[data-camscan]");
      if (!inputs.length) return;
      if (window.isSecureContext) {
        hasCamera().then(function (ok) { if (ok) setup(inputs, "live"); });
      } else {
        setup(inputs, "photo");
      }
    } catch (e) { /* fail quietly: no button */ }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
