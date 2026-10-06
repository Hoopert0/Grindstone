"""Random events, recognised by the game's own NPC data and shaken off.

The 2009scape server (content/global/ame, core/game/worldevents/holiday):

  * the anti-macro events (Genie, Sandwich lady, Evil Bob, Rock Golem...) are paused for admin
    accounts - but an ignored one notes the whole backpack and teleports us to Lumbridge or the
    Draynor sewers, and the hostile ones (golem, river troll, shade, zombie, tree spirit) attack;
  * the holiday events (Halloween 17 Oct - 7 Nov, Christmas in December) fire for admins too:
    they follow us for 30 s - 3 min, hit us, turn us into a zombie or a cat, stand on our clicks;
  * none of them has a "Dismiss" option here, and every one of them ends the moment we're more
    than 10 tiles from it (RandomEventNPC.tick / HolidayRandomEventNPC.tick).

So: an event NPC that is after us -> ::tele 15 tiles away, a tick or two, ::tele straight back.
Where an event offers "Dismiss" (other servers' NPC data) that's used instead.
"""
import logging
import time

log = logging.getLogger("events")

# NPC ids only events use (the server's RandomEvents / HolidayRandomEvents and their helpers)
EVENT_IDS = (
    set(range(2463, 2469))            # evil chicken (one per combat bracket)
    | set(range(391, 397))            # river troll
    | set(range(413, 419))            # rock golem
    | set(range(419, 425))            # zombie
    | set(range(425, 432))            # shade
    | set(range(438, 444))            # tree spirit
    | {410, 409, 407, 408, 411,       # mysterious old man, genie, strange plant, swarm
       2478, 2479, 2476, 2477,        # evil bob, rick turpentine, quiz master
       956, 2790, 2538, 3117,         # drunken dwarf, sergeant damien, giles (certer), sandwich lady
       3207, 1056,                    # pious pete, mime
       6740, 8517, 8540}              # Christmas: snow, jack frost, santa
)
# ids events borrow from ordinary NPCs (a spider, a cook, a witch, the Zanaris choir, a pillory
# guard...): an event only when it came for us - right next to us and facing us
SHARED_IDS = {2862, 1023, 61, 2716, 2714, 4607, 611, 4239, 3206, 2791, 2458, 3312,
              6742, 6743, 6744, 6745, 6746}
NEAR = 3                    # an event NPC stands by us (it follows us around)
HOP_TILES = 15              # > 10: the server ends the event
HOP_WAIT_S = 1.5            # stay away a couple of ticks
SHAKE_EVERY_S = 20.0        # at most this often (a regular NPC with a borrowed id: no ping-pong)


def after_us(npc, me_index, skip=()):
    """Is `npc` a random event that's after us? `skip`: names (keys) the bot is fighting."""
    from lumberjack.core.backpack import key
    if key(npc.get("name") or "") in skip:
        return False
    nid = npc.get("id")
    facing = npc.get("interacting") == 32768 + me_index
    if nid in EVENT_IDS:
        return facing or npc.get("dist", 99) <= 1
    if nid in SHARED_IDS:
        return facing and npc.get("dist", 99) <= NEAR
    return False


def handle(bot, gs):
    """Deal with a random event after us: dismiss it, else teleport away and back. True if one
    was dealt with."""
    ctx = getattr(bot, "ctx", None)
    if ctx is None:
        return False
    me = gs.player()
    mine = me.get("index", -1)
    skip = set(getattr(bot, "targets", None) or [])
    for n in gs.npcs():
        if n.get("dist", 99) > 8:
            continue
        if "Dismiss" in (n.get("ops") or []) and (n.get("interacting") == 32768 + mine or n.get("dist", 99) <= 1):
            from lumberjack.core import interact
            log.info("Random event: dismissing %s", n["name"])
            if interact.use_option(ctx, gs, interact.points_for(n), "Dismiss", n["name"]):
                ctx.sleep(1.2)
                _count(bot)
                return True
            continue
        if after_us(n, mine, skip):
            return shake_off(bot, gs, me, n)
    return False


def shake_off(bot, gs, me, npc):
    """::tele HOP_TILES away and straight back to the same tile: the event ends."""
    from lumberjack.nav import places
    now = time.monotonic()
    if now - getattr(bot, "_event_shaken_at", float("-inf")) < SHAKE_EVERY_S:
        return False
    bot._event_shaken_at = now
    tile, plane = list(me["tile"]), me.get("plane", 0)
    ctx = bot.ctx
    before = getattr(bot, "state", "")
    bot.state = f"teleporting away from a random event ({npc['name']})"
    log.info("Random event: %s is after us - teleporting away and back to lose it", npc["name"])
    try:
        away = [tile[0] + HOP_TILES, tile[1]]
        if not places.teleport(ctx, gs, away, plane, camera=False):
            log.warning("Couldn't teleport away from %s (::tele needs an admin account)", npc["name"])
            return False
        ctx.sleep(HOP_WAIT_S)
        if not places.teleport(ctx, gs, tile, plane, camera=False):
            log.warning("Couldn't teleport back after the random event")
            return False
        _count(bot)
        return True
    finally:
        bot.state = before


def _count(bot):
    bot.events_handled = getattr(bot, "events_handled", 0) + 1
