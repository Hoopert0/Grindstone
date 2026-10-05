"""Fletching: knife on logs -> "What would you like to make?" -> Make 10, repeated.

The make box shows one option per product for that log type, left to right, e.g.
    logs:        Arrow shafts, Shortbow (u), Longbow (u), Wooden stock
    willow logs: Willow shortbow (u), Willow longbow (u), Willow stock
Options are located from their label text; the product is picked by position in the
known order for that log type. Arrow shafts stack in one slot; bows take a slot each.
"""
import logging
import time
from pathlib import Path

import cv2
import numpy as np

from lumberjack import actions
from lumberjack.core import regions as R
from lumberjack.ui import inventory, menu, mouseover

log = logging.getLogger("fletching")

TITLE = Path(__file__).resolve().parents[1] / "assets" / "templates" / "make_title.png"
TITLE_BOX = (140, 350, 230, 18)
LABEL_BAND = (10, 424, 500, 16)          # x, y, w, h of the option labels row
ICON_Y = 405
MAKE_10_ROW = 2                          # Make 1, Make 5, Make 10, Make X, Cancel

# (product, Fletching level) in make-box order, per log type
PRODUCTS = {
    "logs": [("arrow_shafts", 1), ("shortbow", 5), ("longbow", 10), ("wooden_stock", 9)],
    "oak_logs": [("oak_shortbow", 20), ("oak_longbow", 25), ("oak_stock", 24)],
    "willow_logs": [("willow_shortbow", 35), ("willow_longbow", 40), ("willow_stock", 39)],
    "maple_logs": [("maple_shortbow", 50), ("maple_longbow", 55), ("maple_stock", 54)],
    "yew_logs": [("yew_shortbow", 65), ("yew_longbow", 70), ("yew_stock", 69)],
    "magic_logs": [("magic_shortbow", 80), ("magic_longbow", 85)],
}
STACKS = {"arrow_shafts"}

_title = None


def make_box_open(frame):
    global _title
    if _title is None:
        _title = cv2.imread(str(TITLE))
    x, y, w, h = TITLE_BOX
    box = frame[y:y + h, x:x + w].astype(np.int16)
    red = ((box[..., 2] > 110) & (box[..., 1] < 60) & (box[..., 0] < 60)).sum()  # dark-red title text
    return red > 80 and float(np.abs(box - _title.astype(np.int16)).mean()) < 15


def option_centers(frame):
    """x centres of the make-box options, left to right (from the dark label text)."""
    x0, y0, w, h = LABEL_BAND
    band = frame[y0:y0 + h, x0:x0 + w]
    cols = np.where((band.max(axis=2) < 90).any(axis=0))[0]
    if not len(cols):
        return []
    groups, s, e = [], cols[0], cols[0]
    for c in cols[1:]:
        if c - e > 30:            # gaps inside a label ("shortbow (u)") are smaller
            groups.append((s, e))
            s = c
        e = c
    groups.append((s, e))
    return [int(x0 + (a + b) / 2) for a, b in groups]


def _label(product):
    """The make box's label for a product key: 'oak_shortbow' -> 'oak shortbow'."""
    return product.replace("_", " ") if product else None


def best_product(log_type, level):
    """Highest-level product we can make from these logs (stocks only if nothing else)."""
    opts = [(p, lv) for p, lv in PRODUCTS.get(log_type, []) if lv <= level]
    bows = [o for o in opts if "stock" not in o[0]]
    pool = bows or opts
    return max(pool, key=lambda o: o[1])[0] if pool else None


def log_slots(ctx, keep=()):
    frame = ctx.grab()
    return [i for i, o in enumerate(inventory.occupied(frame)) if o and i not in keep]


