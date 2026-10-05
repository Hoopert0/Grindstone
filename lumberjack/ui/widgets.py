"""The game's interfaces read from the client (add-on query `state widgets`): dialogs, make
boxes, side tabs - each visible component with its canvas box, text, options and item.

    find(gs, "click here to continue")   -> [{'if', 'idx', 'x', 'y', 'w', 'h', 'text', 'ops', 'obj'...}]
    continue_dialog(ctx, gs)             -> clicked "Click here to continue"
    make_box(gs)                         -> the make/cook box's option components, or []
    choose(ctx, gs, widget, op)          -> do one of a component's options (left- or right-click)
    make(ctx, gs, product=None, amount=("All", "10", "X"))   -> pick "Make All" (for `product`)

An add-on built before this query existed answers "unknown state": then every function here
returns None/False/[] and the callers keep their pixel way.
"""
import logging
import random
import re
import time

from lumberjack.core.gamestate import GameStateError, clean, menu_row_point

log = logging.getLogger("widgets")
_unsupported = False            # the add-on answered "unknown state" (asked again after RETRY_S)
_unsupported_at = 0.0
RETRY_S = 300                   # a restarted game has the rebuilt add-on


def find(gs, match=None):
    """Visible components (optionally only those whose text/options contain `match`, or of one
    interface id). [] without support."""
    global _unsupported, _unsupported_at
    if gs is None or (_unsupported and time.monotonic() - _unsupported_at < RETRY_S):
        return []
    try:
        out = gs._q("widgets" + (f" {match}" if match is not None else ""))
    except GameStateError as e:
        if "unknown state" in str(e):
            if not _unsupported:
                log.info("The game add-on can't read interfaces yet (restart the game to rebuild it)")
            _unsupported, _unsupported_at = True, time.monotonic()
        return []
    _unsupported = False
    for w in out:
        w["text"] = clean(w.get("text"))[0] if w.get("text") else ""
        w["name"] = clean(w.get("name"))[0] if w.get("name") else ""
        w["ops"] = [clean(o)[0] for o in w.get("ops") or [] if o]
        opt = clean(w.get("option"))[0] if w.get("option") else ""
        if opt and opt not in w["ops"]:          # an older-style button: its one action ("Make All")
            w["ops"].append(opt)
    return out


def center(w):
    return w["x"] + w["w"] // 2, w["y"] + w["h"] // 2


_no_dialog_at = float("-inf")
DIALOG_CHECK_S = 1.0            # loops call this every pass; a full interface read once a second is plenty


def continue_dialog(ctx, gs):
    """Click "Click here to continue" if a dialog shows it. True if clicked."""
    global _no_dialog_at
    if time.monotonic() - _no_dialog_at < DIALOG_CHECK_S:
        return False
    for w in find(gs, "click here to continue"):
        if w["w"] > 0 and w["h"] > 0:
            x, y = center(w)
            ctx.inp.click(x + random.randint(-8, 8), y + random.randint(-1, 1))
            ctx.sleep(0.6)
            return True
    _no_dialog_at = time.monotonic()
    return False


def choose(ctx, gs, w, op, menu=False):
    """Do option `op` of component `w`: left-click when it's the first option, else (or with
    menu=True: other components lie on top of it) right-click and pick it from the menu by
    text. True if clicked."""
    x, y = center(w)
    x, y = x + random.randint(-3, 3), y + random.randint(-3, 3)
    if not menu and w["ops"] and w["ops"][0].lower() == op.lower():
        ctx.inp.click(x, y)
        return True
    ctx.inp.right_click(x, y)
    ctx.sleep(random.uniform(0.25, 0.35))
    try:
        m = gs.menu()
    except GameStateError:
        return False
    e = next((e for e in m.get("entries") or [] if e["verb"].lower() == op.lower()), None)
    if m.get("open") and e:
        rx, ry = menu_row_point(m, e["row"])
        ctx.inp.click(rx + random.randint(-10, 10), ry + random.randint(-1, 1))
        return True
    ctx.inp.move(x, max(10, y - 120))           # leave the menu
    return False


MAKE_OP = re.compile(r"^(make|cook|cut|craft|smelt|smith|fletch|string)\b.*\b(all|\d+|x)$", re.I)


def make_box(gs):
    """Components of an open make/cook box ("Make 1 / 5 / All / X" options), or []."""
    return [w for w in find(gs) if any(MAKE_OP.match(o) for o in w["ops"])]


def make(ctx, gs, product=None, amounts=("All", "10", "X", "5")):
    """With a make box open: pick the first available of `amounts` ("Make All"...) for `product`
    (the buttons nearest its label, or whose options name it), else for the only/first product.
    The server's skill dialogues (and the smithing screen) give each amount its own button,
    often stacked on the item picture - those are picked from the right-click menu. True if
    clicked; False when there's no box (or no add-on support)."""
    box = make_box(gs)
    if not box:
        return False
    ref = None
    if product and len(box) > 1:
        want = product.replace("_", " ").lower()
        labels = [w for w in find(gs) if w["text"] and want in w["text"].lower()]
        if labels:
            ref = center(labels[0])
        else:
            named = [w for w in box if any(want in o.lower() for o in w["ops"])]
            box = named or box
    if ref is None:
        ref = center(box[0])                    # the first product's buttons
    dist = lambda w: (center(w)[0] - ref[0]) ** 2 + (center(w)[1] - ref[1]) ** 2
    near = min(dist(w) for w in box) + 60 ** 2            # within this product's group
    for amount in amounts:
        tail = " " + amount.lower()
        offers = [w for w in box if dist(w) <= near and any(o.lower().endswith(tail) for o in w["ops"])]
        if not offers:
            continue
        w = min(offers, key=dist)
        op = next(o for o in w["ops"] if o.lower().endswith(tail))
        stacked = any(o is not w and abs(center(o)[0] - center(w)[0]) < 4
                      and abs(center(o)[1] - center(w)[1]) < 4 for o in box)
        log.info("Make box: %s%s", op, f" ({product.replace('_', ' ')})" if product else "")
        return choose(ctx, gs, w, op, menu=stacked)
    return False
