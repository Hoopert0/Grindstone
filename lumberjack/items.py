"""Helpful items and their IDs for the singleplayer ::item command (you're admin there).

IDs are the standard RuneScape 2 item IDs, which 2009scape uses; check one in game with
::itemsearch <name> if something spawns wrong.
"""
import time

CATALOG = [
    # (group, key, name, id, note)
    ("Axes", "bronze_axe", "Bronze axe", 1351, "Woodcutting 1"),
    ("Axes", "iron_axe", "Iron axe", 1349, "Woodcutting 1"),
    ("Axes", "steel_axe", "Steel axe", 1353, "Woodcutting 6"),
    ("Axes", "mithril_axe", "Mithril axe", 1355, "Woodcutting 21"),
    ("Axes", "adamant_axe", "Adamant axe", 1357, "Woodcutting 31"),
    ("Axes", "rune_axe", "Rune axe", 1359, "Woodcutting 41"),
    ("Axes", "dragon_axe", "Dragon axe", 6739, "Woodcutting 61"),
    ("Pickaxes", "bronze_pickaxe", "Bronze pickaxe", 1265, "Mining 1"),
    ("Pickaxes", "iron_pickaxe", "Iron pickaxe", 1267, "Mining 1"),
    ("Pickaxes", "steel_pickaxe", "Steel pickaxe", 1269, "Mining 6"),
    ("Pickaxes", "mithril_pickaxe", "Mithril pickaxe", 1273, "Mining 21"),
    ("Pickaxes", "adamant_pickaxe", "Adamant pickaxe", 1271, "Mining 31"),
    ("Pickaxes", "rune_pickaxe", "Rune pickaxe", 1275, "Mining 41"),
    ("Weapons", "bronze_scimitar", "Bronze scimitar", 1321, "Attack 1"),
    ("Weapons", "iron_scimitar", "Iron scimitar", 1323, "Attack 1"),
    ("Weapons", "steel_scimitar", "Steel scimitar", 1325, "Attack 5"),
    ("Weapons", "mithril_scimitar", "Mithril scimitar", 1329, "Attack 20"),
    ("Weapons", "adamant_scimitar", "Adamant scimitar", 1331, "Attack 30"),
    ("Weapons", "rune_scimitar", "Rune scimitar", 1333, "Attack 40"),
    ("Ranged", "shortbow", "Shortbow", 841, "Ranged 1"),
    ("Ranged", "oak_shortbow", "Oak shortbow", 843, "Ranged 5"),
    ("Ranged", "willow_shortbow", "Willow shortbow", 849, "Ranged 20"),
    ("Ranged", "maple_shortbow", "Maple shortbow", 853, "Ranged 30"),
    ("Ranged", "yew_shortbow", "Yew shortbow", 857, "Ranged 40"),
    ("Ranged", "magic_shortbow", "Magic shortbow", 861, "Ranged 50"),
    ("Ranged", "iron_arrow", "Iron arrow", 884, "any bow, x1000"),
    ("Ranged", "steel_arrow", "Steel arrow", 886, "oak bow+"),
    ("Ranged", "mithril_arrow", "Mithril arrow", 888, "willow bow+"),
    ("Ranged", "adamant_arrow", "Adamant arrow", 890, "maple bow+"),
    ("Ranged", "rune_arrow", "Rune arrow", 892, "yew bow+"),
    ("Armour", "bronze_full_helm", "Bronze full helm", 1155, "Defence 1"),
    ("Armour", "bronze_platebody", "Bronze platebody", 1117, "Defence 1"),
    ("Armour", "bronze_platelegs", "Bronze platelegs", 1075, "Defence 1"),
    ("Armour", "bronze_kiteshield", "Bronze kiteshield", 1189, "Defence 1"),
    ("Armour", "iron_full_helm", "Iron full helm", 1153, "Defence 1"),
    ("Armour", "iron_platebody", "Iron platebody", 1115, "Defence 1"),
    ("Armour", "iron_platelegs", "Iron platelegs", 1067, "Defence 1"),
    ("Armour", "iron_kiteshield", "Iron kiteshield", 1191, "Defence 1"),
    ("Armour", "steel_full_helm", "Steel full helm", 1157, "Defence 5"),
    ("Armour", "steel_platebody", "Steel platebody", 1119, "Defence 5"),
    ("Armour", "steel_platelegs", "Steel platelegs", 1069, "Defence 5"),
    ("Armour", "steel_kiteshield", "Steel kiteshield", 1193, "Defence 5"),
    ("Armour", "mithril_full_helm", "Mithril full helm", 1159, "Defence 20"),
    ("Armour", "mithril_platebody", "Mithril platebody", 1121, "Defence 20"),
    ("Armour", "mithril_platelegs", "Mithril platelegs", 1071, "Defence 20"),
    ("Armour", "mithril_kiteshield", "Mithril kiteshield", 1197, "Defence 20"),
    ("Armour", "adamant_full_helm", "Adamant full helm", 1161, "Defence 30"),
    ("Armour", "adamant_platebody", "Adamant platebody", 1123, "Defence 30"),
    ("Armour", "adamant_platelegs", "Adamant platelegs", 1073, "Defence 30"),
    ("Armour", "adamant_kiteshield", "Adamant kiteshield", 1199, "Defence 30"),
    ("Armour", "rune_full_helm", "Rune full helm", 1163, "Defence 40"),
    ("Armour", "rune_platebody", "Rune platebody", 1127, "Defence 40"),
    ("Armour", "rune_platelegs", "Rune platelegs", 1079, "Defence 40"),
    ("Armour", "rune_kiteshield", "Rune kiteshield", 1201, "Defence 40"),
    ("Tools", "tinderbox", "Tinderbox", 590, "Firemaking"),
    ("Tools", "knife", "Knife", 946, "Fletching"),
    ("Tools", "bow_string", "Bow string", 1777, "Fletching (string bows)"),
    ("Tools", "hammer", "Hammer", 2347, "Smithing"),
    ("Tools", "chisel", "Chisel", 1755, "Crafting"),
    ("Tools", "bird_snare", "Bird snare", 10006, "Hunter 1"),
    ("Tools", "box_trap", "Box trap", 10008, "Hunter 27"),
    ("Fishing", "small_fishing_net", "Small fishing net", 303, "Net fishing"),
    ("Fishing", "fishing_rod", "Fishing rod", 307, "Bait fishing"),
    ("Fishing", "fishing_bait", "Fishing bait", 313, "x500 recommended"),
    ("Fishing", "fly_fishing_rod", "Fly fishing rod", 309, "Lure fishing"),
    ("Fishing", "feather", "Feather", 314, "x500 recommended"),
    ("Fishing", "lobster_pot", "Lobster pot", 301, "Cage fishing"),
    ("Fishing", "harpoon", "Harpoon", 311, "Harpoon fishing"),
    ("Logs", "logs", "Logs", 1511, "Firemaking 1"),
    ("Logs", "oak_logs", "Oak logs", 1521, "Firemaking 15"),
    ("Logs", "willow_logs", "Willow logs", 1519, "Firemaking 30"),
    ("Logs", "maple_logs", "Maple logs", 1517, "Firemaking 45"),
    ("Logs", "yew_logs", "Yew logs", 1515, "Firemaking 60"),
    ("Logs", "magic_logs", "Magic logs", 1513, "Firemaking 75"),
    ("Raw fish", "raw_shrimps", "Raw shrimps", 317, "Cooking 1"),
    ("Raw fish", "raw_sardine", "Raw sardine", 327, "Cooking 1"),
    ("Raw fish", "raw_herring", "Raw herring", 345, "Cooking 5"),
    ("Raw fish", "raw_trout", "Raw trout", 335, "Cooking 15"),
    ("Raw fish", "raw_pike", "Raw pike", 349, "Cooking 20"),
    ("Raw fish", "raw_salmon", "Raw salmon", 331, "Cooking 25"),
    ("Raw fish", "raw_tuna", "Raw tuna", 359, "Cooking 30"),
    ("Raw fish", "raw_lobster", "Raw lobster", 377, "Cooking 40"),
    ("Raw fish", "raw_swordfish", "Raw swordfish", 371, "Cooking 45"),
    ("Bars", "bronze_bar", "Bronze bar", 2349, "Smithing 1"),
    ("Bars", "iron_bar", "Iron bar", 2351, "Smithing 15"),
    ("Bars", "steel_bar", "Steel bar", 2353, "Smithing 30"),
    ("Bars", "mithril_bar", "Mithril bar", 2359, "Smithing 50"),
    ("Bars", "adamantite_bar", "Adamantite bar", 2361, "Smithing 70"),
    ("Bars", "runite_bar", "Runite bar", 2363, "Smithing 85"),
    ("Runes", "air_rune", "Air rune", 556, "x1000"),
    ("Runes", "water_rune", "Water rune", 555, "x1000"),
    ("Runes", "earth_rune", "Earth rune", 557, "x1000"),
    ("Runes", "fire_rune", "Fire rune", 554, "x1000"),
    ("Runes", "mind_rune", "Mind rune", 558, "x1000"),
    ("Runes", "chaos_rune", "Chaos rune", 562, "x1000"),
    ("Runes", "nature_rune", "Nature rune", 561, "x1000"),
    ("Runes", "law_rune", "Law rune", 563, "x1000"),
    ("Runes", "pure_essence", "Pure essence", 7936, "Runecrafting"),
    ("Bones", "bones", "Bones", 526, "Prayer 4.5 xp"),
    ("Bones", "big_bones", "Big bones", 532, "Prayer 15 xp"),
    ("Bones", "dragon_bones", "Dragon bones", 536, "Prayer 72 xp"),
    ("Gems", "uncut_opal", "Uncut opal", 1625, "Crafting 1"),
    ("Gems", "uncut_jade", "Uncut jade", 1627, "Crafting 13"),
    ("Gems", "uncut_red_topaz", "Uncut red topaz", 1629, "Crafting 16"),
    ("Gems", "uncut_sapphire", "Uncut sapphire", 1623, "Crafting 20"),
    ("Gems", "uncut_emerald", "Uncut emerald", 1621, "Crafting 27"),
    ("Gems", "uncut_ruby", "Uncut ruby", 1619, "Crafting 34"),
    ("Gems", "uncut_diamond", "Uncut diamond", 1617, "Crafting 43"),
    ("Gems", "uncut_dragonstone", "Uncut dragonstone", 1631, "Crafting 55"),
    ("Herblore", "guam_potion_(unf)", "Guam potion (unf)", 91, "Herblore 3"),
    ("Herblore", "marrentill_potion_(unf)", "Marrentill potion (unf)", 93, "Herblore 5"),
    ("Herblore", "tarromin_potion_(unf)", "Tarromin potion (unf)", 95, "Herblore 12"),
    ("Herblore", "harralander_potion_(unf)", "Harralander potion (unf)", 97, "Herblore 22"),
    ("Herblore", "ranarr_potion_(unf)", "Ranarr potion (unf)", 99, "Herblore 30"),
    ("Herblore", "toadflax_potion_(unf)", "Toadflax potion (unf)", 3002, "Herblore 34"),
    ("Herblore", "irit_potion_(unf)", "Irit potion (unf)", 101, "Herblore 45"),
    ("Herblore", "avantoe_potion_(unf)", "Avantoe potion (unf)", 103, "Herblore 50"),
    ("Herblore", "kwuarm_potion_(unf)", "Kwuarm potion (unf)", 105, "Herblore 55"),
    ("Herblore", "snapdragon_potion_(unf)", "Snapdragon potion (unf)", 3004, "Herblore 63"),
    ("Herblore", "cadantine_potion_(unf)", "Cadantine potion (unf)", 107, "Herblore 66"),
    ("Herblore", "lantadyme_potion_(unf)", "Lantadyme potion (unf)", 2483, "Herblore 69"),
    ("Herblore", "dwarf_weed_potion_(unf)", "Dwarf weed potion (unf)", 109, "Herblore 72"),
    ("Herblore", "torstol_potion_(unf)", "Torstol potion (unf)", 111, "Herblore 78"),
    ("Herblore", "eye_of_newt", "Eye of newt", 221, "attack / super attack"),
    ("Herblore", "unicorn_horn_dust", "Unicorn horn dust", 235, "antipoison"),
    ("Herblore", "limpwurt_root", "Limpwurt root", 225, "strength / super strength"),
    ("Herblore", "red_spiders'_eggs", "Red spiders' eggs", 223, "restore / super restore"),
    ("Herblore", "chocolate_dust", "Chocolate dust", 1975, "energy"),
    ("Herblore", "white_berries", "White berries", 239, "defence / super defence"),
    ("Herblore", "toad's_legs", "Toad's legs", 2152, "agility"),
    ("Herblore", "goat_horn_dust", "Goat horn dust", 9736, "combat"),
    ("Herblore", "snape_grass", "Snape grass", 231, "prayer / fishing"),
    ("Herblore", "mort_myre_fungus", "Mort myre fungus", 2970, "super energy"),
    ("Herblore", "dragon_scale_dust", "Dragon scale dust", 241, "weapon poison / antifire"),
    ("Herblore", "wine_of_zamorak", "Wine of zamorak", 245, "ranging"),
    ("Herblore", "potato_cactus", "Potato cactus", 3138, "magic"),
    ("Herblore", "jangerberries", "Jangerberries", 247, "zamorak brew"),
    ("Farming", "rake", "Rake", 5341, "weeds"),
    ("Farming", "seed_dibber", "Seed dibber", 5343, "planting"),
    ("Farming", "spade", "Spade", 952, "harvesting, digging up"),
    ("Farming", "supercompost", "Supercompost", 6034, "fewer diseases, +xp"),
    ("Farming", "plant_cure", "Plant cure", 6036, "cures a diseased patch"),
    ("Farming", "potato_seed", "Potato seed", 5318, "Farming 1"),
    ("Farming", "onion_seed", "Onion seed", 5319, "Farming 5"),
    ("Farming", "cabbage_seed", "Cabbage seed", 5324, "Farming 7"),
    ("Farming", "tomato_seed", "Tomato seed", 5322, "Farming 12"),
    ("Farming", "sweetcorn_seed", "Sweetcorn seed", 5320, "Farming 20"),
    ("Farming", "strawberry_seed", "Strawberry seed", 5323, "Farming 31"),
    ("Farming", "watermelon_seed", "Watermelon seed", 5321, "Farming 47"),
    ("Farming", "guam_seed", "Guam seed", 5291, "Farming 9"),
    ("Farming", "marrentill_seed", "Marrentill seed", 5292, "Farming 14"),
    ("Farming", "tarromin_seed", "Tarromin seed", 5293, "Farming 19"),
    ("Farming", "harralander_seed", "Harralander seed", 5294, "Farming 26"),
    ("Farming", "ranarr_seed", "Ranarr seed", 5295, "Farming 32"),
    ("Farming", "toadflax_seed", "Toadflax seed", 5296, "Farming 38"),
    ("Farming", "irit_seed", "Irit seed", 5297, "Farming 44"),
    ("Farming", "avantoe_seed", "Avantoe seed", 5298, "Farming 50"),
    ("Farming", "kwuarm_seed", "Kwuarm seed", 5299, "Farming 56"),
    ("Farming", "snapdragon_seed", "Snapdragon seed", 5300, "Farming 62"),
    ("Farming", "cadantine_seed", "Cadantine seed", 5301, "Farming 67"),
    ("Farming", "lantadyme_seed", "Lantadyme seed", 5302, "Farming 73"),
    ("Farming", "dwarf_weed_seed", "Dwarf weed seed", 5303, "Farming 79"),
    ("Farming", "torstol_seed", "Torstol seed", 5304, "Farming 85"),
    ("Food", "shrimps", "Shrimps", 315, "heals 3, x10 recommended"),
    ("Food", "lobster", "Lobster", 379, "heals 12, x10 recommended"),
    ("Food", "swordfish", "Swordfish", 373, "heals 14, x10 recommended"),
    ("Food", "shark", "Shark", 385, "heals 20, x10 recommended"),
    ("Other", "coins", "Coins", 995, "x10000 recommended"),
]
BY_KEY = {k: (name, iid) for _, k, name, iid, _ in CATALOG}

