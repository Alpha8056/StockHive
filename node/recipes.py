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
_ALIASES_KEY = "recipe_aliases"
ALIAS_MAX_RULES = 200

# "Same as" rules: the left side is matched as if it were the right side.
# Starter set: left sides are names TheMealDB uses; right sides are the
# simple tags people give their items. Singular/plural is handled
# separately ("chicken breast" also covers "chicken breasts").
DEFAULT_ALIASES = (
    ("cheddar cheese", "cheddar"), ("parmesan cheese", "parmesan"),
    ("chicken breast", "chicken"), ("chicken thighs", "chicken"),
    ("garlic clove", "garlic"), ("minced garlic", "garlic"),
    ("olive oil", "oil"), ("vegetable oil", "oil"), ("sunflower oil", "oil"),
    ("plain flour", "flour"), ("caster sugar", "sugar"), ("granulated sugar", "sugar"),
    ("unsalted butter", "butter"), ("whole milk", "milk"),
    ("spring onions", "green onion"), ("scallions", "green onion"),
    ("chopped tomatoes", "canned tomatoes"),
    ("black pepper", "pepper"), ("sea salt", "salt"),
    ("minced beef", "ground beef"), ("double cream", "heavy cream"),
)


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


def _base_key(name):
    """Item-tag normalization with the last word made singular ("green
    onions" -> "green onion"). None for blank."""
    value = normalize_ingredient(name)
    if not value:
        return None
    words = value.split(" ")
    words[-1] = _singular(words[-1])
    return " ".join(words)


def ingredient_key(name, aliases=None):
    """The form two ingredient names are compared in, for local and
    imported recipes alike: _base_key(), then the "same as" rules
    (aliases = get_alias_map(), loaded once per page). Rules can chain
    ("a = b", "b = c") up to three steps; a loop just stops."""
    key = _base_key(name)
    if key and aliases:
        seen = {key}
        for _ in range(3):
            nxt = aliases.get(key)
            if not nxt or nxt in seen:
                break
            seen.add(nxt)
            key = nxt
    return key


