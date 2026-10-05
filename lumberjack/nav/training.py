"""Training routes: where (and on what) to train each skill at each level, so a plan step with
place "auto" moves on by itself - normal trees -> oaks -> willows, shrimps -> trout -> lobsters...

STARTER_PLACES are built in (marked ★) and show up with the saved places. Their tiles are
approximate - the bots search ~15-18 tiles around them. If one is off, stand at the right spot
and save a place under the SAME name: a saved place always wins over the built-in one.

pick(task, level) -> (place name, settings overrides) for the highest tier the level allows.
"""
STARTER_PLACES = {
    "★ Lumbridge trees": {"tile": [3192, 3236], "plane": 0, "note": "trees + oaks west of the castle"},
    "★ Draynor willows": {"tile": [3087, 3235], "plane": 0, "note": "willows south of the bank"},
    "★ Draynor fishing": {"tile": [3086, 3228], "plane": 0, "note": "net / bait"},
    "★ Lumbridge river": {"tile": [3239, 3244], "plane": 0, "note": "lure / bait"},
    "★ Catherby fishing": {"tile": [2837, 3432], "plane": 0, "note": "cage / harpoon, bank close by"},
    "★ Varrock east mine": {"tile": [3286, 3366], "plane": 0, "note": "copper, tin, iron"},
    "★ Barbarian mine": {"tile": [3081, 3421], "plane": 0, "note": "coal, tin"},
    "★ Lumbridge chickens": {"tile": [3233, 3295], "plane": 0},
    "★ Lumbridge cows": {"tile": [3258, 3276], "plane": 0},
    "★ Lumbridge goblins": {"tile": [3250, 3238], "plane": 0},
    "★ Al Kharid warriors": {"tile": [3293, 3173], "plane": 0, "note": "the palace"},
    "★ Lumbridge men": {"tile": [3222, 3240], "plane": 0, "note": "men & women around Lumbridge"},
    "★ Varrock guards": {"tile": [3212, 3462], "plane": 0, "note": "palace courtyard"},
    "★ Ardougne knights": {"tile": [2662, 3306], "plane": 0, "note": "the market"},
    "★ Varrock anvil": {"tile": [3188, 3425], "plane": 0, "note": "anvils south of the west bank"},
}

# task -> [(min level, place, Settings overrides)], lowest first
ROUTES = {
    "woodcutting": [
        (1, "★ Lumbridge trees", {"trees": ["tree"], "auto_trees": False}),
        (15, "★ Lumbridge trees", {"trees": ["oak", "tree"], "auto_trees": False}),
        (30, "★ Draynor willows", {"trees": ["willow"], "auto_trees": False}),
    ],
    "fishing": [
        (1, "★ Draynor fishing", {"fish_method": "net", "auto_fish": False}),
        (20, "★ Lumbridge river", {"fish_method": "lure", "auto_fish": False}),
        (40, "★ Catherby fishing", {"fish_method": "cage", "auto_fish": False}),
    ],
    "mining": [
        (1, "★ Varrock east mine", {"ores": ["copper", "tin"], "auto_ores": False}),
        (15, "★ Varrock east mine", {"ores": ["iron"], "auto_ores": False}),
        (30, "★ Barbarian mine", {"ores": ["coal"], "auto_ores": False}),
    ],
    "combat": [
        (1, "★ Lumbridge chickens", {"targets": ["chicken"]}),
        (5, "★ Lumbridge cows", {"targets": ["cow"]}),
        (10, "★ Lumbridge goblins", {"targets": ["goblin"]}),
        (20, "★ Al Kharid warriors", {"targets": ["al-kharid_warrior", "al_kharid_warrior"]}),
    ],
    "ranged": [
        (1, "★ Lumbridge chickens", {"targets": ["chicken"]}),
        (5, "★ Lumbridge cows", {"targets": ["cow"]}),
        (10, "★ Lumbridge goblins", {"targets": ["goblin"]}),
        (20, "★ Al Kharid warriors", {"targets": ["al-kharid_warrior", "al_kharid_warrior"]}),
    ],
    "smithing": [
        (1, "★ Varrock anvil", {}),
    ],
    "magic": [                         # wind strike on chickens, then alchemy / teleports anywhere
        (1, "★ Lumbridge chickens", {"targets": ["chicken"]}),
        (21, None, {}),
    ],
    "thieving": [
        (1, "★ Lumbridge men", {"thieve": ["man", "woman"]}),
        (25, "★ Al Kharid warriors", {"thieve": ["al-kharid_warrior", "al_kharid_warrior"]}),
        (40, "★ Varrock guards", {"thieve": ["guard"]}),
        (55, "★ Ardougne knights", {"thieve": ["knight_of_ardougne"]}),
    ],
}
AUTO = "auto"           # a plan step's place: pick it from the route by level


def has_route(task):
    return task in ROUTES


def pick(task, level):
    """(place name, overrides) for `level` (None = unknown -> the first tier), or (None, {})."""
    tiers = ROUTES.get(task)
    if not tiers:
        return None, {}
    fit = [t for t in tiers if level is not None and t[0] <= level] or tiers[:1]
    _, place, opts = fit[-1]
    return place, dict(opts)


