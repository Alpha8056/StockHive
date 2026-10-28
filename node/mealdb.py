# ============================================================
# FILE: mealdb.py
# TheMealDB (https://www.themealdb.com/) client for Recipes > Browse
# TheMealDB: search, categories, one recipe's details, converting a
# recipe to a local one, and the background "Import all" job.
#
# The node serves one request at a time, so every call here has a short
# timeout and an overall deadline, and every failure becomes MealDBError
# with one friendly message instead of an error page or a hang. Only the
# Browse page and imports call this; the Recipes list and recipe pages
# show stored data only.
#
# Their terms: the test key "1" is for development and educational use;
# a supporter key can be set in Settings > Recipes. Data and images must
# be credited to TheMealDB (CREDIT_TEXT / CREDIT_URL).
# ============================================================

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait

import requests

import db
from inventory import _connect

API_ROOT = "https://www.themealdb.com/api/json/v1"
DEFAULT_KEY = "1"
CREDIT_TEXT = "Recipe data and images: TheMealDB"
CREDIT_URL = "https://www.themealdb.com/"

TIMEOUT = (3, 5)              # connect, read (seconds)
CALL_DEADLINE_SECONDS = 8     # whole call, however slowly the reply trickles in
MAX_REPLY_BYTES = 4 * 1024 * 1024
PAGE_SIZE = 12                # category results per page
DETAIL_WORKERS = 6            # parallel detail lookups for one page
PAGE_DETAIL_SECONDS = 6       # a page waits at most this long for details
IMPORT_PAUSE_SECONDS = 0.3    # between TheMealDB calls in "Import all"
IMPORT_MAX_FAILURES_IN_A_ROW = 3
CATEGORY_CACHE_SECONDS = 24 * 3600

FRIENDLY_ERROR = "Couldn't reach TheMealDB right now. Check this node's internet connection and try again."

# Used if the category list can't be fetched and none is cached.
FALLBACK_CATEGORIES = ("Beef", "Breakfast", "Chicken", "Dessert", "Goat", "Lamb", "Miscellaneous",
                       "Pasta", "Pork", "Seafood", "Side", "Starter", "Vegan", "Vegetarian")

# TheMealDB category -> this node's meal type. Anything not listed
# (including categories TheMealDB adds later) becomes Supper.
MEAL_TYPE_FOR_CATEGORY = {
    "Breakfast": "Breakfast",
    "Side": "Side",
    "Starter": "Snack",
    "Dessert": "Snack",
}

_KEY_KEY = "mealdb_key"
_CATEGORIES_KEY = "mealdb_categories"
_KEY_RE = re.compile(r"^[A-Za-z0-9]{1,64}$")
_ID_RE = re.compile(r"^[0-9]{1,12}$")


class MealDBError(Exception):
    pass


# ------------------------------------------------------------
# SUBSECTION: Key (Settings > Recipes)
# ------------------------------------------------------------
def get_key() -> str:
    key = db.get_setting(_KEY_KEY) or DEFAULT_KEY
    return key if _KEY_RE.match(key) else DEFAULT_KEY


def has_custom_key() -> bool:
    return get_key() != DEFAULT_KEY


def set_key(value: str) -> None:
    """Blank keeps the saved key; "1" goes back to the free test key.
    Raises ValueError for anything that isn't letters and digits."""
    value = (value or "").strip()
    if not value:
        return
    if not _KEY_RE.match(value):
        raise ValueError("TheMealDB key should be letters and numbers only.")
    db.set_setting(_KEY_KEY, value)


def is_valid_id(value) -> bool:
    return bool(_ID_RE.match(str(value or "")))


# ------------------------------------------------------------
# SUBSECTION: HTTP
# ------------------------------------------------------------
def _get(endpoint: str, params: dict) -> dict:
    url = f"{API_ROOT}/{get_key()}/{endpoint}"
    start = time.monotonic()
    try:
        with requests.get(url, params=params, timeout=TIMEOUT, stream=True,
                          headers={"User-Agent": "StockHive"}) as r:
            r.raise_for_status()
            # read1() returns as soon as any bytes arrive, so the deadline
            # is checked even when a reply trickles in a byte at a time.
            read = getattr(r.raw, "read1", None) or (lambda n, **kw: r.raw.read(1024, **kw))
            body = b""
            while True:
                chunk = read(16384, decode_content=True)
                if not chunk:
                    break
                body += chunk
                if len(body) > MAX_REPLY_BYTES or time.monotonic() - start > CALL_DEADLINE_SECONDS:
                    raise MealDBError(FRIENDLY_ERROR)
        data = json.loads(body.decode("utf-8"))
    except (requests.RequestException, ValueError, UnicodeDecodeError):
        raise MealDBError(FRIENDLY_ERROR)
    if not isinstance(data, dict):
        raise MealDBError(FRIENDLY_ERROR)
    return data


