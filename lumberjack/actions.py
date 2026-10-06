"""Reusable in-game actions, shared by the bots and the control panel's manual buttons.

Every function takes a `Ctx` (window + input + an interruptible sleep).
"""
import logging
import random
import time
from dataclasses import dataclass, field

import numpy as np

from lumberjack.core import regions as R
from lumberjack.core.input import VK_LEFT, VK_SHIFT, VK_UP
from lumberjack.ui import inventory, menu, mouseover
from lumberjack.vision import minimap as MM

log = logging.getLogger("actions")

SERVER_TICK = 0.6
# side-panel tab buttons (fixed mode, top row)
TABS = {"combat": (541, 187), "stats": (575, 187), "quests": (608, 187), "inventory": (642, 187),
        "equipment": (675, 187), "prayer": (708, 187), "magic": (745, 187)}
HOME_TELEPORT_ICON = (571, 229)


@dataclass
class Ctx:
    win: object
    inp: object
    sleep: callable = field(default=time.sleep)
    grabber: callable = None      # a bot's grab (waits while the game is minimized)

    def grab(self):
        return self.grabber() if self.grabber else self.win.grab()


def open_tab(ctx, name):
    ctx.inp.click(*TABS[name])
    ctx.sleep(0.4)


CAMERA_TOP_DOWN = 383      # the client's highest pitch
CAMERA_SETTLE_S = 2.5      # the camera glides to a new target; wait at most this long


def _camera_gs():
    from lumberjack.core import gamestate
    return gamestate.shared()


def _wait_camera(ctx, gs):
    """Wait until the camera has reached its target (yaw wraps at 2048)."""
    from lumberjack.core.gamestate import GameStateError
    end = time.monotonic() + CAMERA_SETTLE_S
    while time.monotonic() < end:
        try:
            c = gs.camera()
        except GameStateError:
            return
        dyaw = abs(c["yaw"] - c["yaw_target"]) % 2048
        if min(dyaw, 2048 - dyaw) < 12 and abs(c["pitch"] - c["pitch_target"]) < 6:
            return
        ctx.sleep(0.1)


def reset_camera(ctx):
    """The standard camera: facing north (so the minimap is north-up too) and tilted fully
    top-down (what the pixel finders are tuned on). Set directly through the game's camera
    when its data is readable, else by holding the Up arrow."""
    from lumberjack.core.gamestate import GameStateError
    gs = _camera_gs()
    if gs:
        try:
            gs.camera(yaw=0, pitch=CAMERA_TOP_DOWN)
            _wait_camera(ctx, gs)
            return
        except GameStateError:
            pass
    ctx.inp.move(260, 300)
    ctx.inp.hold_key(VK_UP, 2.0)
    ctx.sleep(0.3)


def rotate_camera(ctx, quarters=1):
    """Turn the camera by a quarter turn (90 degrees) per `quarters`, to look around for
    something out of view. Exact through the game's camera, else ~a quarter by arrow key."""
    from lumberjack.core.gamestate import GameStateError
    gs = _camera_gs()
    if gs:
        try:
            c = gs.camera()
            gs.camera(yaw=(c["yaw_target"] + 512 * quarters) % 2048)
            _wait_camera(ctx, gs)
            return
        except GameStateError:
            pass
    ctx.inp.move(260, 300)
    ctx.inp.hold_key(VK_LEFT, random.uniform(0.7, 0.9) * abs(quarters))
    ctx.sleep(0.3)


def home_teleport(ctx, timeout=30):
    """Cast Lumbridge Home Teleport and wait until we've landed. Returns True on success."""
    from lumberjack.core import gamestate
    gs = gamestate.shared()
    if gs is not None:
        try:
            return _home_teleport_gs(ctx, gs, timeout)
        except gamestate.GameStateError:
            pass
    open_tab(ctx, "magic")
    ctx.inp.move(*HOME_TELEPORT_ICON)
    ctx.sleep(0.3)
    if not mouseover.is_text(ctx.grab(), "cast", "home_tp", "green"):
        log.warning("Couldn't find the Home Teleport spell")
        open_tab(ctx, "inventory")
        return False
    ctx.inp.click()
    log.info("Casting Home Teleport...")
    before, _ = MM.patch(ctx.grab())
    start = time.monotonic()
    landed = False
    # the cast takes ~10-15 s; we've landed when the minimap changes completely
    while time.monotonic() - start < timeout:
        ctx.sleep(1.0)
        now, _ = MM.patch(ctx.grab())
        if float(np.abs(now.astype(np.int16) - before.astype(np.int16)).mean()) > 25:
            landed = True
            break
    ctx.sleep(2.0)  # let the arrival animation finish
    open_tab(ctx, "inventory")
    log.info("Teleported" if landed else "Teleport didn't seem to happen (in combat? on cooldown?)")
    return landed