# best axe for a Woodcutting level (wieldability depends on Attack, but axes work from the backpack)
AXES = [("dragon_axe", 61), ("rune_axe", 41), ("adamant_axe", 31), ("mithril_axe", 21),
        ("steel_axe", 6), ("iron_axe", 1)]


def best_axe(wc_level):
    for key, lv in AXES:
        if wc_level >= lv:
            return key
    return "bronze_axe"


PICKAXES = [("rune_pickaxe", 41), ("adamant_pickaxe", 31), ("mithril_pickaxe", 21), ("steel_pickaxe", 6),
            ("iron_pickaxe", 1)]


def best_pickaxe(mining_level):
    for key, lv in PICKAXES:
        if mining_level >= lv:
            return key
    return "bronze_pickaxe"


# (bow, Ranged level, the best arrows it fires)
BOWS = [("magic_shortbow", 50, "rune_arrow"), ("yew_shortbow", 40, "rune_arrow"),
        ("maple_shortbow", 30, "adamant_arrow"), ("willow_shortbow", 20, "mithril_arrow"),
        ("oak_shortbow", 5, "steel_arrow"), ("shortbow", 1, "iron_arrow")]


def best_bow(ranged_level):
    """(bow, arrows) for a Ranged level."""
    for bow, lv, arrows in BOWS:
        if ranged_level >= lv:
            return bow, arrows
    return "shortbow", "iron_arrow"