# ---- checking the built-in places in the game -----------------------------------------------
NEAR = 15               # what a bot searches around its spot
FAR = 40                # how far a check looks for the targets when they aren't near
SETTLE_S = 3.0          # after a teleport, give the scene time to load


def find_targets(gs, task, opts, radius):
    """World tiles of what `task` with `opts` works on within `radius` tiles (game data)."""
    from lumberjack.core.backpack import key
    if task == "woodcutting":
        from lumberjack.skills.woodcutting import TREE_BY_NAME
        want = set(opts.get("trees") or [])
        return [l["tile"] for l in gs.locs(radius)
                if TREE_BY_NAME.get(l["name"].lower()) in want and "Chop down" in l["ops"]]
    if task == "mining":
        from lumberjack.skills.mining import load_rock_ores
        known = load_rock_ores()
        want = set(opts.get("ores") or [])
        rocks = [l for l in gs.locs(radius) if "Mine" in l["ops"]]
        sure = [l["tile"] for l in rocks if known.get(l["id"]) in want]
        # rocks whose ore isn't learned yet count too - mining learns them on the first tries
        return sure or [l["tile"] for l in rocks if l["id"] not in known]
    if task == "fishing":
        from lumberjack.skills.fishing import METHOD_VERB
        verb = METHOD_VERB.get(opts.get("fish_method"), "Net")
        return [n["tile"] for n in gs.npcs("Fishing spot") if verb in n["ops"] and n["dist"] <= radius]
    if task == "smithing":
        return [l["tile"] for l in gs.locs(radius, "anvil") if l["name"] == "Anvil"]
    if task in ("combat", "ranged", "thieving"):
        want, op = (set(opts.get("targets") or []), "Attack") if task != "thieving" else \
            (set(opts.get("thieve") or []), "Pickpocket")
        return [n["tile"] for n in gs.npcs() if key(n["name"]) in want and op in n["ops"]
                and n["dist"] <= radius]
    return []


def places_to_check():
    """{place: [(task, min level, overrides)]} for every route."""
    out = {}
    for task, tiers in ROUTES.items():
        for lv, place, opts in tiers:
            if place:
                out.setdefault(place, []).append((task if task != "magic" else "combat", lv, opts))
    return out


def check_place(ctx, gs, name, tiers, fix=True, log=None):
    """Teleport to place `name` and count each tier's targets around it. When the place has none
    near but some within FAR tiles, walk up to them and save the place there (same name, so it
    replaces the built-in one). Returns {'place', 'found': {label: n}, 'status': 'ok' | 'moved' |
    'missing' | 'unreachable', 'tile'}."""
    import logging
    from lumberjack.nav import places
    log = log or logging.getLogger("places")
    place = places.load()[name]
    res = {"place": name, "found": {}, "status": "ok", "tile": place["tile"]}
    if not places.travel(ctx, gs, place, use_tele=True):
        res["status"] = "unreachable"
        log.warning("✗ %s: couldn't get there", name)
        return res
    ctx.sleep(SETTLE_S)
    label = lambda task, lv, o: f"{task} {lv}+: " + "/".join(o.get("trees") or o.get("ores") or o.get("targets")
                                                            or o.get("thieve") or [o.get("fish_method", "anvil")])
    near = {label(*t): len(find_targets(gs, t[0], t[2], NEAR)) for t in tiers}
    res["found"] = near
    if all(near.values()):
        log.info("✓ %s: %s", name, ", ".join(f"{k} x{n}" for k, n in near.items()))
        return res
    # move toward the highest tier that's missing here
    task, lv, opts = [t for t in tiers if not near[label(*t)]][-1]
    far = find_targets(gs, task, opts, FAR)
    if not far:
        res["status"] = "missing"
        log.warning("✗ %s: no %s within %d tiles - save this place yourself", name, label(task, lv, opts), FAR)
        return res
    if not fix:
        res["status"] = "missing"
        return res
    from lumberjack.core import interact
    me = gs.player()["tile"]
    target = min(far, key=lambda t: max(abs(t[0] - me[0]), abs(t[1] - me[1])))
    log.info("%s: the nearest %s is at %s - moving the place there", name, label(task, lv, opts), target)
    interact.walk_to_tile(ctx, gs, target, arrive=2, max_clicks=30)
    if not find_targets(gs, task, opts, NEAR):
        res["status"] = "missing"
        log.warning("✗ %s: still nothing near after walking - save this place yourself", name)
        return res
    saved = places.save_here(name, gs, note=place.get("note", ""))
    res.update(status="moved", tile=saved["tile"])
    log.info("✓ %s: moved to %s", name, saved["tile"])
    return res


def check_all(ctx, gs, fix=True, log=None, only=None):
    """check_place for every route place (or just `only`). Returns the results."""
    return [check_place(ctx, gs, name, tiers, fix=fix, log=log)
            for name, tiers in places_to_check().items() if only is None or name == only]