def _hover_spell(ctx, gs, name):
    """Hover the spellbook component named `name` (the interface read); its top menu entry."""
    from lumberjack.core.gamestate import top_entry
    from lumberjack.ui import widgets
    for w in widgets.find(gs, name):
        if w["w"] > 0 and w["h"] > 0:
            ctx.inp.move(*widgets.center(w))
            ctx.sleep(0.3)
            top = top_entry(gs.menu())
            if top and name in (top["subject"] + " " + top["verb"]).lower():
                return top
    return None


def _home_teleport_gs(ctx, gs, timeout):
    """The spell confirmed by the menu text ('Cast ... Home Teleport'); landed = our tile jumped."""
    from lumberjack.core.gamestate import top_entry
    open_tab(ctx, "magic")
    ctx.inp.move(*HOME_TELEPORT_ICON)
    ctx.sleep(0.3)
    top = top_entry(gs.menu())
    if not top or "home teleport" not in (top["subject"] + " " + top["verb"]).lower():
        top = _hover_spell(ctx, gs, "home teleport") or top     # not where we expected: find it by name
    if not top or "home teleport" not in (top["subject"] + " " + top["verb"]).lower():
        log.warning("Couldn't find the Home Teleport spell (menu shows %s)", top and top["verb"] + " " + top["subject"])
        open_tab(ctx, "inventory")
        return False
    start_tile = gs.player()["tile"]
    ctx.inp.click()
    log.info("Casting Home Teleport...")
    end = time.monotonic() + timeout
    landed = False
    while time.monotonic() < end:
        ctx.sleep(1.0)
        if max(abs(a - b) for a, b in zip(gs.player()["tile"], start_tile)) > 10:
            landed = True
            break
    ctx.sleep(2.0)
    open_tab(ctx, "inventory")
    log.info("Teleported" if landed else "Teleport didn't seem to happen (in combat? on cooldown?)")
    return landed


def confirm_product(ctx, i, food=False):
    """Hover slot i and positively identify it as a product (never a tool or wieldable).
    Last line of defence before anything is dropped. food=True also counts "Eat <item>"."""
    from lumberjack.core import backpack
    inv = backpack.slots()
    if inv is not None:                      # the game says what it is - no hovering
        return backpack.is_product(inv[i]["key"], food)
    if tool_icon(ctx.grab(), i):
        return False
    ctx.inp.move(*R.INV_SLOTS[i].center, steps=2)
    return _product_every_read(ctx, food)


_ICONS = None


def tool_icon(frame, i):
    """Name of the known tool whose backpack icon is in slot i, else None. Icons sit on a
    plain background, so this is exact where hover text can be spoiled by the scene."""
    global _ICONS
    if _ICONS is None:
        import cv2
        from pathlib import Path
        d = Path(__file__).resolve().parent / "assets" / "templates" / "items"
        _ICONS = {p.stem: cv2.imread(str(p)).astype(np.int16) for p in d.glob("*.png")}
    crop = R.INV_SLOTS[i].crop(frame).astype(np.int16)
    for name, icon in _ICONS.items():
        if icon.shape == crop.shape and float(np.abs(crop - icon).mean()) < 6:
            return name
    return None


def _product_every_read(ctx, food=False):
    """True only if every read of the current hover says product. A busy background can
    spoil a read either way; when in doubt the item is KEPT - losing a tool is far worse
    than keeping a log."""
    for wait in READS:
        ctx.sleep(wait)
        if not _is_product(R.MOUSEOVER_TEXT.crop(ctx.grab()), food):
            return False
    return True


