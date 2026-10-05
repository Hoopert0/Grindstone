"""Banking: open a booth, deposit, withdraw, close.

Learned from the 2009scape client:
  * booths left-click to "Use", which does NOT open the bank - "Use-quickly" (right-click
    menu) does. Some booths are "Closed bank booth" and are skipped by the hover check.
  * there's no deposit-everything button; in bank mode an inventory item's menu has
    "Deposit-All", which removes every item of that type.
  * bank items hover as "Withdraw-1 <item>" (item name in orange); left-click = withdraw 1.
"""
import logging
import time
from pathlib import Path

import cv2
import numpy as np

from lumberjack.core import regions as R
from lumberjack.ui import inventory, menu, mouseover

log = logging.getLogger("bank")

TITLE = Path(__file__).resolve().parent / "assets" / "templates" / "bank_title.png"
TITLE_BOX = (190, 26, 140, 18)          # x, y, w, h of "Bank of RuneScape"
CLOSE_BUTTON = (489, 34)
BANK_SLOT0 = (54, 105)                  # first bank item
BANK_DX, BANK_DY, BANK_COLS, BANK_ROWS = 43.5, 45, 10, 5

_title = None


def is_open(frame):
    global _title
    if _title is None:
        _title = cv2.imread(str(TITLE))
    x, y, w, h = TITLE_BOX
    return float(np.abs(frame[y:y + h, x:x + w].astype(np.int16) - _title.astype(np.int16)).mean()) < 12


