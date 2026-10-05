"""Cooking on a fire: use a raw fish on the fire -> cook box -> "Cook All" -> wait.

    click a raw fish ("Use")  ->  hover flame blobs until it reads "Use Raw shrimps -> Fire"
    -> click  ->  the chatbox shows the cook box (the fish's picture)
    -> right-click the picture -> "Cook All" (the row with the widest white text:
       "Cook All" beats "Cook 1" / "Cook 5" / "Cook X")
    -> each raw fish turns into a cooked or burnt one in the same slot; we're done when
       every raw slot changed, or nothing changed for a few seconds (fire went out, or a
       level-up dialog interrupted us - the caller simply goes round again)

Backpack items are told apart by their hover text:
    raw    "Use Raw shrimps"     orange name template raw_shrimps, raw_anchovies, ...
    cooked "Eat Shrimps"         white action template "eat" (any Eat item counts as cooked)
    burnt  "Use Burnt fish"      anything else read as "Use <item>" that isn't a log

Templates (captured in game with the helpers at the bottom, from the panel's Fishing card):
    mouseover/fire.png          cyan "Fire" (hovered with a raw fish selected)
    mouseover/eat.png           white "Eat"
    mouseover/raw_shrimps.png   orange item names (raw_anchovies, shrimps, burnt_shrimp, ...)
The cook box itself needs no template: it's a parchment dialog with an item picture and
no "Click here to continue".
"""
import logging
import random
import time
import cv2
import numpy as np

from lumberjack import actions
from lumberjack.core import regions as R
from lumberjack.ui import inventory, menu, mouseover
from lumberjack.vision import fire as F

log = logging.getLogger("cooking")

# raw item template -> (cooked template, burnt template, Cooking level)
FISH = {
    "raw_shrimps": ("shrimps", "burnt_shrimp", 1),
    "raw_anchovies": ("anchovies", "burnt_fish", 1),
    "raw_sardine": ("sardine", "burnt_fish", 1),
    "raw_herring": ("herring", "burnt_fish", 5),
    "raw_trout": ("trout", "burnt_fish", 15),
    "raw_pike": ("pike", "burnt_fish", 20),
    "raw_salmon": ("salmon", "burnt_fish", 25),
    "raw_tuna": ("tuna", "burnt_fish", 30),
    "raw_lobster": ("lobster", "burnt_lobster", 40),
    "raw_swordfish": ("swordfish", "burnt_swordfish", 45),
}
RAW = list(FISH)
GENERIC_RAW = "raw_fish"   # samples of catches the bot had no name for (learned while fishing)
COOKED = sorted({c for c, _, _ in FISH.values()})
BURNT = sorted({b for _, b, _ in FISH.values()})
# the names the panel offers to learn (net fishing at Draynor first)
LEARNABLE = ["raw_shrimps", "shrimps", "burnt_shrimp", "raw_anchovies", "anchovies", "burnt_fish",
             "raw_sardine", "sardine", "raw_herring", "herring", "raw_trout", "trout",
             "raw_salmon", "salmon"]
LOGS = actions.LOG_ITEMS

BEIGE = np.array([156, 193, 212])          # dialog parchment (BGR), as in actions.dismiss_dialog
FIRE_OFFSETS = [(0, 0), (0, -6), (0, 6), (-6, 0), (6, 0)]
FIRE_RADIUS = 6                           # tiles searched for a fire to cook on (game data)
BOX_WAIT_S = 3.5
STILL_TICKS = 8                           # x 0.8 s with no slot changing -> cooking stopped
CHANGED = 2.0                             # mean slot diff; a static slot measures ~0


# ---- pure helpers (unit tested) ---------------------------------------------------------
def kind_of(action, name):
    """'raw' | 'cooked' | 'burnt' | 'log' | None from a hover read (action word, item name)."""
    if action == "eat":
        return "cooked"
    if action != "use":
        return None
    if name in FISH or name == GENERIC_RAW:
        return "raw"
    if name in LOGS:
        return "log"
    return "burnt"     # some other "Use <item>": burnt fish (tools are recognised before this)


def missing_templates(cooked_action="drop", available=mouseover.available, names_known=False):
    """What still needs learning before cooking can run (empty = ready). names_known: the
    backpack is read from the game, so item names needn't be learned."""
    out = []
    if not names_known and not available("fire"):       # with game data fires are found by name
        out.append("the fire (Fishing > Learn fire)")
    if not names_known and not any(available(r) for r in RAW + [GENERIC_RAW]):
        out.append("a raw fish name (Fishing > Learn item, e.g. raw_shrimps)")
    return out   # 'Eat' isn't needed up front: learn_eat() picks it up from the first cooked fish