# Shift-click dropping: None = not tried yet, True = works in this client, False = doesn't.
# It's tried first on a non-food item ("Use ..."), where a shift-click that doesn't drop
# only selects the item; never on food until proven, since a plain click on food eats it.
SHIFT_DROP = None


def _is_food(ctx):
    strip = R.MOUSEOVER_TEXT.crop(ctx.grab())
    return mouseover.available("eat") and mouseover.action_ok(strip, "eat")


def _shift_drop(ctx, i, x, y):
    """Shift-click slot i. True if the item is gone."""
    global SHIFT_DROP
    ctx.inp.key_down(VK_SHIFT)
    try:
        ctx.inp.click(x, y)
    finally:
        ctx.inp.key_up(VK_SHIFT)
    if SHIFT_DROP:
        ctx.sleep(random.uniform(0.08, 0.15))
        return True
    ctx.sleep(SERVER_TICK * 1.5)                 # first try: check the slot really emptied
    if not inventory.occupied(ctx.grab())[i]:
        SHIFT_DROP = True
        log.info("Shift-click drops items - using it from now on")
        return True
    SHIFT_DROP = False
    log.info("Shift-click doesn't drop in this client - using the right-click menu")
    cancel_selection(ctx, force=True)            # the click selected the item ("Use"): cancel that
    return False


def cancel_selection(ctx, force=False):
    """Undo a selected item ("Use Burnt shrimp -> ..."): switching to another side tab and back
    cancels it (clicking the already-open backpack tab doesn't). With game data only when the
    game says something is selected; force=True does it regardless. True if it cancelled."""
    from lumberjack.core import gamestate
    gs = gamestate.shared()
    if not force:
        if gs is None:
            return False              # can't tell without game data - only cancel when told to
        try:
            if not gs.menu().get("targeting"):
                return False
        except gamestate.GameStateError:
            return False
    log.info("Cancelling a selected item")
    open_tab(ctx, "stats")
    open_tab(ctx, "inventory")
    return True


def drop_slot(ctx, i, food=False):
    if not confirm_product(ctx, i, food):
        log.warning("Not dropping slot %d - it isn't a product (tool, wieldable or unreadable)", i + 1)
        return False
    x, y = R.INV_SLOTS[i].center
    x += random.randint(-4, 4)
    y += random.randint(-4, 4)
    from lumberjack.core import gamestate
    gs = gamestate.shared()
    # the shift-click experiment can leave an item selected ("Use ..."): only without game data,
    # and once proven it drops; with game data the menu route below is exact anyway
    if gs is None and (SHIFT_DROP or (SHIFT_DROP is None and not _is_food(ctx))):
        if _shift_drop(ctx, i, x, y):
            return True
    cancel_selection(ctx)
    ctx.inp.right_click(x, y)
    ctx.sleep(random.uniform(0.15, 0.25))
    if gs is not None:                       # pick "Drop" by the menu's text and row
        from lumberjack.core.gamestate import GameStateError, menu_row_point
        try:
            m = gs.menu()
            e = next((e for e in m.get("entries") or [] if e["verb"] == "Drop"), None)
            if m.get("open") and e:
                rx, ry = menu_row_point(m, e["row"])
                ctx.inp.click(rx + random.randint(-15, 15), ry + random.randint(-1, 1))
                ctx.sleep(random.uniform(0.08, 0.15))
                return True
            ctx.inp.move(x - 120, y)
            return False
        except GameStateError:
            pass
    frame = ctx.grab()
    m = menu.find(frame, (x, y))
    opt = menu.find_option(frame, m, "drop") if m else None
    if not opt:
        ctx.inp.move(x - 120, y)  # leave the menu (it closes when the mouse leaves)
        return False
    _, row = opt
    rx, ry = row.center
    ctx.inp.click(rx + random.randint(-15, 15), ry + random.randint(-2, 2))
    ctx.sleep(random.uniform(0.08, 0.15))
    return True


LOG_ITEMS = ["logs", "oak_logs", "willow_logs", "maple_logs", "yew_logs", "magic_logs"]

CONTINUE_BAND = (10, 446, 500, 28)       # where "Click here to continue" appears


