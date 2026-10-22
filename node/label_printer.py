# ============================================================
# FILE: label_printer.py
# StockHive — Barcode label rendering + sending to the print bridge
#
# Labels are drawn here (item name + Code 128 bars + readable code)
# as a 1-bit image exactly the CTP500's 384-dot print width, then
# POSTed as a PNG to printbridge/ on the kitchen Pi, which owns the
# Bluetooth link. Nothing here talks Bluetooth.
# ============================================================

# ============================================================
# SECTION: Imports
# ============================================================
import io

import requests
from PIL import Image, ImageDraw, ImageFont

import db

# ============================================================
# SECTION: Constants
# ============================================================
PRINT_WIDTH = 384            # CTP500 fixed raster width, in dots
MARGIN = 12                  # left/right margin for text, in dots
QUIET_MODULES = 10           # Code 128 quiet zone each side, in modules
MAX_MODULE_PX = 4            # widest bar module we'll draw
BAR_HEIGHT = 90

NAME_SIZES = (34, 30, 26, 22, 18)
NAME_MAX_LINES = 2
DATE_TEXT_SIZE = 22          # "Made: Oct 22" / "Use by: Oct 29" lines
CODE_TEXT_SIZE = 22
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# (connect, read) seconds — short so a dead bridge/printer gives a red
# banner quickly instead of tying up the single gunicorn worker.
BRIDGE_TIMEOUT = (3, 20)

_BRIDGE_URL_KEY = "printer_bridge_url"
_TOKEN_KEY = "printer_token"

_FONT_PATHS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
)


# ============================================================
# SECTION: Settings
# ============================================================

def get_bridge_url() -> str:
    return db.get_setting(_BRIDGE_URL_KEY, "")


def set_bridge_url(value: str) -> None:
    value = (value or "").strip().rstrip("/")
    if value and "://" not in value:
        value = "http://" + value
    db.set_setting(_BRIDGE_URL_KEY, value)


def has_token() -> bool:
    return bool(db.get_setting(_TOKEN_KEY, ""))


def set_token(value: str) -> None:
    db.set_setting(_TOKEN_KEY, (value or "").strip())


# ============================================================
# SECTION: Rendering
# ============================================================

def _font(size: int):
    for path in _FONT_PATHS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    # Pillow's bundled scalable font (Pillow >= 10.1) — LXCs often
    # have no system fonts installed.
    return ImageFont.load_default(size=size)


def _wrap(draw, text: str, font, max_width: int) -> list:
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width:
            line = trial
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def _fit_name(draw, name: str, max_width: int):
    """Largest font where the name wraps into NAME_MAX_LINES lines that
    each fit; at the smallest size, overflow is cut with an ellipsis."""
    for size in NAME_SIZES:
        font = _font(size)
        lines = _wrap(draw, name, font, max_width)
        if len(lines) <= NAME_MAX_LINES and all(draw.textlength(l, font=font) <= max_width for l in lines):
            return font, lines
    font = _font(NAME_SIZES[-1])
    lines = _wrap(draw, name, font, max_width)[:NAME_MAX_LINES]
    last = lines[-1] if lines else ""
    while last and draw.textlength(last + "…", font=font) > max_width:
        last = last[:-1]
    if lines:
        lines[-1] = last.rstrip() + "…"
    return font, lines


def _line_height(font) -> int:
    top, bottom = font.getbbox("Ag")[1::2]
    return bottom - top


def _short_date(iso) -> str:
    """'2026-10-22' -> 'Oct 22'; '' for blank/unparseable (line is skipped)."""
    try:
        _year, month, day = (int(p) for p in str(iso or "").split("-"))
        return f"{_MONTHS[month - 1]} {day}"
    except (ValueError, IndexError):
        return ""


def render_label(name: str, code: str, made=None, use_by=None) -> Image.Image:
    """1-bit label image, PRINT_WIDTH wide, black ink on white. `made` and
    `use_by` are optional YYYY-MM-DD dates printed under the name."""
    # Imported here so a node missing python-barcode still starts;
    # only printing fails (see print_label).
    from barcode import Code128

    name = (name or "").strip() or code
    pattern = Code128(code).build()[0]   # '1' = bar module, '0' = space
    date_lines = [
        f"{label}: {_short_date(value)}"
        for label, value in (("Made", made), ("Use by", use_by))
        if _short_date(value)
    ]

    total_modules = len(pattern) + 2 * QUIET_MODULES
    module_px = min(MAX_MODULE_PX, PRINT_WIDTH // total_modules)
    if module_px < 1:
        raise ValueError("Barcode is too long to fit on a label")

    scratch = ImageDraw.Draw(Image.new("1", (1, 1)))
    name_font, name_lines = _fit_name(scratch, name, PRINT_WIDTH - 2 * MARGIN)
    date_font = _font(DATE_TEXT_SIZE)
    code_font = _font(CODE_TEXT_SIZE)
    name_lh = _line_height(name_font) + 6
    date_lh = _line_height(date_font) + 6
    code_lh = _line_height(code_font)

    height = 10 + name_lh * len(name_lines) + date_lh * len(date_lines) + 8 + BAR_HEIGHT + 8 + code_lh + 12
    img = Image.new("1", (PRINT_WIDTH, height), 1)
    draw = ImageDraw.Draw(img)

    y = 10
    for line in name_lines:
        w = draw.textlength(line, font=name_font)
        draw.text(((PRINT_WIDTH - w) / 2, y - name_font.getbbox("Ag")[1]), line, font=name_font, fill=0)
        y += name_lh
    for line in date_lines:
        w = draw.textlength(line, font=date_font)
        draw.text(((PRINT_WIDTH - w) / 2, y - date_font.getbbox("Ag")[1]), line, font=date_font, fill=0)
        y += date_lh
    y += 8

    x = (PRINT_WIDTH - len(pattern) * module_px) // 2
    for i, bit in enumerate(pattern):
        if bit == "1":
            draw.rectangle([x + i * module_px, y, x + (i + 1) * module_px - 1, y + BAR_HEIGHT - 1], fill=0)
    y += BAR_HEIGHT + 8

    w = draw.textlength(code, font=code_font)
    draw.text(((PRINT_WIDTH - w) / 2, y - code_font.getbbox("Ag")[1]), code, font=code_font, fill=0)
    return img


# ============================================================
# SECTION: Sending
# ============================================================

def send_to_bridge(img: Image.Image) -> tuple:
    """Returns (ok, message). Never raises for network/printer trouble."""
    url = get_bridge_url()
    token = db.get_setting(_TOKEN_KEY, "")
    if not url or not token:
        return False, "Printer not set up — add the bridge address and token in Settings"

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    try:
        resp = requests.post(
            f"{url}/print",
            data=buf.getvalue(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "image/png"},
            timeout=BRIDGE_TIMEOUT,
        )
    except requests.Timeout:
        return False, f"Print bridge at {url} timed out"
    except requests.RequestException:
        return False, f"Can't reach the print bridge at {url}"

    if resp.status_code == 200:
        return True, "Label sent to printer"
    if resp.status_code == 401:
        return False, "Print bridge rejected the token — check it in Settings"
    try:
        detail = resp.json().get("error") or resp.reason
    except ValueError:
        detail = resp.reason
    return False, f"Print failed: {detail}"


def print_label(name: str, code: str, made=None, use_by=None) -> tuple:
    try:
        img = render_label(name, code, made=made, use_by=use_by)
    except ImportError:
        return False, "Label printing needs python-barcode — run Settings > Update on this node"
    except Exception as e:
        return False, f"Can't make a label for {code}: {e}"
    return send_to_bridge(img)