def sprite_mask(chat):
    """Pixels of the item picture in a chatbox crop: not parchment, not dark/red/blue/white text."""
    c = chat.astype(np.int16)
    b, g, r = c[..., 0], c[..., 1], c[..., 2]
    far = np.abs(c - BEIGE).max(axis=2) > 45
    bright = c.max(axis=2) > 90
    red_text = (r > 110) & (g < 60) & (b < 60)
    blue_text = (b > 180) & (g < 80) & (r < 80)
    white_text = (c >= 250).all(axis=2)
    return (far & bright & ~red_text & ~blue_text & ~white_text).astype(np.uint8)


# the item picture sits between the dialog's title line and the item name under it; the
# chatbox border and the (dark red) title must not count as picture
SPRITE_BAND = (36, 90)        # chatbox-relative rows searched for the picture
SPRITE_MARGIN_X = 14          # ... and columns kept clear of the border


def sprite_center(frame):
    """Canvas centre of the item picture in the chatbox, or None. Picture pieces (e.g. the
    three shrimps of one icon) are merged before picking the biggest blob."""
    m = sprite_mask(R.CHATBOX.crop(frame))
    y0, y1 = SPRITE_BAND
    band = np.zeros_like(m)
    band[y0:y1, SPRITE_MARGIN_X:-SPRITE_MARGIN_X] = m[y0:y1, SPRITE_MARGIN_X:-SPRITE_MARGIN_X]
    band = cv2.dilate(band, np.ones((7, 7), np.uint8))
    n, _, stats, cents = cv2.connectedComponentsWithStats(band)
    best = None
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area >= 120 and w >= 12 and h >= 12 and (best is None or area > best[0]):
            best = (area, cents[i])
    if best is None:
        return None
    cx, cy = best[1]
    return int(cx) + R.CHATBOX.x, int(cy) + R.CHATBOX.y


def parchment(frame):
    """Share of the chatbox that is dialog parchment."""
    chat = R.CHATBOX.crop(frame).astype(np.int16)
    return float((np.abs(chat - BEIGE).max(axis=2) < 25).mean())


def _scrollbar(frame):
    """Plain chat has a dark scrollbar track on the right; dialogs don't."""
    return float((frame[350:440, 497:511].max(axis=2) < 70).mean()) > 0.15


def cook_box_open(frame):
    """The cook box (or the shared make box) is showing in the chatbox: a parchment dialog
    without a scrollbar, with an item picture and without the blue "Click here to
    continue" of a message/level-up dialog."""
    from lumberjack.skills.fletching import make_box_open
    if make_box_open(frame):
        return True
    if parchment(frame) < 0.45 or _scrollbar(frame):
        return False
    x0, y0, w, h = actions.CONTINUE_BAND
    band = frame[y0:y0 + h, x0:x0 + w].astype(np.int16)
    # same test as actions.dismiss_dialog, so the two never claim the same dialog
    if actions.continue_blue(band).sum() >= actions.CONTINUE_MIN_PX:
        return False
    if (band >= 250).all(axis=2).sum() >= actions.CONTINUE_MIN_PX:
        return False
    return sprite_center(frame) is not None


def white_span(frame, row):
    """Width of the white text in a menu row (first to last white column)."""
    cols = np.where(menu._white(frame, row).any(axis=0))[0]
    return int(cols[-1] - cols[0] + 1) if len(cols) else 0


def cook_all_row(frame, rows):
    """Index of the "Cook All" row: the widest white text, ignoring Cancel/Examine."""
    best = None
    for i, r in enumerate(rows):
        if any((menu.TEMPLATES / f"{t}.png").exists() and menu.row_has_action(frame, r, t)
               for t in ("cancel", "examine")):
            continue
        w = white_span(frame, r)
        if w and (best is None or w > best[0]):
            best = (w, i)
    return best[1] if best else None


def changed_slots(before, frame, slots):
    """Slots whose picture changed since `before` ({slot: crop})."""
    out = []
    for i in slots:
        now = R.INV_SLOTS[i].crop(frame).astype(np.int16)
        if float(np.abs(now - before[i]).mean()) > CHANGED:
            out.append(i)
    return out


# ---- reading the backpack ---------------------------------------------------------------
def read_hover(strip):
    """(action, name) of an inventory hover strip: ('use', 'raw_shrimps'), ('eat', None)..."""
    if mouseover.available("eat") and mouseover.action_ok(strip, "eat"):
        return "eat", None
    if not mouseover.action_ok(strip, "use"):
        return None, None
    for n in RAW + [GENERIC_RAW] + LOGS + BURNT:
        if mouseover.available(n) and mouseover._target_ok(strip, n, "orange"):
            return "use", n
    return "use", None