# (metal, level) - scimitars by Attack, armour by Defence
METALS = [("rune", 40), ("adamant", 30), ("mithril", 20), ("steel", 5), ("iron", 1), ("bronze", 1)]
ARMOUR_PIECES = ("full_helm", "platebody", "platelegs", "kiteshield")


def best_metal(level):
    return next(m for m, lv in METALS if level >= lv)


def melee_gear(attack, defence):
    """The item keys a melee fighter should wear: the best scimitar + armour for the levels."""
    return [f"{best_metal(attack)}_scimitar"] + [f"{best_metal(defence)}_{p}" for p in ARMOUR_PIECES]


def ensure(ctx, have, key, amount=1, log=None):
    """With game data: if nothing carried or worn passes have(item_key), spawn `key`.
    Returns the new item's slot, -1 if we already have one, or None (no game data / nothing
    appeared - e.g. a full backpack)."""
    from lumberjack.core import backpack
    got = backpack.have(have)
    if got is None:
        return None
    if got:
        return -1
    before = {i for i, s in enumerate(backpack.slots() or []) if s["id"] >= 0}
    if log:
        log.info("No %s - spawning %s", key.replace("_", " "), f"{amount}" if amount > 1 else "one")
    spawn(ctx, key, amount)
    ctx.sleep(0.6)
    new = [i for i, s in enumerate(backpack.slots() or []) if s["id"] >= 0 and i not in before]
    return new[0] if new else None


def fill(ctx, key, want=28):
    """Spawn up to `want` of `key` into the free backpack slots, a few commands if the game gives
    fewer per command (with game data). Returns how many slots it filled."""
    from lumberjack.core import backpack
    used = lambda: sum(1 for s in backpack.slots() or [] if s["id"] >= 0)
    start = used()
    for _ in range(28):
        have = used()
        n = min(want - (have - start), 28 - have)
        if n <= 0:
            break
        spawn(ctx, key, n)
        ctx.sleep(0.4)
        if used() <= have:
            break
    return used() - start


def command(key, amount=1):
    name, iid = BY_KEY[key]
    return f"::item {iid} {amount}" if amount > 1 else f"::item {iid}"


def spawn(ctx, key, amount=1):
    """Type the ::item command into chat. Needs a free backpack slot (stackables excepted)."""
    import logging
    ctx.inp.move(260, 300)
    ctx.inp.type_text(command(key, amount), enter=True)
    ctx.sleep(1.2)
    logging.getLogger("items").info("Spawned %s x%d (%s)", BY_KEY[key][0], amount, command(key, amount))
    time.sleep(0.1)