CONTINUE_MIN_PX = 60                     # "Click here to continue" is ~20 letters of text


def continue_blue(band):
    """Pixels of blue "Click here to continue" text in a (BGR, int16) chatbox band. Relative,
    not absolute: level-up dialogs draw it a softer blue-purple than pure 0000FF, and the
    parchment around it is never bluer than it is red."""
    b, g, r = band[..., 0], band[..., 1], band[..., 2]
    return (b > 100) & (b - np.maximum(r, g) > 50)


def dismiss_dialog(ctx):
    """Close a level-up (or other 'Click here to continue') dialog. True if one was open.
    These block actions - fires won't light while one is showing."""
    from lumberjack.core import gamestate
    from lumberjack.ui import widgets
    gs = gamestate.shared()
    if gs is not None and widgets.continue_dialog(ctx, gs):     # read from the game's interfaces
        log.info("Closed a dialog (level up?)")
        return True
    # "Click here to continue" is blue text near the bottom of the chatbox dialog; its x
    # position varies with the dialog, so look for a run of pure-blue text in that band.
    frame = ctx.grab()
    x0, y0, w, h = CONTINUE_BAND
    band = frame[y0:y0 + h, x0:x0 + w].astype(np.int16)
    # dialogs have a parchment-beige background (BGR ~156,193,212); plain chat doesn't
    beige = (np.abs(band - np.array([156, 193, 212])).max(axis=2) < 25).mean()
    if beige < 0.5:
        return False
    # ...and no chat scrollbar: plain chat has one on the right edge, dialogs don't
    bar = frame[350:440, 497:511]
    if (bar.max(axis=2) < 70).mean() > 0.15:      # the scrollbar track is dark
        return False
    from lumberjack.skills.fletching import make_box_open
    if make_box_open(frame):                       # "What would you like to make?" isn't a dialog to close
        return False
    blue = continue_blue(band)
    white = (band >= 250).all(axis=2)         # message dialogs draw it in white
    text = blue if blue.sum() >= CONTINUE_MIN_PX else white
    if text.sum() < CONTINUE_MIN_PX:
        return False
    ys, xs = np.where(text)
    log.info("Closing a dialog (level up?)")
    ctx.inp.click(x0 + int(xs.mean()), y0 + int(ys.mean()))
    ctx.sleep(0.6)
    return True


# items with a plain "Use" hover that are tools, never products (names need a template;
# anything not readable as "Use <item>" - e.g. "Wield Rune axe" - is a keeper anyway)
TOOLS = ["tinderbox", "knife", "small_fishing_net", "fishing_rod", "fly_fishing_rod", "harpoon",
         "lobster_pot", "fishing_bait", "feather", "hammer", "chisel"]
READS = (0.15, 0.25, 0.35)   # a product must be read as one on ALL of these


def _is_product(strip, food=False):
    """A product is "Use <item>" where the item isn't a known tool: logs, unstrung bows,
    arrow shafts, raw/burnt fish... With food=True, "Eat <item>" (cooked fish) counts too.
    Wieldables ("Wield Rune axe"), tools and unreadable hovers are not."""
    if food and mouseover.available("eat") and mouseover.action_ok(strip, "eat"):
        return bool(mouseover.strip_mask(strip, "orange").any())
    if not mouseover.action_ok(strip, "use"):
        return False
    if not mouseover.strip_mask(strip, "orange").any():
        return False
    return not any(mouseover.available(t) and mouseover._target_ok(strip, t, "orange") for t in TOOLS)


def carried_keepers(ctx):
    """Backpack slots worth keeping (tools). Hovers each item and reads its hover text.
    Logs and other products are left for the full-inventory action (burn/fletch/bank/drop)."""
    from lumberjack.core import backpack
    inv = backpack.slots()
    if inv is not None:
        return {i for i, s in enumerate(inv) if s["id"] >= 0 and not backpack.is_product(s["key"])}
    open_tab(ctx, "inventory")
    keep = set()
    frame = ctx.grab()
    for i, occ in enumerate(inventory.occupied(frame)):
        if not occ:
            continue
        if tool_icon(frame, i):          # a known tool by its picture - no hover needed
            keep.add(i)
            continue
        ctx.inp.move(*R.INV_SLOTS[i].center, steps=2)
        if not _product_every_read(ctx):
            keep.add(i)
    ctx.inp.move(260, 300)
    return keep


