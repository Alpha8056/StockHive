# ============================================================
# FILE: recipes.py
# Recipes (Settings > Recipes): storage, matching recipes against
# what's in stock, staples, and the free-text Grocery List lines a
# recipe adds for ingredients no item is tagged with.
#
# Matching lives in one place (ingredient_key / match_recipe) so
# imported recipes (e.g. TheMealDB, later) are matched the same way.
# ============================================================

import json

import db
from inventory import _connect, normalize_ingredient, INGREDIENT_MAX_LEN

MEAL_TYPES = ("Breakfast", "Lunch", "Supper", "Side", "Snack")
DEFAULT_STAPLES = ("salt", "pepper", "water", "oil")

NAME_MAX = 100
SERVINGS_MAX = 20
AMOUNT_MAX = 30
TEXT_MAX = 20000          # instructions / notes
MAX_LINES = 100           # ingredient lines per recipe
_STAPLES_KEY = "recipe_staples"


# ------------------------------------------------------------
# SUBSECTION: Matching
# ------------------------------------------------------------
def _singular(word: str) -> str:
    """Simple English singular, so "egg"/"eggs" and "tomato"/"tomatoes"
    match. Only has to be consistent (both sides go through it), not
    perfect: irregular words like leaves/leaf don't match."""
    if len(word) <= 3:
        return word
    if word.endswith("ies") and len(word) > 4:
        word = word[:-3] + "y"                  # berries -> berry
    elif word.endswith("oes"):
        word = word[:-2]                        # tomatoes -> tomato
    elif word.endswith(("ches", "shes", "sses", "xes", "zes")):
        word = word[:-2]                        # peaches -> peach, boxes -> box
    elif word.endswith("s") and not word.endswith(("ss", "us", "is")):
        word = word[:-1]                        # eggs -> egg (not glass, hummus)
    if word.endswith("ie") and len(word) > 3:
        word = word[:-2] + "y"                  # cookie -> cooky, same as cookies
    return word


def ingredient_key(name):
    """The form two ingredient names are compared in: the item-tag
    normalization, with the last word made singular ("green onions" ->
    "green onion"). None for blank."""
    value = normalize_ingredient(name)
    if not value:
        return None
    words = value.split(" ")
    words[-1] = _singular(words[-1])
    return " ".join(words)