def sort_backpack(ctx, keep=()):
    """{'raw': [...], 'cooked': [...], 'burnt': [...], 'log': [...], 'other': [...]} of the
    backpack slots not in `keep`, read by hovering each one (a few reads if unsure)."""
    out = {"raw": [], "cooked": [], "burnt": [], "log": [], "other": []}
    from lumberjack.core import backpack
    inv = backpack.slots()
    if inv is not None:
        for i, s in enumerate(inv):
            if s["id"] < 0 or i in keep:
                continue
            kd = backpack.kind(s["key"])
            out[kd if kd in out else "other"].append(i)
        return out
    frame = ctx.grab()
    for i, occ in enumerate(inventory.occupied(frame)):
        if not occ or i in keep or actions.tool_icon(frame, i):
            continue
        out[slot_kind(ctx, i) or "other"].append(i)   # re-reads "burnt" (the fallback) to be sure
    ctx.inp.move(260, 300)
    return out


def slot_kind(ctx, i):
    """What one backpack slot holds: 'raw' | 'cooked' | 'burnt' | 'log' | None - from the
    game's own data when readable, else by hovering the slot."""
    from lumberjack.core import backpack
    inv = backpack.slots()
    if inv is not None:
        kd = backpack.kind(inv[i]["key"])
        return kd if kd in ("raw", "cooked", "burnt", "log") else None
    ctx.inp.move(*R.INV_SLOTS[i].center, steps=2)
    kind = None
    for wait in (0.12, 0.25, 0.35):
        ctx.sleep(wait)
        kind = kind_of(*read_hover(R.MOUSEOVER_TEXT.crop(ctx.grab())))
        if kind and kind != "burnt":
            break
    return kind


def still_raw(ctx, slots):
    """The slots in `slots` that still hold a raw fish, re-read by hovering (the pixel diff
    that tracks cooking can miss a slot, and a stale slot list sends the wrong item to the fire)."""
    actions.open_tab(ctx, "inventory")
    occ = inventory.occupied(ctx.grab())
    out = [i for i in slots if occ[i] and slot_kind(ctx, i) == "raw"]
    ctx.inp.move(260, 300)
    return out


# ---- the fire ---------------------------------------------------------------------------
def hover_is_fire(frame):
    return mouseover.available("fire") and mouseover.which(frame, "use", ["fire"], "cyan") == "fire"


def _deselect(ctx):
    """Drop the 'Use' selection (another side tab and back - the open tab alone doesn't)."""
    actions.cancel_selection(ctx, force=True)


def use_on_fire_gs(ctx, gs, slot):
    """Fires from the game's scene data: select the fish, hover a fire, click when the menu's
    top entry is 'Use <fish> -> Fire'. None if there's no fire nearby (nothing selected)."""
    from lumberjack.core import interact
    from lumberjack.core.gamestate import top_entry
    fires = [l for l in gs.locs(FIRE_RADIUS, "fire") if l["name"] == "Fire" and interact.on_screen(*l["screen"])]
    if not fires:
        return None
    x, y = R.INV_SLOTS[slot].center
    ctx.inp.click(x + random.randint(-4, 4), y + random.randint(-4, 4))
    ctx.sleep(random.uniform(0.25, 0.35))
    for f in fires[:3]:
        for px, py in interact.points_for(f):
            if not interact.on_screen(px, py):
                continue
            ctx.inp.move(px + random.randint(-2, 2), py + random.randint(-2, 2))
            ctx.sleep(random.uniform(0.1, 0.16))
            top = top_entry(gs.menu())
            if top and top["verb"] == "Use" and top["subject"].lower().endswith("fire"):
                ctx.inp.click()
                log.info("Using slot %d on a fire %d tile(s) away", slot + 1, f["dist"])
                return True
    _deselect(ctx)
    return False


def use_on_fire(ctx, slot):
    """Select the item in `slot` and click a verified fire. True if we clicked one."""
    from lumberjack.core import gamestate
    gs = gamestate.shared()
    if gs is not None:
        try:
            r = use_on_fire_gs(ctx, gs, slot)
            return bool(r)
        except gamestate.GameStateError:
            pass
    x, y = R.INV_SLOTS[slot].center
    ctx.inp.click(x + random.randint(-4, 4), y + random.randint(-4, 4))
    ctx.sleep(random.uniform(0.25, 0.35))
    ctx.inp.move(260, 120)                       # off the backpack, so the scene shows
    f1 = ctx.grab()
    ctx.sleep(0.3)
    f2 = ctx.grab()
    for c in F.find_fires(f2, f1)[:5]:
        for dx, dy in FIRE_OFFSETS:
            px, py = c.x + dx, c.y + dy
            if not R.VIEWPORT.contains(px, py) or R.MOUSEOVER_TEXT.contains(px, py):
                continue
            ctx.inp.move(px, py)
            ctx.sleep(random.uniform(0.12, 0.2))
            if hover_is_fire(ctx.grab()):
                ctx.inp.click()
                log.info("Using slot %d on the fire at (%d, %d)", slot + 1, px, py)
                return True
    _deselect(ctx)
    return False