def _meals(data: dict) -> list:
    """TheMealDB returns {"meals": null} for "nothing found"."""
    meals = data.get("meals") or []
    return [m for m in meals if isinstance(m, dict) and is_valid_id(m.get("idMeal"))]


# ------------------------------------------------------------
# SUBSECTION: Cache (mealdb_cache table)
# ------------------------------------------------------------
def cache_get(ids) -> dict:
    ids = [str(i) for i in ids]
    if not ids:
        return {}
    conn = _connect()
    try:
        cur = conn.cursor()
        marks = ",".join("?" * len(ids))
        cur.execute(f"SELECT external_id, data FROM mealdb_cache WHERE external_id IN ({marks});", ids)
        out = {}
        for r in cur.fetchall():
            try:
                out[r["external_id"]] = json.loads(r["data"])
            except ValueError:
                pass
        return out
    finally:
        conn.close()


def cache_put(meals) -> None:
    rows = [(str(m["idMeal"]), json.dumps(m)) for m in meals if is_valid_id(m.get("idMeal"))]
    if not rows:
        return
    conn = _connect()
    try:
        conn.executemany("""
            INSERT INTO mealdb_cache (external_id, data, fetched_at) VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(external_id) DO UPDATE SET data = excluded.data, fetched_at = CURRENT_TIMESTAMP;
        """, rows)
        conn.commit()
    finally:
        conn.close()


# ------------------------------------------------------------
# SUBSECTION: API calls
# ------------------------------------------------------------
def search(name: str) -> list:
    """Full recipes whose name matches (one call). Cached as a side effect."""
    meals = _meals(_get("search.php", {"s": name}))
    cache_put(meals)
    return meals


def categories() -> list:
    """Category names, cached for a day. Never raises: falls back to the
    last cached list, then to FALLBACK_CATEGORIES."""
    cached = None
    try:
        cached = json.loads(db.get_setting(_CATEGORIES_KEY) or "null")
    except ValueError:
        pass
    if cached and time.time() - cached.get("at", 0) < CATEGORY_CACHE_SECONDS:
        return cached["names"]
    try:
        data = _get("categories.php", {})
        names = [c["strCategory"] for c in data.get("categories") or []
                 if isinstance(c, dict) and isinstance(c.get("strCategory"), str) and c["strCategory"].strip()]
        if names:
            db.set_setting(_CATEGORIES_KEY, json.dumps({"at": time.time(), "names": names}))
            return names
    except MealDBError:
        pass
    return (cached or {}).get("names") or list(FALLBACK_CATEGORIES)


def category_meals(category: str) -> list:
    """[{id, name, thumb}] in a category (one call; no ingredients)."""
    return [
        {"id": str(m["idMeal"]), "name": m.get("strMeal") or "", "thumb": m.get("strMealThumb") or ""}
        for m in _meals(_get("filter.php", {"c": category}))
    ]


def lookup(meal_id: str) -> dict:
    """One full recipe, from the cache if fetched before."""
    meal_id = str(meal_id)
    cached = cache_get([meal_id]).get(meal_id)
    if cached:
        return cached
    meals = _meals(_get("lookup.php", {"i": meal_id}))
    if not meals:
        raise MealDBError("That recipe isn't on TheMealDB any more.")
    cache_put(meals[:1])
    return meals[0]


def details_for(ids) -> dict:
    """{id: full recipe} for a page of category results: cached ones at
    once, the rest fetched in parallel. Waits at most PAGE_DETAIL_SECONDS;
    any that don't arrive in time (or fail) are just left out."""
    ids = [str(i) for i in ids]
    found = cache_get(ids)
    missing = [i for i in ids if i not in found]
    if missing:
        pool = ThreadPoolExecutor(max_workers=min(DETAIL_WORKERS, len(missing)))
        futures = {pool.submit(lambda i: _meals(_get("lookup.php", {"i": i})), i): i for i in missing}
        done, _ = wait(futures, timeout=PAGE_DETAIL_SECONDS)
        pool.shutdown(wait=False, cancel_futures=True)
        fetched = []
        for f in done:
            try:
                meals = f.result()
            except MealDBError:
                continue
            if meals:
                fetched.append(meals[0])
        cache_put(fetched)
        found.update({str(m["idMeal"]): m for m in fetched})
    return found


