# ============================================================
# FILE: photos.py
# Optional per-item photos. An uploaded image is validated,
# EXIF-corrected, resized, and saved as a small JPEG (full +
# thumbnail) on disk under static/photos/ — only the resulting
# filename is ever stored in the database.
# ============================================================

import os
import uuid

from PIL import Image, ImageOps

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PHOTOS_DIR = os.path.join(BASE_DIR, "static", "photos")
THUMBS_DIR = os.path.join(PHOTOS_DIR, "thumbs")

FULL_MAX_SIDE = 600
THUMB_MAX_SIDE = 150
JPEG_QUALITY = 80

os.makedirs(THUMBS_DIR, exist_ok=True)  # also creates PHOTOS_DIR, its parent


def _resized_copy(img: Image.Image, max_side: int) -> Image.Image:
    copy = img.copy()
    copy.thumbnail((max_side, max_side), Image.LANCZOS)
    return copy


def save_photo(file_storage) -> str:
    """
    Validates and processes an uploaded image, returning the generated
    filename (shared by the full image and its thumbnail, which live in
    separate directories). Raises ValueError with a UI-safe message if
    the upload isn't a readable image — validated by actually attempting
    to decode it, not by trusting the filename/extension.
    """
    try:
        img = Image.open(file_storage.stream)
        img.verify()
    except Exception:
        raise ValueError("That file doesn't look like a valid image.")

    # verify() leaves the parser unusable for further reads — reopen
    # from the same stream for the real decode.
    file_storage.stream.seek(0)
    try:
        img = Image.open(file_storage.stream)
        # JPEG-only; a silent no-op for every other format. Lets Pillow's
        # decoder target roughly this resolution directly instead of
        # decoding the full sensor resolution first — a meaningful
        # speed/memory win on a Pi for a multi-megapixel phone photo,
        # since we're about to downscale to FULL_MAX_SIDE anyway.
        img.draft("RGB", (FULL_MAX_SIDE, FULL_MAX_SIDE))
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGB")
    except Exception:
        raise ValueError("Could not read that image.")

    filename = f"{uuid.uuid4().hex}.jpg"

    full = _resized_copy(img, FULL_MAX_SIDE)
    full.save(os.path.join(PHOTOS_DIR, filename), "JPEG", quality=JPEG_QUALITY, optimize=True)

    thumb = _resized_copy(img, THUMB_MAX_SIDE)
    thumb.save(os.path.join(THUMBS_DIR, filename), "JPEG", quality=JPEG_QUALITY, optimize=True)

    return filename


def delete_photo(filename: str) -> None:
    """Best-effort removal of both the full image and its thumbnail —
    called when a photo is replaced, removed, or its item is deleted.
    A missing file (already gone, or a stale DB reference) is not an
    error."""
    if not filename:
        return
    for path in (os.path.join(PHOTOS_DIR, filename), os.path.join(THUMBS_DIR, filename)):
        try:
            os.remove(path)
        except OSError:
            pass
