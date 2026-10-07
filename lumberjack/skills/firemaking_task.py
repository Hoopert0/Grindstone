"""Firemaking task: burn banked logs - or, with game data and 'Spawn missing tools', spawned
ones (::item, singleplayer admin): the best logs the Firemaking level can light, a backpack at
a time, burned where the run started, for as long as it runs. No bank or stock needed.

    bank: deposit (keep tinderbox/axe) -> Withdraw-All of the best log type we can light
    walk to the burn spot -> light them all -> repeat until the bank runs out

Log types are tried best-first (willow 30, oak 15, normal 1). A type that won't light
at all (level too low) is banked again and skipped from then on.
"""
from lumberjack import actions, bank
from lumberjack.skills import firemaking
from lumberjack.skills.base import BotBase, StopBot
from lumberjack.ui import inventory

# bank-name template -> Firemaking level needed
LOG_LEVELS = {"logs": 1, "oak_logs": 15, "willow_logs": 30, "maple_logs": 45, "yew_logs": 60, "magic_logs": 75}


LANES = (0, 2, -2, 4, -4)       # rows (tiles north/south of the start) the loads take turns on


class Firemaker(BotBase):
    name = "firemaking"

    def __init__(self, logs=("willow_logs", "oak_logs", "logs"), bank_spot=None, burn_spot=None,
                 spawn_logs=False, **kw):
        self.keep_carried_fm = kw.pop("keep_carried", True)
        kw.pop("clear_at_start", None)               # logs are the fuel - never dropped at start
        super().__init__(keep_carried=False, **kw)   # we decide what to keep ourselves (logs are fuel)
        self.log_types = sorted(logs, key=lambda t: -LOG_LEVELS.get(t, 0))
        self.bank_spot, self.burn_spot = bank_spot, burn_spot
        self.blocked = set()       # log types we can't light (level too low)
        self.current = None
        self.tinder_slot = None
        self.spawn_logs = spawn_logs      # with game data: spawn logs instead of banking
        self.home = None                  # spawning: where we started - fires are lit around here

    def progress(self):
        return self.burned

    def stats(self):
        return f"{self.burned} fires, {self.banked} bank trips"

    def _bank_xy(self):
        from lumberjack.nav.spots import bank_spots
        if self.bank_spot:
            return self.spot_xy(self.bank_spot)
        found = bank_spots(self.walker.map) if self.walker else []
        return (found[0][1]["x"], found[0][1]["y"]) if found else None

    def restock(self):
        """Bank: deposit everything we don't keep, withdraw the next log type. False = none left."""
        xy = self._bank_xy()
        if self.walker and xy:
            self.state = "walking to bank"
            if not self.walker.walk_to(*xy):
                raise StopBot("couldn't reach the bank")
        self.state = "banking"
        if not bank.open_bank(self.ctx):
            raise StopBot("couldn't open the bank")
        # deposit everything but the user's keepers - including our tinderbox, then take a
        # fresh one, so we never carry on with a bank note by mistake
        if self.tinder_slot is not None:
            self.keep_slots = set(self.keep_slots) - {self.tinder_slot}
            self.tinder_slot = None
        frame = self.ctx.grab()
        self.keep_slots = {s for s in self.keep_slots if not inventory.is_noted(frame, s)}
        bank.deposit_all(self.ctx, keep_slots=self.keep_slots)
        if self.tinder_slot is None:
            if not bank.withdraw(self.ctx, "tinderbox"):
                bank.close(self.ctx)
                raise StopBot("no tinderbox in the backpack or the bank")
            self.ctx.sleep(0.6)
            # the tinderbox lands in the first free slot
            free = [i for i, o in enumerate(inventory.occupied(self.ctx.grab())) if o and i not in self.keep_slots]
            self.tinder_slot = free[0] if free else None
            if self.tinder_slot is not None:
                self.keep_slots = set(self.keep_slots) | {self.tinder_slot}
        got = None
        for kind in self.log_types:
            if kind in self.blocked or not _known(kind):
                continue
            if bank.withdraw_all(self.ctx, kind):
                got = kind
                break
        bank.close(self.ctx)
        self.banked += 1
        self.current = got
        return got is not None

    def keep_non_logs(self):
        """Keep what we're carrying except logs (those get burned)."""
        from lumberjack.skills.base import MAX_KEPT
        keep = actions.carried_keepers(self.ctx)
        if len(keep) > MAX_KEPT:
            raise StopBot(f"you're carrying {len(keep)} non-log items - bank all but the axe/tinderbox first")
        self.keep_slots = keep
        if keep:
            self.log.info("Keeping slots %s (everything else gets burned/banked)",
                          ", ".join(str(s + 1) for s in sorted(keep)))

    def run(self):
        self.log.info("Firemaking started - press F12 to stop")
        learner = None
        try:
            self.prepare()
            if self.walker:
                from lumberjack.nav.learner import MapLearner
                learner = MapLearner(self.walker, self.win)
                learner.start()
            from lumberjack.core import gamestate
            if self.spawn_logs and gamestate.shared() is not None:
                return self.run_spawning()       # tools are known by name - no keep limit
            if self.keep_carried_fm:
                self.keep_non_logs()
            self.tinder_slot = firemaking.find_tinderbox(self.ctx)
            if self.tinder_slot is not None:
                self.keep_slots = set(self.keep_slots) | {self.tinder_slot}
            while True:
                logs_held = [i for i, o in enumerate(inventory.occupied(self.grab()))
                             if o and i not in self.keep_slots]
                if not logs_held:
                    if not self.restock():
                        self.stop_reason = "finished - no more logs in the bank that you can light"
                        self.log.info("No more logs to burn. %s", self.stats())
                        return
                    continue
                if self.tinder_slot is None:
                    self.restock()  # fetches the tinderbox
                    continue
                self.go_to_burn_spot()
                self.state = f"burning {(self.current or 'logs').replace('_', ' ')}"
                n, gave_up = firemaking.burn_all(self.ctx, self.tinder_slot, self.keep_slots)
                self.burned += n
                self.log.info("Lit %d fires (%s)", n, self.stats())
                if gave_up and n == 0 and self.current:
                    self.log.warning("Can't light %s yet - skipping them", self.current.replace("_", " "))
                    self.blocked.add(self.current)
                if gave_up:
                    self.restock_or_finish()
        except StopBot as e:
            self.stop_reason = str(e)
            self.log.info("Stopped: %s. %s", e, self.stats())
        finally:
            self.state = "stopped"
            if learner:
                learner.stop()
            self.inp.close()

    # ---- spawning: no bank needed --------------------------------------------------------
    def run_spawning(self):
        """Spawn a load of the best logs we can light, burn them, walk back, repeat (until the
        run's time/level limit). Called from run() - its StopBot handling applies."""
        from lumberjack import items
        from lumberjack.core import backpack, gamestate
        gs = gamestate.shared()
        self.log.info("Spawning logs and burning them here (no bank needed)")
        self.home = self.base = list(gs.player()["tile"])
        self.lane = 0
        self.chat_handled = ("can't light a fire here",)      # burn_all steps aside by itself
        while True:
            self.check_stop()
            slot = items.ensure(self.ctx, lambda k: k == "tinderbox", "tinderbox", log=self.log)
            inv = backpack.slots() or []
            tinder = next((i for i, s in enumerate(inv) if s["key"] == "tinderbox"), None)
            if tinder is None:
                raise StopBot("no tinderbox and couldn't spawn one")
            self.tinder_slot = tinder if slot is None or slot < 0 else slot
            self.keep_slots = set(self.keep_slots) | {self.tinder_slot}
            logs_held = [i for i, s in enumerate(inv) if s["key"] in LOG_LEVELS]
            if not logs_held:
                # each load starts on its own row: the last row's fires are still burning
                off = LANES[self.lane % len(LANES)]
                self.lane += 1
                self.home = [self.base[0], self.base[1] + off]
                self.walk_home(gs)
                if not self.spawn_load():
                    raise StopBot("couldn't spawn logs (backpack full of other things?)")
            self.state = f"burning {(self.current or 'logs').replace('_', ' ')}"
            n, gave_up = firemaking.burn_all(self.ctx, self.tinder_slot, self.non_log_slots())
            self.burned += n
            self.log.info("Lit %d fires (%s)", n, self.stats())
            if gave_up and n == 0 and self.current:
                self.log.warning("Can't light %s - trying a lower log", self.current.replace("_", " "))
                self.blocked.add(self.current)
                actions.drop_all(self.ctx, keep=self.non_log_slots(), food=False)

    def non_log_slots(self):
        """Every slot that isn't logs (burn_all only burns what's not kept)."""
        from lumberjack.core import backpack
        inv = backpack.slots() or []
        return {i for i, s in enumerate(inv) if s["id"] >= 0 and s["key"] not in LOG_LEVELS}

    def best_log(self):
        from lumberjack.core import gamestate
        lv = (gamestate.skill("firemaking") or {}).get("base", 1)
        ok = [k for k, need in LOG_LEVELS.items() if need <= lv and k not in self.blocked]
        return max(ok, key=LOG_LEVELS.get) if ok else "logs"

    def spawn_load(self):
        """Fill the free slots with the best logs. True if any arrived."""
        from lumberjack import items
        from lumberjack.core import backpack
        kind = self.best_log()
        self.current = kind
        self.state = f"spawning {kind.replace('_', ' ')}"
        start = len([s for s in backpack.slots() or [] if s["id"] >= 0])
        for _ in range(28):
            have = len([s for s in backpack.slots() or [] if s["id"] >= 0])
            free = 28 - have
            if free <= 0:
                break
            items.spawn(self.ctx, kind, free)
            self.ctx.sleep(0.4)
            got = len([s for s in backpack.slots() or [] if s["id"] >= 0]) - have
            if got <= 0:
                break               # (only one per command? the next round asks for the rest)
        return len([s for s in backpack.slots() or [] if s["id"] >= 0]) > start

    def walk_home(self, gs):
        """Each load lights a line of fires away from where we stand: go back first."""
        from lumberjack.core import interact
        me = gs.player()["tile"]
        if self.home and max(abs(me[0] - self.home[0]), abs(me[1] - self.home[1])) > 0:
            self.state = "walking back"
            interact.walk_to_tile(self.ctx, gs, self.home, arrive=1)

    def go_to_burn_spot(self):
        """Fires can't be lit indoors (e.g. in the bank): walk to the burn spot unless
        we're already near it."""
        import math
        xy = self.spot_xy(self.burn_spot)
        if not xy:
            return
        here = self.walker.where()
        if here and math.hypot(here.x - xy[0], here.y - xy[1]) <= 24:  # ~6 tiles
            return
        self.walk_to_spot(self.burn_spot)
        actions.reset_camera(self.ctx)

    def restock_or_finish(self):
        """After a failed burn: bank the leftovers and pick a type we can light."""
        if not self.restock():
            raise StopBot("no logs left that can be lit")


def _known(kind):
    from lumberjack.core import gamestate
    from lumberjack.ui import mouseover
    return mouseover.available(kind) or gamestate.shared() is not None   # by name from the game
