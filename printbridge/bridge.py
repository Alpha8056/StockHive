#!/usr/bin/env python3
# ============================================================
# FILE: bridge.py
# StockHive Print Bridge — receives label PNGs over HTTP on the LAN
# and prints them on a Core Innovations CTP500 over Bluetooth.
#
# Runs on the kitchen Pi (the one with Bluetooth). StockHive nodes
# render the label; this only converts it to printer raster and sends.
#
# Protocol (from n3m0-22/thermal-printer and
# thirtythreedown/CTP500PrinterApp):
#   RFCOMM channel 1, 384-dot raster width
#   status 1E 47 03 (38-byte reply) -> init 1B 40 -> start 1D 49 F0 19
#   -> GS v 0 raster 1D 76 30 00 wL wH hL hH <rows, 1 bit = black>
#   -> feed 0A x3 -> end 9A
#
# Config comes from the environment (see printbridge.env.example).
# ============================================================

import argparse
import hmac
import io
import json
import os
import socket
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image, ImageDraw

# ============================================================
# SECTION: Printer protocol
# ============================================================
PRINT_WIDTH = 384
CMD_STATUS = b"\x1e\x47\x03"
CMD_INIT = b"\x1b\x40"
CMD_START = b"\x1d\x49\xf0\x19"
CMD_RASTER = b"\x1d\x76\x30\x00"
CMD_FEED = b"\x0a" * 3
CMD_END = b"\x9a"
STATUS_LEN = 38

CHUNK = 4096
CMD_DELAY = 0.25       # pause between commands, as in the reference apps
CONNECT_TIMEOUT = 6    # seconds; an off/out-of-range printer fails fast
STATUS_TIMEOUT = 2
IO_TIMEOUT = 10

MAX_BODY = 1024 * 1024
MAX_HEIGHT = 2000
BUSY_WAIT = 10         # seconds a request waits for an in-progress print


def to_raster(img: Image.Image) -> bytes:
    """Fit to 384 wide, threshold to 1-bit (no dithering — keeps
    barcode edges sharp) and pack with 1 = ink."""
    img = img.convert("L")
    if img.width > PRINT_WIDTH:
        img = img.resize((PRINT_WIDTH, max(1, round(img.height * PRINT_WIDTH / img.width))))
    if img.width < PRINT_WIDTH:
        padded = Image.new("L", (PRINT_WIDTH, img.height), 255)
        padded.paste(img, ((PRINT_WIDTH - img.width) // 2, 0))
        img = padded
    # Dark pixels -> 255 so PIL's "1" packing writes a 1 bit for ink.
    bits = img.point(lambda v: 255 if v < 128 else 0, mode="1")
    width_bytes = PRINT_WIDTH // 8
    return (
        CMD_RASTER
        + struct.pack("<HH", width_bytes, bits.height)
        + bits.tobytes()
    )


def print_image(img: Image.Image, mac: str, channel: int) -> None:
    raster = to_raster(img)
    sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
    try:
        sock.settimeout(CONNECT_TIMEOUT)
        sock.connect((mac, channel))
        # Both reference apps ask for status first; the reply isn't needed.
        sock.settimeout(STATUS_TIMEOUT)
        sock.sendall(CMD_STATUS)
        try:
            sock.recv(STATUS_LEN)
        except socket.timeout:
            pass
        sock.settimeout(IO_TIMEOUT)

        sock.sendall(CMD_INIT)
        time.sleep(CMD_DELAY)
        sock.sendall(CMD_START)
        time.sleep(CMD_DELAY)
        for i in range(0, len(raster), CHUNK):
            sock.sendall(raster[i:i + CHUNK])
        sock.sendall(CMD_FEED)
        sock.sendall(CMD_END)
        time.sleep(0.5)  # let the printer drain before the link drops
    finally:
        try:
            sock.close()
        except OSError:
            pass


def test_pattern() -> Image.Image:
    img = Image.new("L", (PRINT_WIDTH, 160), 255)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, PRINT_WIDTH - 1, 159], outline=0, width=3)
    d.text((16, 16), "StockHive print bridge", fill=0)
    d.text((16, 36), "self-test OK", fill=0)
    for i in range(0, PRINT_WIDTH - 32, 16):
        d.rectangle([16 + i, 70, 16 + i + 7, 140], fill=0)
    return img


# ============================================================
# SECTION: HTTP server
# ============================================================
_print_lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    server_version = "StockHivePrintBridge/1"
    config = {}

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"ok": True})
        else:
            self._send(404, {"error": "Not found"})

    def do_POST(self):
        if self.path != "/print":
            self._send(404, {"error": "Not found"})
            return

        expected = f"Bearer {self.config['token']}"
        if not hmac.compare_digest(self.headers.get("Authorization", ""), expected):
            self._send(401, {"error": "Bad token"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0:
            self._send(400, {"error": "Empty body"})
            return
        if length > MAX_BODY:
            self._send(413, {"error": "Image too large"})
            return

        try:
            img = Image.open(io.BytesIO(self.rfile.read(length)))
            img.load()
        except Exception:
            self._send(400, {"error": "Not a readable image"})
            return
        if img.height * PRINT_WIDTH / max(1, img.width) > MAX_HEIGHT:
            self._send(413, {"error": "Image too tall"})
            return

        if not _print_lock.acquire(timeout=BUSY_WAIT):
            self._send(503, {"error": "Printer busy, try again"})
            return
        try:
            print_image(img, self.config["mac"], self.config["channel"])
        except OSError as e:
            self.log_message("print failed: %s", e)
            self._send(502, {"error": f"Printer not reachable ({e.__class__.__name__})"})
            return
        except Exception as e:
            self.log_message("print failed: %r", e)
            self._send(500, {"error": "Bridge error, see its log"})
            return
        finally:
            _print_lock.release()
        self._send(200, {"ok": True})


# ============================================================
# SECTION: Main
# ============================================================

def load_config() -> dict:
    mac = os.environ.get("PRINTER_MAC", "").strip()
    token = os.environ.get("TOKEN", "").strip()
    if not mac or not token:
        sys.exit("PRINTER_MAC and TOKEN must be set (see printbridge.env.example)")
    return {
        "mac": mac,
        "token": token,
        "channel": int(os.environ.get("RFCOMM_CHANNEL", "1")),
        "bind": os.environ.get("BIND", "0.0.0.0"),
        "port": int(os.environ.get("PORT", "8765")),
    }


def main():
    parser = argparse.ArgumentParser(description="StockHive CTP500 print bridge")
    parser.add_argument("--selftest", action="store_true",
                        help="print a test pattern straight to the printer and exit (no HTTP)")
    args = parser.parse_args()

    if args.selftest:
        mac = os.environ.get("PRINTER_MAC", "").strip()
        if not mac:
            sys.exit("Set PRINTER_MAC first, e.g. PRINTER_MAC=AA:BB:CC:DD:EE:FF python3 bridge.py --selftest")
        print_image(test_pattern(), mac, int(os.environ.get("RFCOMM_CHANNEL", "1")))
        print("Sent test pattern.")
        return

    Handler.config = load_config()
    server = ThreadingHTTPServer((Handler.config["bind"], Handler.config["port"]), Handler)
    print(f"Print bridge on {Handler.config['bind']}:{Handler.config['port']} -> {Handler.config['mac']}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
