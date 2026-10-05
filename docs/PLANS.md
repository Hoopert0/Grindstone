# Plans

A plan runs tasks one after another, nonstop. Plan tab → add steps → **Start plan**.

## Steps

Each step has a **task**, an end - **minutes** and/or a target **level** (whichever comes first;
both blank = until stopped) - and a **place**:

- *(stay where I am)* - no travel
- a saved or built-in place - travel there first
- **auto - best spot for my level** - the task's training route picks the place and settings,
  and the step moves up a tier as soon as you reach its level

Before each step the bot tidies up: closes menus and dialogs, travels, then **banks** everything
the next task doesn't use at the nearest booth (other tasks' tools, products, loot) - or drops
products and loot if there's no bank nearby. Unknown items are only ever banked, never dropped.

## Options

| Option | What it does |
|---|---|
| Loop | Start over after the last step |
| Stop the plan after N hours | An overall time limit (kept across panel restarts) |
| Lowest level first | The next step is the one whose skill is lowest; steps already at their target level are skipped |
| Update between steps | Downloads a newer version between steps, restarts the panel, carries on |
| Resume after the panel restarts | A plan that was running when the panel closed starts again (after the step it had finished) |
| Travel by teleport | Travel with `::tele` (singleplayer admin); otherwise walk, with Home Teleport for long trips |

## 🤖 Autopilot

Controls → **🤖 Autopilot** → **Start**. The target level (99 = everything) and skills to leave
out are on the **Settings** tab. No steps needed. It uses its own settings (drop for XP, cook the
catch, spawned food, no looting), wears the best weapon and armour for its levels, and shows its
progress (total level) under Controls. It trains Attack/Strength/Defence to 10 first (enough HP for everything after),
Prayer to 43, then always the lowest skill up to its next milestone - the next better spot
(oaks at 15, willows at 30...) or the next 10 levels - staying on that skill until it gets
there, at the best spot for the level. A run that stops earning XP for 6 minutes counts as a problem. Tick skills under *Skip* to leave them out. A skill that fails twice in a row
is rested for an hour. The plan options (time limit, updates, resume) apply.

## ★ Starter plan

One click: woodcutting, fishing, mining, combat, ranged, magic, thieving and smithing for 30
minutes each at auto places, firemaking, cooking, prayer, fletching and crafting for 20 minutes
each, looping, lowest level first. Add a time limit and press **Start plan**.

## Places

Stand somewhere, type a name, **Save here**. **Go** travels to a place. Built-in places are
marked ★ and used by *auto*; their coordinates are approximate.

**Check ★ places** (Places tab) visits every built-in place and counts what it's for (trees, rocks, fishing
spots, monsters, an anvil). If they're elsewhere nearby, it walks over and saves the place there.
To fix one yourself, stand at the right spot and save a place with **exactly the same name** -
yours always wins over the built-in one.

## When things go wrong

A step that ends badly (a crash, no progress for 6 minutes, dying, being teleported away by a
random event) is recovered: back to where it was working, tidy up, try again - up to 3 times in
a row; a run that went well for 10 minutes resets the count. If the game closes it waits for it
(and restarts it after a crash). A round where nothing worked waits a minute before the next.

## History

**Last 24 hours** on the Plan tab: total time, XP per skill, levels gained, and each run with
how it ended. Stored per PC in `logs\runs.jsonl`.