def choose_cook_all(ctx):
    """With the cook box open: right-click the picture and pick 'Cook All'."""
    from lumberjack.core import gamestate
    from lumberjack.ui import widgets
    gs = gamestate.shared()
    if gs is not None and widgets.make(ctx, gs):          # the box's options read from the game
        return True
    frame = ctx.grab()
    xy = sprite_center(frame)
    if not xy:
        log.warning("Cook box open but no item picture found")
        return False
    x, y = xy
    ctx.inp.right_click(x, y)
    ctx.sleep(0.35)
    frame = ctx.grab()
    m = menu.find(frame, (x, y))
    if not m:
        ctx.inp.click(x, y)                      # no menu - a left-click cooks at least one
        return True
    rows = menu.rows(m)
    i = cook_all_row(frame, rows)
    if i is None:
        ctx.inp.move(x, max(R.VIEWPORT.y + 30, y - 120))
        return False
    rx, ry = rows[i].center
    ctx.inp.click(rx + random.randint(-10, 10), ry + random.randint(-2, 2))
    return True


def _box_in_game():
    from lumberjack.core import gamestate
    from lumberjack.ui import widgets
    gs = gamestate.shared()
    return bool(gs is not None and widgets.make_box(gs))


def cook(ctx, raw_slots):
    """Cook the fish in `raw_slots` on a fire in view.

    Returns (status, done): status 'no_fire' (nothing to use them on), 'no_box' (the cook
    box never showed and nothing cooked) or 'ok'; done = slots that turned cooked/burnt."""
    if not raw_slots:
        return "ok", []
    first = next((i for i in raw_slots if slot_kind(ctx, i) == "raw"), None)
    if first is None:
        log.info("None of slots %s holds a raw fish any more", ", ".join(str(i + 1) for i in raw_slots))
        return "ok", list(raw_slots)
    before = {i: R.INV_SLOTS[i].crop(ctx.grab()).astype(np.int16) for i in raw_slots}
    if not use_on_fire(ctx, first):
        return "no_fire", []
    end = time.monotonic() + BOX_WAIT_S
    box = False
    while time.monotonic() < end:
        ctx.sleep(0.25)
        frame = ctx.grab()
        if cook_box_open(frame) or _box_in_game():
            box = True
            break
        if changed_slots(before, frame, raw_slots):
            break                                # cooked straight away (e.g. the last fish)
    if box and not choose_cook_all(ctx):
        return "no_box", []
    ctx.inp.move(260, 300)
    done, still = [], 0
    while still < STILL_TICKS and len(done) < len(raw_slots):
        ctx.sleep(0.8)
        if actions.dismiss_dialog(ctx):          # a level-up stops cooking
            break
        now = changed_slots(before, ctx.grab(), raw_slots)
        if len(now) > len(done):
            done, still = now, 0
        else:
            still += 1
    if not box and not done:
        return "no_box", []
    return "ok", done


# ---- calibration helpers (run from the control panel's manual runner) -------------------
def learn_item(ctx, slot, name):
    """Hover backpack `slot` and save its orange name as `name` (and 'Eat' if unknown).
    Cooked fish read "Eat Shrimps"; raw and burnt fish "Use Raw shrimps"."""
    from lumberjack.tools.calibrate_name import save_name
    action = "eat" if name in COOKED else "use"
    ctx.inp.move(*R.INV_SLOTS[slot].center, steps=2)
    ctx.sleep(0.35)
    strip = R.MOUSEOVER_TEXT.crop(ctx.grab())
    ctx.inp.move(260, 300)
    if not mouseover.strip_mask(strip, "orange").any():
        log.warning("Slot %d shows no item name - is something there?", slot + 1)
        return False
    if not mouseover.available(action):
        mouseover.save_word_templates(None, action, f"_tmp_{name}", "orange", strip=strip)
        _discard(f"_tmp_{name}")
        log.info("Learned the '%s' action", action.capitalize())
    elif not mouseover.action_ok(strip, action):
        log.warning("Slot %d doesn't read '%s ...' - is that %s?", slot + 1, action.capitalize(),
                    name.replace("_", " "))
        return False
    others = [n for n in RAW + COOKED + BURNT + LOGS + actions.TOOLS
              if n != name and mouseover.available(n) and mouseover._target_ok(strip, n, "orange")]
    if others or actions.tool_icon(ctx.grab(), slot):
        log.warning("Slot %d holds %s, not %s - pick the slot the %s is in", slot + 1,
                    (others or ["a tool"])[0].replace("_", " "), name.replace("_", " "), name.replace("_", " "))
        return False
    if mouseover.available(name):
        try:
            mouseover.add_variant(strip, name, "orange")   # another sample of a known name
        except ValueError as e:
            log.warning("Not learning slot %d as %s: %s", slot + 1, name.replace("_", " "), e)
            return False
    else:
        save_name(strip, name, "orange")
    log.info("Learned '%s' from slot %d", name, slot + 1)
    return True