def find_logs(ctx, keep=()):
    """{log_type: [slots]} by item name (the game's data, else hovering each slot)."""
    from lumberjack.core import backpack
    inv = backpack.slots()
    if inv is not None:
        out = {}
        for i, s in enumerate(inv):
            if s["key"] in PRODUCTS and i not in keep:
                out.setdefault(s["key"], []).append(i)
        return out
    out = {}
    for i in log_slots(ctx, keep):
        ctx.inp.move(*R.INV_SLOTS[i].center, steps=2)
        kind = None
        for wait in (0.12, 0.25, 0.35):   # busy backgrounds can spoil a single read
            ctx.sleep(wait)
            kind = mouseover.which(ctx.grab(), "use", list(PRODUCTS), "orange")
            if kind:
                break
        if kind:
            out.setdefault(kind, []).append(i)
    ctx.inp.move(260, 300)
    return out


def open_make_box(ctx, knife_slot, log_slot):
    ctx.inp.click(*R.INV_SLOTS[knife_slot].center)
    ctx.sleep(0.35)
    ctx.inp.click(*R.INV_SLOTS[log_slot].center)
    from lumberjack.core import gamestate
    from lumberjack.ui import widgets
    gs = gamestate.shared()
    end = time.monotonic() + 3
    while time.monotonic() < end:
        ctx.sleep(0.25)
        if make_box_open(ctx.grab()) or (gs is not None and widgets.make_box(gs)):
            return True
    return False


def choose_make_10(ctx, option_index, product=None):
    from lumberjack.core import gamestate
    from lumberjack.ui import widgets
    gs = gamestate.shared()
    if gs is not None and widgets.make(ctx, gs, product=_label(product), amounts=("10", "All", "X", "5")):
        return True
    frame = ctx.grab()
    xs = option_centers(frame)
    if option_index >= len(xs):
        log.warning("Make box has %d options, wanted #%d", len(xs), option_index + 1)
        return False
    x, y = xs[option_index], ICON_Y
    ctx.inp.right_click(x, y)
    ctx.sleep(0.35)
    frame = ctx.grab()
    m = menu.find(frame, (x, y))
    if not m:
        return False
    rows = menu.rows(m)
    if len(rows) <= MAKE_10_ROW or not menu.row_has_action(frame, rows[0], "make"):
        ctx.inp.move(x, y - 120)
        return False
    ctx.inp.click(*rows[MAKE_10_ROW].center)
    return True


def _slot_look(frame, i):
    return R.INV_SLOTS[i].crop(frame).astype(np.int16)


def _changed(a, b, threshold=8.0):   # log->empty measures ~17, an unchanged slot 0
    return float(np.abs(a - b).mean()) > threshold


def fletch_type(ctx, knife_slot, log_type, product, keep=(), on_progress=None):
    """Fletch every log of one type into `product`. Returns how many logs were used."""
    names = [p for p, _ in PRODUCTS[log_type]]
    idx = names.index(product)
    used = stalls = 0
    while stalls < 3:
        actions.dismiss_dialog(ctx)
        slots = find_logs(ctx, keep).get(log_type, [])
        if not slots:
            break
        looks = {i: _slot_look(ctx.grab(), i) for i in slots}   # what each log slot looks like now
        if not open_make_box(ctx, knife_slot, slots[0]) or not choose_make_10(ctx, idx, product):
            stalls += 1
            ctx.inp.move(260, 300)
            continue
        # A fletched log either vanishes (shafts stack elsewhere) or turns into a bow in the
        # same slot - so count log slots whose picture changed. A batch of 10 takes ~15-20 s.
        last, still = 0, 0
        while still < 5:
            ctx.sleep(0.8)
            if actions.dismiss_dialog(ctx):   # level up interrupts making
                break
            frame = ctx.grab()
            now = sum(1 for i in slots if _changed(looks[i], _slot_look(frame, i)))
            if now > last:
                last, still = now, 0
            else:
                still += 1
        made = last
        used += made
        if on_progress and made:
            on_progress(made)   # live counter for the panel
        stalls = stalls + 1 if made == 0 else 0
    if stalls >= 3:
        log.warning("Couldn't fletch %s into %s (level too low?)", log_type.replace("_", " "),
                    product.replace("_", " "))
    return used


def find_knife(ctx):
    from lumberjack import bank
    slots = bank.inventory_slots_with(ctx, "knife", "use")
    ctx.inp.move(260, 300)
    frame = ctx.grab()
    real = [s for s in slots if not inventory.is_noted(frame, s)]
    return real[0] if real else None