def clear_materials(ctx, food=True, log=None):
    """Drop every material in the backpack - logs, ore, fish (cooked too with food=True), loot -
    so a run starts with room. Tools and anything not known as a product stay: each slot is
    checked before it's dropped (by name with game data, else by its hover text). Returns how
    many slots were dropped, or None when that couldn't be counted (no game data)."""
    from lumberjack.core import backpack, gamestate
    inv = backpack.slots()
    if inv is not None:
        mats = [i for i, s in enumerate(inv) if s["id"] >= 0 and backpack.is_product(s["key"], food=food)]
        n = len(mats)
        if not n:
            return 0
        if log:
            log.info("Dropping %d material(s) before starting", n)
        gs = gamestate.shared()
        if gs is not None:            # known by name: drop exactly those (the hover check was for
            drop_known(ctx, gs, mats)  # no game data, and kept every slot it couldn't read)
            return n
    elif log:
        log.info("Dropping materials before starting")
    drop_all(ctx, keep=(), food=food)
    return n if inv is not None else None


def use_slot(ctx, gs, i, verb):
    """Hover backpack slot i and left-click it if its top menu entry is `verb` ("Bury", "Eat",
    "Use"...), read from the game's data. True if clicked."""
    from lumberjack.core.gamestate import top_entry
    x, y = R.INV_SLOTS[i].center
    ctx.inp.move(x + random.randint(-4, 4), y + random.randint(-4, 4), steps=2)
    ctx.sleep(random.uniform(0.08, 0.14))
    top = top_entry(gs.menu())
    if top and top["verb"] == verb:
        ctx.inp.click()
        return True
    return False


def drop_known(ctx, gs, slots):
    """Drop exactly these slots by the menu's "Drop" row (game data) - for items the caller knows
    by name (cut gems, spawned bones...), which the generic product check would keep."""
    from lumberjack.core.gamestate import menu_row_point
    open_tab(ctx, "inventory")
    cancel_selection(ctx)
    dropped = 0
    for i in sorted(slots, key=lambda i: (i % R.INV_COLS, i // R.INV_COLS)):
        x, y = R.INV_SLOTS[i].center
        ctx.inp.right_click(x + random.randint(-4, 4), y + random.randint(-4, 4))
        ctx.sleep(random.uniform(0.15, 0.25))
        m = gs.menu()
        e = next((e for e in m.get("entries") or [] if e["verb"] == "Drop"), None)
        if m.get("open") and e:
            rx, ry = menu_row_point(m, e["row"])
            ctx.inp.click(rx + random.randint(-15, 15), ry + random.randint(-1, 1))
            ctx.sleep(random.uniform(0.08, 0.15))
            dropped += 1
        else:
            ctx.inp.move(x - 120, y)
    return dropped


def drop_all(ctx, keep=(), food=False):
    """Drop every item in the backpack (except slot indexes in `keep`). Food ("Eat ...")
    is only dropped with food=True. Only products ever go: with game data they're known by
    name (dropped straight from the menu), else each slot's hover text is checked first."""
    from lumberjack.core import backpack, gamestate
    gs = gamestate.shared()
    if gs is not None and backpack.slots() is not None:
        for _ in range(3):
            inv = backpack.slots() or []
            slots = [i for i, s in enumerate(inv)
                     if s["id"] >= 0 and i not in keep and backpack.is_product(s["key"], food)]
            if not slots:
                return
            drop_known(ctx, gs, slots)
            ctx.sleep(SERVER_TICK)
        return
    open_tab(ctx, "inventory")
    for _ in range(3):
        slots = [i for i, o in enumerate(inventory.occupied(ctx.grab())) if o and i not in keep]
        if not slots:
            break
        slots.sort(key=lambda i: (i % R.INV_COLS, i // R.INV_COLS))  # column by column
        for i in slots:
            drop_slot(ctx, i, food)
        ctx.sleep(SERVER_TICK * 2)