def _wait_open(ctx, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if is_open(ctx.grab()):
            return True
        ctx.sleep(0.25)
    return False


def _find_booth(ctx, radius=150, step=18):
    """Hover a grid around our character until the text reads 'Use Bank booth'."""
    px, py = R.PLAYER.center
    pts = []
    for dy in range(-radius, radius + 1, step):
        for dx in range(-radius, radius + 1, step):
            x, y = px + dx, py + dy
            if R.VIEWPORT.contains(x, y) and not R.MOUSEOVER_TEXT.contains(x, y):
                pts.append((dx * dx + dy * dy, x, y))
    for _, x, y in sorted(pts):  # nearest first
        ctx.inp.move(x, y, steps=2)
        ctx.sleep(0.07)
        if mouseover.is_text(ctx.grab(), "use", "bank_booth"):
            return x, y
    return None


BOOTH = "Bank booth"            # "Closed bank booth" doesn't match - those are skipped
BOOTH_RADIUS = 15               # tiles searched for a booth when opening the bank
TRIP_RADIUS = 45                # tiles searched for a booth for a bank trip without a map


def _gs():
    from lumberjack.core import gamestate
    return gamestate.shared()


def _booths(gs, radius):
    return [l for l in gs.locs(radius, "bank") if l["name"] == BOOTH and l["ops"]]


def open_bank_gs(ctx, gs):
    """Booths (and bankers) from the game's own data; 'Use-quickly' confirmed in the menu.
    Walks up to the nearest booth when none is on screen. True when the bank is open."""
    from lumberjack.core import interact
    for attempt in range(2):
        booths = _booths(gs, BOOTH_RADIUS)
        for b in [b for b in booths if interact.on_screen(*b["screen"])][:3]:
            verb = "Use-quickly" if "Use-quickly" in b["ops"] else ("Bank" if "Bank" in b["ops"] else b["ops"][0])
            if interact.use_option(ctx, gs, interact.points_for(b), verb, b["name"]) and _wait_open(ctx, 8):
                log.info("Bank open")
                return True
        bankers = [n for n in gs.npcs("Banker") if "Bank" in n["ops"] and interact.on_screen(*n["screen"])]
        for n in bankers[:2]:
            if interact.use_option(ctx, gs, interact.points_for(n), "Bank", n["name"]) and _wait_open(ctx, 8):
                log.info("Bank open (banker)")
                return True
        if not booths or attempt:
            break
        interact.walk_to_tile(ctx, gs, booths[0]["tile"], arrive=1)
    log.warning("Couldn't open a bank booth nearby")
    return False


def gs_bank_trip(ctx, keep_slots=(), radius=TRIP_RADIUS, back=True):
    """No map needed: walk to the nearest bank booth, deposit everything but `keep_slots`,
    walk back to the exact tile we left from. False if there's no game data or no booth."""
    from lumberjack.core import interact
    from lumberjack.core.gamestate import GameStateError
    gs = _gs()
    if gs is None:
        return False
    try:
        start = gs.player()["tile"]
        booths = _booths(gs, radius)
        if not booths:
            log.warning("No bank booth within %d tiles", radius)
            return False
        log.info("Walking to the bank booth %d tiles away", booths[0]["dist"])
        if not interact.walk_to_tile(ctx, gs, booths[0]["tile"], arrive=3):
            return False
        if not open_bank_gs(ctx, gs):
            return False
        ok = deposit_all(ctx, keep_slots=keep_slots)
        close(ctx)
        if ok and back:
            log.info("Walking back")
            interact.walk_to_tile(ctx, gs, start, arrive=1)
        return ok
    except GameStateError as e:
        log.warning("Game data stopped answering mid-trip (%s)", e)
        return False


def open_bank(ctx, tries=3):
    """Open the bank from somewhere near a booth. Returns True when the bank screen is up."""
    if is_open(ctx.grab()):
        return True
    gs = _gs()
    if gs is not None:
        from lumberjack.core.gamestate import GameStateError
        try:
            return open_bank_gs(ctx, gs)
        except GameStateError:
            pass          # fall back to hovering for the booth
    for attempt in range(tries):
        spot = _find_booth(ctx)
        if not spot:
            log.warning("No bank booth in view")
            return False
        x, y = spot
        ctx.inp.right_click(x, y)
        ctx.sleep(0.35)
        frame = ctx.grab()
        m = menu.find(frame, (x, y))
        opt = menu.find_option(frame, m, "use_quickly") if m else None
        if not opt:
            ctx.inp.move(x, y - 80)  # close the menu and try another booth
            continue
        ctx.inp.click(*opt[1].center)
        # we may need to walk up to the booth first
        if _wait_open(ctx, 8):
            log.info("Bank open")
            return True
    return False


BANK_INV = 95                   # the bank's contents in the game's own data
BANK_VISIBLE = BANK_COLS * BANK_ROWS


def _menu_pick(ctx, gs, x, y, verb, subject=None):
    """Right-click (x, y) and choose the row whose verb is `verb` (and subject, if given)."""
    import random as _r
    from lumberjack.core.gamestate import menu_row_point
    ctx.inp.right_click(x, y)
    ctx.sleep(0.3)
    m = gs.menu()
    e = next((e for e in m.get("entries") or [] if e["verb"] == verb
              and (subject is None or e["subject"].lower() == subject.lower())), None)
    if not (m.get("open") and e):
        ctx.inp.move(x - 150, y)
        return False
    rx, ry = menu_row_point(m, e["row"])
    ctx.inp.click(rx + _r.randint(-12, 12), ry + _r.randint(-1, 1))
    return True


def _slot_point(index):
    r, c = divmod(index, BANK_COLS)
    return int(BANK_SLOT0[0] + c * BANK_DX), int(BANK_SLOT0[1] + r * BANK_DY)


def find_item_gs(ctx, gs, item):
    """Canvas point of a bank item by name (game data), confirmed by hovering it ("Withdraw-1
    <name>"). None if it isn't in the bank or not in the first visible rows."""
    from lumberjack.core.backpack import key
    from lumberjack.core.gamestate import top_entry
    for i, s in enumerate(gs.inv(BANK_INV)):
        if s.get("id", -1) < 0 or key(s.get("name")) != item:
            continue
        if i >= BANK_VISIBLE:
            log.warning("%s is in the bank but below the first %d slots - move it up", s["name"], BANK_VISIBLE)
            return None
        x, y = _slot_point(i)
        ctx.inp.move(x, y, steps=2)
        ctx.sleep(0.15)
        top = top_entry(gs.menu())
        if top and top["verb"].startswith("Withdraw") and key(top["subject"]) == item:
            return x, y
        log.info("Bank slot %d doesn't hover as %s (a tab view?) - searching the old way", i + 1, s["name"])
        return None
    return None


def deposit_all(ctx, keep_slots=()):
    """Deposit every inventory item except those in `keep_slots` (slot indexes)."""
    gs = _gs()
    if gs is not None:
        from lumberjack.core.gamestate import GameStateError
        try:
            for _ in range(28):
                occupied = [i for i, o in enumerate(inventory.occupied(ctx.grab())) if o and i not in keep_slots]
                if not occupied:
                    return True
                x, y = R.INV_SLOTS[occupied[0]].center
                if not _menu_pick(ctx, gs, x, y, "Deposit-All"):
                    log.warning("No Deposit-All option on slot %d", occupied[0] + 1)
                    return False
                ctx.sleep(0.9)
            return False
        except GameStateError:
            pass
    for _ in range(28):
        occupied = [i for i, o in enumerate(inventory.occupied(ctx.grab())) if o and i not in keep_slots]
        if not occupied:
            return True
        i = occupied[0]
        x, y = R.INV_SLOTS[i].center
        ctx.inp.right_click(x, y)
        ctx.sleep(0.3)
        frame = ctx.grab()
        m = menu.find(frame, (x, y))
        opt = menu.find_option(frame, m, "deposit_all") if m else None
        if not opt:
            ctx.inp.move(x - 150, y)
            log.warning("No Deposit-All option on slot %d", i)
            return False
        ctx.inp.click(*opt[1].center)
        ctx.sleep(0.9)
    return False


NOTE_BUTTON = (241, 307)
NOTE_BOX = (224, 293, 35, 29)           # x, y, w, h of the note-mode button
_TEMPLATES = Path(__file__).resolve().parent / "assets" / "templates"


def note_mode_on(frame):
    """The note button looks pressed (darker) while withdrawals come out as bank notes."""
    x, y, w, h = NOTE_BOX
    crop = frame[y:y + h, x:x + w].astype(np.int16)
    on = cv2.imread(str(_TEMPLATES / "note_btn_on.png")).astype(np.int16)
    off = cv2.imread(str(_TEMPLATES / "note_btn_off.png")).astype(np.int16)
    return np.abs(crop - on).mean() < np.abs(crop - off).mean()


def ensure_item_mode(ctx):
    """Withdrawals must come out as items, not bank notes - a tinderbox does nothing to
    noted logs ("Nothing interesting happens")."""
    ctx.inp.move(300, 250)          # don't hover the button (hover changes its look)
    ctx.sleep(0.2)
    if not note_mode_on(ctx.grab()):
        return True
    log.info("Bank was in note mode - switching to item withdrawals")
    ctx.inp.click(*NOTE_BUTTON)
    ctx.sleep(0.5)
    ctx.inp.move(300, 250)
    ctx.sleep(0.2)
    return not note_mode_on(ctx.grab())


def withdraw(ctx, item, count=1):
    """Withdraw `count` of an item (by name from the game, else its orange-name template) with left-clicks."""
    ensure_item_mode(ctx)
    gs = _gs()
    pos = find_item_gs(ctx, gs, item) if gs is not None else None
    if pos:
        for _ in range(count):
            ctx.inp.click()
            ctx.sleep(0.6)
        log.info("Withdrew %d x %s", count, item)
        return True
    for r in range(BANK_ROWS):
        for c in range(BANK_COLS):
            x = int(BANK_SLOT0[0] + c * BANK_DX)
            y = int(BANK_SLOT0[1] + r * BANK_DY)
            ctx.inp.move(x, y, steps=2)
            ctx.sleep(0.1)
            if mouseover.is_text(ctx.grab(), "withdraw_1", item, "orange"):
                for _ in range(count):
                    ctx.inp.click()
                    ctx.sleep(0.6)
                log.info("Withdrew %d x %s", count, item)
                return True
    log.warning("Couldn't find %s in the bank", item)
    return False


def find_item(ctx, item):
    """Canvas position of a bank item (by name from the game, else its orange-name template), or None."""
    gs = _gs()
    if gs is not None:
        pos = find_item_gs(ctx, gs, item)
        if pos:
            return pos
    for r in range(BANK_ROWS):
        for c in range(BANK_COLS):
            x = int(BANK_SLOT0[0] + c * BANK_DX)
            y = int(BANK_SLOT0[1] + r * BANK_DY)
            ctx.inp.move(x, y, steps=2)
            ctx.sleep(0.1)
            if mouseover.is_text(ctx.grab(), "withdraw_1", item, "orange"):
                return x, y
    return None


def withdraw_all(ctx, item):
    """Withdraw every one of an item (right-click > Withdraw-All). False if it's not there."""
    ensure_item_mode(ctx)
    pos = find_item(ctx, item)
    if not pos:
        log.info("No %s left in the bank", item.replace("_", " "))
        return False
    x, y = pos
    gs = _gs()
    if gs is not None and _menu_pick(ctx, gs, x, y, "Withdraw-All"):
        ctx.sleep(0.9)
        log.info("Withdrew all %s", item.replace("_", " "))
        return True
    ctx.inp.right_click(x, y)
    ctx.sleep(0.35)
    frame = ctx.grab()
    m = menu.find(frame, (x, y))
    opt = menu.find_option(frame, m, "withdraw_all") if m else None
    if not opt:
        ctx.inp.move(x, y - 60)
        log.warning("No Withdraw-All option for %s", item)
        return False
    ctx.inp.click(*opt[1].center)
    ctx.sleep(0.9)
    log.info("Withdrew all %s", item.replace("_", " "))
    return True


def close(ctx):
    ctx.inp.click(*CLOSE_BUTTON)
    ctx.sleep(0.6)
    return not is_open(ctx.grab())


def inventory_slots_with(ctx, item, action="use"):
    """Inventory slots whose hover reads '<action> <item>' (normal mode, e.g. 'Use Tinderbox').
    With the game's own data: the slots holding that item, by name."""
    from lumberjack.core import backpack
    found = backpack.find(item)
    if found is not None:
        return found
    out = []
    for i, occ in enumerate(inventory.occupied(ctx.grab())):
        if not occ:
            continue
        ctx.inp.move(*R.INV_SLOTS[i].center, steps=2)
        ctx.sleep(0.12)
        if mouseover.is_text(ctx.grab(), action, item, "orange"):
            out.append(i)
    return out