def learn_eat(ctx, slots):
    """Learn the 'Eat' action from a cooked fish: hover `slots` (fish that were raw before
    cooking) for one whose hover isn't "Use ..." (burnt fish are "Use Burnt shrimp") but
    "<white verb> <orange name>" - that verb is Eat. True once 'eat' is known."""
    if mouseover.available("eat"):
        return True
    if not mouseover.available("use"):
        return False
    actions.open_tab(ctx, "inventory")
    occ = inventory.occupied(ctx.grab())
    for i in slots:
        if not occ[i]:
            continue
        ctx.inp.move(*R.INV_SLOTS[i].center, steps=2)
        ctx.sleep(0.3)
        strip = R.MOUSEOVER_TEXT.crop(ctx.grab())
        if not mouseover.strip_mask(strip, "orange").any() or mouseover.action_ok(strip, "use"):
            continue
        try:
            mouseover.save_word_templates(None, "eat", "_tmp_eat", "orange", strip=strip)
        except ValueError:
            continue
        _discard("_tmp_eat")
        log.info("Learned 'Eat' from the cooked fish in slot %d", i + 1)
        ctx.inp.move(260, 300)
        return True
    ctx.inp.move(260, 300)
    return False


def learn_fire(ctx):
    """Stand next to a lit fire with a raw fish in the backpack: select the fish, hover the
    flames and save the cyan name as mouseover/fire.png."""
    from lumberjack.tools.calibrate_name import KNOWN, save_name
    inv = sort_backpack(ctx)
    pick = (inv["raw"] or inv["burnt"] or inv["other"] or [None])[0]
    if pick is None:
        log.warning("Put a raw fish (or any 'Use' item) in the backpack first")
        return False
    x, y = R.INV_SLOTS[pick].center
    ctx.inp.click(x, y)
    ctx.sleep(0.3)
    ctx.inp.move(260, 120)
    ctx.sleep(0.2)
    for c in F.find_fires(ctx.grab())[:6]:
        for dx, dy in FIRE_OFFSETS:
            ctx.inp.move(c.x + dx, c.y + dy)
            ctx.sleep(0.25)
            strip = R.MOUSEOVER_TEXT.crop(ctx.grab())
            if not mouseover.action_ok(strip, "use") or not mouseover.strip_mask(strip, "cyan").any():
                continue
            if any(mouseover.available(k) and mouseover._target_ok(strip, k, "cyan") for k in KNOWN):
                continue                         # that's a tree
            save_name(strip, "fire", "cyan")
            log.info("Learned 'fire' from the hover at (%d, %d)", c.x + dx, c.y + dy)
            _deselect(ctx)
            return True
    _deselect(ctx)
    log.warning("No fire found next to you - light one (Manual > Burn logs) and try again")
    return False


def test_cook(ctx, keep=()):
    """Cook every raw fish in the backpack on a fire in view and log what happened."""
    inv = sort_backpack(ctx, keep)
    if not inv["raw"]:
        log.warning("No raw fish recognised in the backpack (learn their names first)")
        return 0
    status, done = cook(ctx, inv["raw"])
    if status == "no_fire":
        log.warning("Couldn't find a fire to use - is one lit right next to you? (Learn fire first)")
    elif status == "no_box":
        log.warning("Used the fish on the fire, but no cook box appeared and nothing cooked")
    else:
        log.info("Cooked %d of %d raw fish", len(done), len(inv["raw"]))
    return len(done)


def _discard(name):
    p = mouseover.TEMPLATES / f"{name}.png"
    if p.exists():
        p.unlink()
    mouseover._cache.pop(name, None)
    mouseover._variants_cache.pop(name, None)
