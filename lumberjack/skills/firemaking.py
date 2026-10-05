"""Firemaking: keep a tinderbox in the backpack and burn the logs in it.

Lighting = click the tinderbox ("Use Tinderbox"), then click a log. Success shows up as
that log's slot emptying (the character then steps aside on its own). Failures - "You
can't light a fire here", standing on a fire, too low a level - just leave the log
there, so after a couple of misses we step a few tiles away and try again, and after
repeated misses we give up on the load.
"""
import logging
import math
import random
import time

from lumberjack import bank
from lumberjack.core import regions as R
from lumberjack.ui import inventory
from lumberjack.vision import minimap as MM

log = logging.getLogger("firemaking")

LIGHT_TIMEOUT = 9.0     # a light attempt can take several game ticks at low levels
MISSES_BEFORE_MOVE = 2
MOVES_BEFORE_GIVING_UP = 2


def find_tinderbox(ctx):
    """Slot of a usable tinderbox (a noted one can't light anything), or None."""
    slots = bank.inventory_slots_with(ctx, "tinderbox", "use")
    ctx.inp.move(260, 300)
    frame = ctx.grab()
    real = [s for s in slots if not inventory.is_noted(frame, s)]
    if slots and not real:
        log.warning("The tinderbox in the backpack is a bank note - it'll be swapped at the bank")
    return real[0] if real else None


def fetch_tool(ctx, item, find, walker=None, bank_xy=None, return_xy=None, keep_slots=()):
    """Get a tool (e.g. "tinderbox", "knife") from the bank. Walks there and back if a
    map/bank spot is given. Items in `keep_slots` (e.g. an axe) stay in the backpack.
    `find(ctx)` locates the tool afterwards. Returns its slot, or None."""
    if bank_xy and walker:
        log.info("No %s - walking to the bank for one", item)
        if not walker.walk_to(*bank_xy):
            return None
    if not bank.open_bank(ctx):
        return None
    frame = ctx.grab()
    keep_slots = {s for s in keep_slots if not inventory.is_noted(frame, s)}  # never keep notes
    bank.deposit_all(ctx, keep_slots=keep_slots)   # make room, keeping the axe etc.
    ok = bank.withdraw(ctx, item)
    bank.close(ctx)
    if not ok:
        return None
    if return_xy and walker:
        walker.walk_to(*return_xy)
    return find(ctx)


def fetch_tinderbox(ctx, walker=None, bank_xy=None, return_xy=None, keep_slots=()):
    return fetch_tool(ctx, "tinderbox", find_tinderbox, walker, bank_xy, return_xy, keep_slots)


def _step_aside(ctx):
    """Walk 3-5 tiles in a random direction (a fresh, fire-free tile)."""
    ang = random.uniform(0, 2 * math.pi)
    r = random.uniform(12, 20)                 # minimap px (~4 per tile)
    ctx.inp.click(MM.CENTER[0] + r * math.cos(ang), MM.CENTER[1] + r * math.sin(ang))
    ctx.sleep(3.5)


def light_one(ctx, tinder_slot, log_slot):
    """Use tinderbox on one log. True if the log was used up (fire lit)."""
    tx, ty = R.INV_SLOTS[tinder_slot].center
    lx, ly = R.INV_SLOTS[log_slot].center
    ctx.inp.click(tx + random.randint(-4, 4), ty + random.randint(-4, 4))
    ctx.sleep(random.uniform(0.25, 0.4))
    ctx.inp.click(lx + random.randint(-4, 4), ly + random.randint(-4, 4))
    end = time.monotonic() + LIGHT_TIMEOUT
    while time.monotonic() < end:
        ctx.sleep(0.4)
        if not inventory.occupied(ctx.grab())[log_slot]:
            ctx.sleep(1.2)                     # let the character step off the fire
            return True
    return False


def burn_all(ctx, tinder_slot, keep_slots=()):
    """Burn every log in the backpack. Returns (burned, gave_up)."""
    keep = set(keep_slots) | {tinder_slot}
    from lumberjack import actions
    burned = misses = moves = 0
    while True:
        actions.dismiss_dialog(ctx)
        logs = [i for i, o in enumerate(inventory.occupied(ctx.grab())) if o and i not in keep]
        if not logs:
            return burned, False
        if light_one(ctx, tinder_slot, logs[0]):
            burned += 1
            misses = 0
            continue
        if actions.dismiss_dialog(ctx):   # a level-up popped up mid-attempt - not a real miss
            continue
        misses += 1
        ctx.inp.move(260, 300)
        if misses >= MISSES_BEFORE_MOVE:
            if moves >= MOVES_BEFORE_GIVING_UP:
                log.warning("Can't light these logs (Firemaking level too low?) - giving up on this load")
                return burned, True
            log.info("Couldn't light a fire here - moving a few tiles")
            _step_aside(ctx)
            moves += 1
            misses = 0