# ------------------------------------------------------------
# SUBSECTION: Converting a TheMealDB recipe
# ------------------------------------------------------------
def _http_url(value):
    value = (value or "").strip()
    return value if re.match(r"^https?://", value, re.I) and len(value) <= 500 else None


def meal_lines(meal: dict) -> list:
    """[(amount, ingredient)] from strMeasure1..20 / strIngredient1..20."""
    out = []
    for i in range(1, 21):
        ingredient = (meal.get(f"strIngredient{i}") or "").strip()
        if ingredient:
            out.append(((meal.get(f"strMeasure{i}") or "").strip(), ingredient))
    return out


def meal_type_for(category) -> str:
    return MEAL_TYPE_FOR_CATEGORY.get((category or "").strip(), "Supper")


def meal_to_fields(meal: dict) -> dict:
    """What recipes.import_recipe() saves."""
    instructions = (meal.get("strInstructions") or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return {
        "name": (meal.get("strMeal") or "").strip(),
        "meal_type": meal_type_for(meal.get("strCategory")),
        "instructions": instructions,
        "source": "themealdb",
        "external_id": str(meal["idMeal"]),
        "image_url": _http_url(meal.get("strMealThumb")),
        "source_url": _http_url(meal.get("strSource")),
        "source_category": (meal.get("strCategory") or "").strip() or None,
        "source_area": (meal.get("strArea") or "").strip() or None,
        "lines": meal_lines(meal),
    }


def as_recipe(meal: dict) -> dict:
    """A recipe-shaped dict (for recipes.match_recipe) without saving it."""
    return {"lines": [{"amount": a, "ingredient": i} for a, i in meal_lines(meal)]}


# ------------------------------------------------------------
# SUBSECTION: "Import all" (background job)
# Runs in its own thread so the node keeps serving pages; the Browse
# page polls import_status(), which never calls TheMealDB. One job at a
# time; it's lost if the node restarts (already-imported ones are
# skipped when it's started again).
# ------------------------------------------------------------
_job_lock = threading.Lock()
_job = {"state": "idle"}
_cancel = threading.Event()


def import_status() -> dict:
    with _job_lock:
        return dict(_job)


def cancel_import() -> None:
    _cancel.set()


def start_import_all(category: str, import_fn, imported_ids_fn) -> bool:
    """False if a job is already running. import_fn(fields) -> (id, created);
    imported_ids_fn() -> {external_id: recipe_id}."""
    with _job_lock:
        if _job.get("state") == "running":
            return False
        _job.clear()
        _job.update({"state": "running", "category": category, "total": 0, "processed": 0,
                     "imported": 0, "skipped": 0, "failed": 0, "message": ""})
    _cancel.clear()
    threading.Thread(target=_run_import_all, args=(category, import_fn, imported_ids_fn), daemon=True).start()
    return True


def _set(**changes):
    with _job_lock:
        _job.update(changes)


def _bump(field):
    with _job_lock:
        _job[field] = _job.get(field, 0) + 1
        _job["processed"] = _job.get("processed", 0) + 1


def _run_import_all(category, import_fn, imported_ids_fn):
    try:
        try:
            entries = category_meals(category)
        except MealDBError as e:
            _set(state="failed", message=str(e))
            return
        _set(total=len(entries))
        existing = imported_ids_fn()
        failures_in_a_row = 0
        for entry in entries:
            if _cancel.is_set():
                _set(state="cancelled", message="Stopped.")
                return
            if entry["id"] in existing:
                _bump("skipped")
                continue
            was_cached = bool(cache_get([entry["id"]]))
            try:
                meal = lookup(entry["id"])
                _, created = import_fn(meal_to_fields(meal))
                _bump("imported" if created else "skipped")
                failures_in_a_row = 0
            except MealDBError as e:
                _bump("failed")
                failures_in_a_row += 1
                if failures_in_a_row >= IMPORT_MAX_FAILURES_IN_A_ROW:
                    _set(state="failed", message=str(e))
                    return
            except Exception as e:
                print("[mealdb] import failed:", e, flush=True)
                _bump("failed")
            if not was_cached:
                time.sleep(IMPORT_PAUSE_SECONDS)       # be gentle with TheMealDB
        _set(state="done", message="")
    except Exception as e:                              # never let the thread die silently
        print("[mealdb] import job crashed:", e, flush=True)
        _set(state="failed", message="Import stopped because of an error.")
