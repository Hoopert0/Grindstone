"""The backpack read from the game itself (core.gamestate): what's in each of the 28 slots,
by item name - no hovering, no icon matching, no learned name templates.

    slots()            -> [{'id', 'count', 'name', 'key'}] x 28, or None (no game data)
    occupied()         -> [bool] x 28, or None
    find("tinderbox")  -> [slot, ...] holding that item (by key)
    kind(key)          -> 'raw' | 'cooked' | 'burnt' | 'log' | 'tool' | 'product' | 'loot' | None
    is_product(key)    -> safe to drop (logs, ores, fish, unstrung bows...); food only with food=True

Every caller keeps its old pixel/hover way as the fallback for when this returns None.
Keys are lower-case names with underscores: "Raw shrimps" -> "raw_shrimps".
"""
import re

from lumberjack.core import gamestate

# never dropped, and found by name for the bots that need them
TOOLS = {"tinderbox", "knife", "small_fishing_net", "big_fishing_net", "fishing_rod", "fly_fishing_rod",
         "harpoon", "lobster_pot", "fishing_bait", "feather", "hammer", "chisel", "bow_string",
         "coins", "needle", "thread"}
COOKED = {"shrimps", "anchovies", "sardine", "herring", "trout", "pike", "salmon", "tuna", "lobster",
          "swordfish", "shark", "cod", "mackerel", "bass", "monkfish", "cooked_chicken", "cooked_meat",
          "bread", "cake"}
LOGS = {"logs", "oak_logs", "willow_logs", "maple_logs", "yew_logs", "magic_logs", "teak_logs",
        "mahogany_logs", "achey_tree_logs"}
ORES = {"clay", "coal", "rune_essence", "pure_essence"}
# what fights leave behind: worth banking, fine to drop
LOOT = {"bones", "big_bones", "burnt_bones", "wolf_bones", "bat_bones", "cowhide", "goblin_mail",
        "bronze_med_helm", "iron_dagger", "bronze_spear", "bronze_bolts", "body_rune", "mind_rune",
        "water_rune", "earth_rune", "air_rune", "fire_rune", "chaos_rune", "nature_rune", "law_rune",
        "grimy_guam", "grimy_marrentill", "grimy_tarromin", "grimy_harralander", "grimy_ranarr",
        "seaweed", "egg", "bucket", "eye_of_newt", "beer", "air_talisman", "earth_talisman"}

# a new account's starter kit that no task uses (each task spawns its own gear): dropped between
# tasks when there's no bank, so a fresh account has room for food and supplies
STARTER = {"pot", "bronze_dagger", "bronze_sword", "wooden_shield", "shortbow", "bronze_arrow",
           "bronze_arrows", "bucket", "empty_pot"}

# the tools each task uses (axe/pickaxe matched by name); everything else can be banked between tasks
TASK_TOOLS = {
    "woodcutting": {"tinderbox", "knife"},
    "firemaking": {"tinderbox"},
    "fishing": {"small_fishing_net", "big_fishing_net", "fishing_rod", "fly_fishing_rod", "harpoon",
                "lobster_pot", "fishing_bait", "feather", "tinderbox"},
    "mining": set(),
    "cooking": {"tinderbox"},
    "fletching": {"knife"},
    "crafting": {"chisel"},
    "smithing": {"hammer"},
    "thieving": {"coins"},
    "combat": {"coins"},
}


def key(name):
    """'Raw shrimps' -> 'raw_shrimps', 'Oak shortbow (u)' -> 'oak_shortbow_(u)'."""
    return re.sub(r"\s+", "_", (name or "").strip().lower())


def source():
    """The shared GameState, or None (see gamestate.shared)."""
    return gamestate.shared()


def slots():
    gs = source()
    if gs is None:
        return None
    try:
        out = gs.inv()
    except gamestate.GameStateError:
        gamestate.drop_shared()         # the game closed / add-on gone: fall back for a while
        return None
    for s in out:
        s["key"] = key(s.get("name")) if s.get("id", -1) >= 0 else None
    return out


def worn():
    """Keys of the items we're wearing/wielding (equipment), or None without game data."""
    gs = source()
    if gs is None:
        return None
    try:
        return [key(s["name"]) for s in gs.inv(94) if s.get("id", -1) >= 0]
    except gamestate.GameStateError:
        gamestate.drop_shared()
        return None


def have(pred):
    """Is an item matching pred(key) carried or worn? None without game data."""
    inv, eq = slots(), worn()
    if inv is None:
        return None
    return any(s["key"] and pred(s["key"]) for s in inv) or any(pred(k) for k in eq or [])


def occupied():
    s = slots()
    return None if s is None else [x["id"] >= 0 for x in s]


def find(item, inv=None):
    """Slots holding `item` (a key such as 'tinderbox' or 'raw_shrimps')."""
    inv = slots() if inv is None else inv
    if inv is None:
        return None
    return [i for i, s in enumerate(inv) if s["key"] == item]


def kind(k):
    if not k:
        return None
    if k in TOOLS or k.endswith("_axe") or k.endswith("pickaxe") or k == "axe":
        return "tool"
    if k.startswith("raw_"):
        return "raw"
    if k.startswith("burnt_"):
        return "burnt"
    if k in COOKED:
        return "cooked"
    if k in LOGS:
        return "log"
    if k in LOOT or k.startswith("grimy_") or k.endswith("_bones"):
        return "loot"
    if (k.endswith("_ore") or k in ORES or k.endswith("_(u)") or k in ("arrow_shaft", "arrow_shafts")
            or k.startswith("uncut_") or k.endswith("_stock")):
        return "product"
    return None


def is_product(k, food=False):
    """Safe to drop. Anything not positively known as a product (weapons, armour, runes,
    quest items...) is kept - losing a tool is far worse than keeping a log."""
    kd = kind(k)
    return kd in ("raw", "burnt", "log", "product", "loot") or (food and kd == "cooked")


def needed_for(k, task):
    """Does `task` use item `k`? Fishing cuts its own fire logs, so it wants an axe too; combat
    wants its food. Coins always stay (they cost nothing to carry: one slot)."""
    if not k:
        return False
    if k == "coins" or k in TASK_TOOLS.get(task, ()):
        return True
    if (k.endswith("_axe") or k == "axe") and not k.endswith("pickaxe"):
        return task in ("woodcutting", "fishing")
    if k.endswith("pickaxe"):
        return task == "mining"
    if kind(k) == "cooked":
        return task in ("combat", "ranged", "thieving", "magic")
    return False