def _tagged_items(aliases=None):
    """Every item with an ingredient tag, with what matching and the
    grocery list need to know about it."""
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT i.id, i.barcode, i.name, i.quantity, i.include_in_grocery, i.made_date, i.ingredient,
                   (g.item_id IS NOT NULL) AS on_list
            FROM items i LEFT JOIN grocery_list g ON g.item_id = i.id
            WHERE i.ingredient IS NOT NULL AND i.ingredient != '';
        """)
        return [dict(r, key=ingredient_key(r["ingredient"], aliases)) for r in cur.fetchall()]
    finally:
        conn.close()


def stock_context(expiring_barcodes=()):
    """What every match needs, gathered once per page: ingredient keys in
    stock (a tagged item with quantity > 0), staple keys, and keys of
    in-stock items in Expiring Soon."""
    expiring_barcodes = set(expiring_barcodes)
    aliases = get_alias_map()
    have, expiring = set(), set()
    for item in _tagged_items(aliases):
        if int(item["quantity"] or 0) > 0:
            have.add(item["key"])
            if item["barcode"] in expiring_barcodes:
                expiring.add(item["key"])
    staples = {ingredient_key(s, aliases) for s in get_staples()}
    return {"have": have, "staples": staples, "expiring": expiring, "aliases": aliases}


def match_recipe(recipe: dict, ctx: dict) -> dict:
    """Marks each ingredient line have / staple / missing. The rule:
    "have" = an item tagged with it has quantity > 0; a staple always
    counts as have. Returns lines plus have/total/missing counts and
    whether it uses anything in Expiring Soon."""
    lines = []
    for line in recipe["lines"]:
        key = ingredient_key(line["ingredient"], ctx.get("aliases"))
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
# SUBSECTION: "Same as" rules (Settings > Recipes)
# ------------------------------------------------------------
def get_alias_rules() -> list:
    """[(name, same_as)] as saved, normalized; the starter set until the
    box is first saved."""
    raw = db.get_setting(_ALIASES_KEY)
    if raw is None:
        return list(DEFAULT_ALIASES)
    try:
        rules = json.loads(raw)
    except (TypeError, ValueError):
        return list(DEFAULT_ALIASES)
    return [tuple(r) for r in rules if isinstance(r, list) and len(r) == 2]


def alias_rules_text() -> str:
    return "\n".join(f"{a} = {b}" for a, b in get_alias_rules())


def set_alias_rules(text: str) -> list:
    """One "name = same as" rule per line; lines without exactly one "="
    or with a blank side are ignored. Saves and returns the rules."""
    rules, seen = [], set()
    for line in (text or "").splitlines():
        parts = line.split("=")
        if len(parts) != 2:
            continue
        a, b = normalize_ingredient(parts[0]), normalize_ingredient(parts[1])
        if a and b and a != b and a not in seen:
            seen.add(a)
            rules.append((a, b))
    rules = rules[:ALIAS_MAX_RULES]
    db.set_setting(_ALIASES_KEY, json.dumps(rules))
    return rules


def get_alias_map() -> dict:
    """{base key of name: base key of same-as} for ingredient_key()."""
    out = {}
    for a, b in get_alias_rules():
        ka, kb = _base_key(a), _base_key(b)
        if ka and kb and ka != kb:
            out[ka] = kb
    return out


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


def import_recipe(fields: dict):
    """Saves an imported recipe (fields from mealdb.meal_to_fields) unless
    one with the same source + external_id exists; an existing one is
    never overwritten, so edits to it are kept. Returns (recipe_id,
    created)."""
    lines = []
    for amount, ingredient in fields["lines"][:MAX_LINES]:
        ingredient = normalize_ingredient(ingredient)
        if ingredient:
            lines.append((_clip(amount, AMOUNT_MAX), ingredient))
    meal_type = fields["meal_type"] if fields["meal_type"] in MEAL_TYPES else "Supper"
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE;")
        cur.execute("SELECT id FROM recipes WHERE source = ? AND external_id = ?;",
                    (fields["source"], fields["external_id"]))
        row = cur.fetchone()
        if row:
            conn.rollback()
            return row["id"], False
        cur.execute("""
            INSERT INTO recipes (name, meal_type, servings, instructions, notes, source, external_id,
                                 image_url, source_url, source_category, source_area)
            VALUES (?, ?, NULL, ?, NULL, ?, ?, ?, ?, ?, ?);
        """, (
            _clip(fields["name"], NAME_MAX) or "Untitled recipe", meal_type,
            _clip(fields.get("instructions"), TEXT_MAX), fields["source"], fields["external_id"],
            fields.get("image_url"), fields.get("source_url"),
            _clip(fields.get("source_category"), NAME_MAX), _clip(fields.get("source_area"), NAME_MAX),
        ))
        recipe_id = cur.lastrowid
        cur.executemany(
            "INSERT INTO recipe_ingredients (recipe_id, position, amount, ingredient) VALUES (?, ?, ?, ?);",
            [(recipe_id, i, amount, ingredient) for i, (amount, ingredient) in enumerate(lines)],
        )
        conn.commit()
        return recipe_id, True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def imported_ids(source: str) -> dict:
    """{external_id: recipe_id} for every recipe imported from `source`."""
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT external_id, id FROM recipes WHERE source = ? AND external_id IS NOT NULL;", (source,))
        return {r["external_id"]: r["id"] for r in cur.fetchall()}
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
    aliases = get_alias_map()
    covered = {i["key"] for i in _tagged_items(aliases) if int(i["quantity"] or 0) > 0 or i["on_list"]}
    if not covered:
        return
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, text FROM grocery_extras;")
        stale = [(r["id"],) for r in cur.fetchall() if ingredient_key(r["text"], aliases) in covered]
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
    aliases = get_alias_map()
    by_key = {}
    for item in _tagged_items(aliases):
        by_key.setdefault(item["key"], []).append(item)
    result = {"added": [], "already": [], "kept_off": []}
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT text FROM grocery_extras;")
        extra_keys = {ingredient_key(r["text"], aliases) for r in cur.fetchall()}
        for line in matched["lines"]:
            if line["status"] != "missing":
                continue
            name = line["ingredient"]
            items = by_key.get(line["key"], [])
            # Leftovers (made_date set) never go on the grocery list.
            included = [i for i in items if int(i["include_in_grocery"]) and not i["made_date"]]
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