def _tagged_items():
    """Every item with an ingredient tag, with what matching and the
    grocery list need to know about it."""
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT i.id, i.barcode, i.name, i.quantity, i.include_in_grocery, i.ingredient,
                   (g.item_id IS NOT NULL) AS on_list
            FROM items i LEFT JOIN grocery_list g ON g.item_id = i.id
            WHERE i.ingredient IS NOT NULL AND i.ingredient != '';
        """)
        return [dict(r, key=ingredient_key(r["ingredient"])) for r in cur.fetchall()]
    finally:
        conn.close()


def stock_context(expiring_barcodes=()):
    """What every match needs, gathered once per page: ingredient keys in
    stock (a tagged item with quantity > 0), staple keys, and keys of
    in-stock items in Expiring Soon."""
    expiring_barcodes = set(expiring_barcodes)
    have, expiring = set(), set()
    for item in _tagged_items():
        if int(item["quantity"] or 0) > 0:
            have.add(item["key"])
            if item["barcode"] in expiring_barcodes:
                expiring.add(item["key"])
    staples = {ingredient_key(s) for s in get_staples()}
    return {"have": have, "staples": staples, "expiring": expiring}


def match_recipe(recipe: dict, ctx: dict) -> dict:
    """Marks each ingredient line have / staple / missing. The rule:
    "have" = an item tagged with it has quantity > 0; a staple always
    counts as have. Returns lines plus have/total/missing counts and
    whether it uses anything in Expiring Soon."""
    lines = []
    for line in recipe["lines"]:
        key = ingredient_key(line["ingredient"])
        if key in ctx["have"]:
            status = "have"
        elif key in ctx["staples"]:
            status = "staple"
        else:
            status = "missing"
        lines.append(dict(line, key=key, status=status, expiring=key in ctx["expiring"]))
    missing = sum(1 for l in lines if l["status"] == "missing")
    return {
        "lines": lines,
        "total": len(lines),
        "have": len(lines) - missing,
        "missing": missing,
        "can_make": missing == 0,
        "uses_expiring": any(l["expiring"] for l in lines),
    }


# ------------------------------------------------------------
# SUBSECTION: Staples (always count as "have")
# ------------------------------------------------------------
def get_staples() -> list:
    raw = db.get_setting(_STAPLES_KEY)
    if raw is None:
        return list(DEFAULT_STAPLES)
    try:
        values = json.loads(raw)
    except (TypeError, ValueError):
        return list(DEFAULT_STAPLES)
    return [v for v in values if isinstance(v, str)]


def set_staples(text: str) -> list:
    """Comma- or line-separated names, normalized and de-duplicated."""
    seen, staples = set(), []
    for part in (text or "").replace("\n", ",").split(","):
        value = normalize_ingredient(part)
        if value and value not in seen:
            seen.add(value)
            staples.append(value)
    db.set_setting(_STAPLES_KEY, json.dumps(staples))
    return staples


# ------------------------------------------------------------
# SUBSECTION: Recipes (storage)
# ------------------------------------------------------------
def _clip(value, limit):
    value = (value or "").strip()
    return value[:limit] or None


def _attach_lines(cur, recipes: list) -> list:
    if not recipes:
        return recipes
    by_id = {r["id"]: r for r in recipes}
    for r in recipes:
        r["lines"] = []
    marks = ",".join("?" * len(by_id))
    cur.execute(f"""
        SELECT recipe_id, amount, ingredient FROM recipe_ingredients
        WHERE recipe_id IN ({marks}) ORDER BY recipe_id, position, id;
    """, list(by_id))
    for row in cur.fetchall():
        by_id[row["recipe_id"]]["lines"].append({"amount": row["amount"] or "", "ingredient": row["ingredient"]})
    return recipes


def list_recipes() -> list:
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM recipes ORDER BY name COLLATE NOCASE;")
        return _attach_lines(cur, [dict(r) for r in cur.fetchall()])
    finally:
        conn.close()


def get_recipe(recipe_id: int):
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM recipes WHERE id = ?;", (recipe_id,))
        row = cur.fetchone()
        return _attach_lines(cur, [dict(row)])[0] if row else None
    finally:
        conn.close()


def save_recipe(recipe_id, name, meal_type, servings, instructions, notes, lines) -> int:
    """Creates (recipe_id None) or updates a local recipe and replaces its
    ingredient lines. lines = [(amount, ingredient)]; blank ingredients are
    dropped. Raises ValueError with a user-facing message."""
    name = _clip(name, NAME_MAX)
    if not name:
        raise ValueError("Recipe name is required.")
    if meal_type not in MEAL_TYPES:
        raise ValueError("Pick a meal type.")
    clean = []
    for amount, ingredient in lines:
        ingredient = normalize_ingredient(ingredient)
        if ingredient:
            clean.append((_clip(amount, AMOUNT_MAX), ingredient))
    if len(clean) > MAX_LINES:
        raise ValueError(f"A recipe can have up to {MAX_LINES} ingredients.")
    values = (name, meal_type, _clip(servings, SERVINGS_MAX), _clip(instructions, TEXT_MAX), _clip(notes, TEXT_MAX))

    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE;")
        if recipe_id is None:
            cur.execute("""
                INSERT INTO recipes (name, meal_type, servings, instructions, notes, source)
                VALUES (?, ?, ?, ?, ?, 'local');
            """, values)
            recipe_id = cur.lastrowid
        else:
            cur.execute("""
                UPDATE recipes SET name = ?, meal_type = ?, servings = ?, instructions = ?, notes = ?,
                       updated_at = CURRENT_TIMESTAMP
                WHERE id = ?;
            """, (*values, recipe_id))
            if cur.rowcount == 0:
                raise ValueError("Recipe not found.")
            cur.execute("DELETE FROM recipe_ingredients WHERE recipe_id = ?;", (recipe_id,))
        cur.executemany(
            "INSERT INTO recipe_ingredients (recipe_id, position, amount, ingredient) VALUES (?, ?, ?, ?);",
            [(recipe_id, i, amount, ingredient) for i, (amount, ingredient) in enumerate(clean)],
        )
        conn.commit()
        return recipe_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_recipe(recipe_id: int) -> bool:
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE;")
        cur.execute("DELETE FROM recipe_ingredients WHERE recipe_id = ?;", (recipe_id,))
        cur.execute("DELETE FROM recipes WHERE id = ?;", (recipe_id,))
        deleted = cur.rowcount > 0
        conn.commit()
        return deleted
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def suggestion_names() -> list:
    """Ingredient names from item tags and every recipe, for the
    recipe form's suggestions."""
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT ingredient FROM items WHERE ingredient IS NOT NULL AND ingredient != ''
            UNION SELECT ingredient FROM recipe_ingredients
            ORDER BY 1;
        """)
        return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


# ------------------------------------------------------------
# SUBSECTION: Grocery List extras
# The grocery list itself only holds items (one goes on automatically
# at quantity 0). A missing recipe ingredient that no item is tagged
# with goes here instead, as a plain text line.
# ------------------------------------------------------------
def prune_extras() -> None:
    """Drops extra lines that are now covered by a real item: one tagged
    with that ingredient is in stock, or is on the list itself."""
    covered = {i["key"] for i in _tagged_items() if int(i["quantity"] or 0) > 0 or i["on_list"]}
    if not covered:
        return
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, text FROM grocery_extras;")
        stale = [(r["id"],) for r in cur.fetchall() if ingredient_key(r["text"]) in covered]
        if stale:
            cur.executemany("DELETE FROM grocery_extras WHERE id = ?;", stale)
            conn.commit()
    finally:
        conn.close()


def list_extras() -> list:
    """[{id, text}] oldest first, after pruning covered lines."""
    prune_extras()
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, text FROM grocery_extras ORDER BY added_at, id;")
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def remove_extra(extra_id: int) -> None:
    conn = _connect()
    try:
        conn.execute("DELETE FROM grocery_extras WHERE id = ?;", (extra_id,))
        conn.commit()
    finally:
        conn.close()


def add_missing_to_grocery(recipe_id: int, matched: dict) -> dict:
    """"Add missing to Grocery List". For each missing ingredient (so every
    item tagged with it is at 0):
      - an item tagged with it that's set to go on the list is on it
        -> "already"
      - such items exist but were taken off the list (Grocery List >
        Remove) -> put back on the list ("added")
      - items tagged with it are all set to stay off the list -> left
        alone ("kept_off"); this never overrides that switch
      - no item is tagged with it -> a free-text extra line ("added", or
        "already" if that line is there)
    Returns {"added": [...], "already": [...], "kept_off": [...]}."""
    by_key = {}
    for item in _tagged_items():
        by_key.setdefault(item["key"], []).append(item)
    result = {"added": [], "already": [], "kept_off": []}
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT text FROM grocery_extras;")
        extra_keys = {ingredient_key(r["text"]) for r in cur.fetchall()}
        for line in matched["lines"]:
            if line["status"] != "missing":
                continue
            name = line["ingredient"]
            items = by_key.get(line["key"], [])
            included = [i for i in items if int(i["include_in_grocery"])]
            if any(i["on_list"] for i in included):
                result["already"].append(name)
            elif included:
                # At 0 but removed from the list earlier: put them back.
                cur.executemany(
                    "INSERT OR IGNORE INTO grocery_list (item_id, added_date) VALUES (?, CURRENT_TIMESTAMP);",
                    [(i["id"],) for i in included],
                )
                result["added"].append(name)
            elif items:
                result["kept_off"].append(name)
            elif line["key"] in extra_keys:      # "egg" already there covers "eggs"
                result["already"].append(name)
            else:
                cur.execute("INSERT OR IGNORE INTO grocery_extras (text, recipe_id) VALUES (?, ?);",
                            (name[:INGREDIENT_MAX_LEN], recipe_id))
                extra_keys.add(line["key"])
                result["added"].append(name)
        conn.commit()
        return result
    finally:
        conn.close()
