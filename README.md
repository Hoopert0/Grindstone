<p align="center"><img src="lumberjack/assets/grindstone.png" width="112" alt="Grindstone"></p>

# Grindstone

**A nonstop skill-training bot for 2009scape singleplayer**, driven from a control panel in your
browser. Pick a skill and press Start, or press **★ Starter plan** and let it rotate through
every skill for hours, moving to better spots and better gear as you level up.

> [!IMPORTANT]
> **Singleplayer only.** Grindstone is built for your own offline 2009scape singleplayer game
> and relies on its admin commands (`::item`, `::tele`). Don't use it on the public 2009scape
> servers: botting is against their rules and will get the account banned.

---

- [Features](#features)
- [Skills](#skills)
- [Requirements](#requirements)
- [Install](#install)
- [First run](#first-run)
- [Everyday use](#everyday-use)
- [Updates](#updates)
- [Documentation](#documentation)
- [Contributing](#contributing) · [License](#license)

## Features

- **19 skills, one panel.** Gathering, combat and artisan skills, each with sensible defaults.
- **🤖 Autopilot.** One button trains the whole account by itself - even a brand-new one in
  Lumbridge: combat basics first, quick Prayer, then always the lowest skill up to its next
  milestone, at the best spot for the level.
- **Plans.** Chain tasks by time or target level, loop them, train the lowest skill first, stop
  after N hours. The **★ Starter plan** sets up every skill in one click.
- **Training routes.** Set a plan step's place to *auto* and it picks the right spot and target
  for your level - trees → oaks → willows, shrimps → trout → lobsters, chickens → cows →
  goblins → Al Kharid warriors - moving up a tier the moment you level.
- **Runs unattended.** Recovers from crashes, getting stuck, dying and random events; restarts
  the game after a crash; banks or drops between tasks; spawns the tools, food, runes and
  supplies it needs; resumes a plan after the panel restarts.
- **Reads the game itself.** A small add-on inside the game client reports the backpack, skills,
  NPCs, scenery, menus and interfaces, so the bot acts on what the game says is there instead of
  guessing from pixels.
- **Leaves your PC alone.** Clicks and keys go into the game window directly - your real mouse
  stays free and the game can sit behind other windows.
- **History.** The Plan tab shows what the last 24 hours earned: XP per skill, levels gained, and
  every run with why it ended.

## Skills

| | Skill | How it trains |
|---|---|---|
| 🪓 | Woodcutting | Chops the best tree for your level; drops, banks, burns or fletches the logs |
| 🎣 | Fishing | Net → lure → cage by level; cooks the catch on a fire it lights from a nearby tree |
| ⛏️ | Mining | Best ore for your level; learns which rock holds which ore; drops or banks |
| ⚔️ | Combat | Melee, rotating Attack/Strength/Defence; eats, loots, buries bones |
| 🎯 | Ranged | Combat with the best shortbow for your level and its arrows (spawned, worn) |
| 🪄 | Magic | Strikes and Wind Bolt → Low Alchemy → Varrock/Camelot Teleport → High Alchemy |
| 🧤 | Thieving | Pickpockets men → Al Kharid warriors → guards → knights → paladins → heroes; handles stuns, eats |
| 💀 | Slayer | Melee on the route's monsters with a matching Slayer task, set and renewed by itself |
| 🌱 | Farming | Falador allotments + herb patch: rake, compost, plant, ::grow, harvest |
| 🏃 | Agility | Laps of the Gnome Stronghold course, the Barbarian Outpost course from 35; teleports back to the start when lost |
| 🪤 | Hunter | Bird snares, then box traps for chinchompas, as many traps as the level allows |
| 🔥 | Firemaking | Spawns the best logs you can light and burns them (or uses banked logs) |
| 🍳 | Cooking | Spawns raw fish for your level and cooks them on its own fire |
| 🦴 | Prayer | Spawns and buries dragon bones |
| 🏹 | Fletching | Knife on spawned logs → the best bow (u) you can make |
| 💎 | Crafting | Cuts spawned uncut gems, opal to dragonstone |
| 🔨 | Smithing | Spawned bars into daggers at an anvil |
| 🌿 | Herblore | Spawned unfinished potions + ingredients into the best potion for your level |
| 🔮 | Runecrafting | Teleports into the best altar room, crafts spawned pure essence |

Details, requirements and level tiers: [docs/SKILLS.md](docs/SKILLS.md).

## Requirements

- **Windows 10 or 11**
- **2009scape singleplayer**, installed with the official launcher and started once
- That's it - the setup script installs Git, Python 3.12 and a Java JDK 11 for you (via `winget`)

## Install

Open **PowerShell** and run:

```powershell
winget install -e --id Git.Git
git clone https://github.com/idle-forge/Grindstone $env:USERPROFILE\Grindstone
powershell -ExecutionPolicy Bypass -File $env:USERPROFILE\Grindstone\setup_pc.ps1
```

(If `git` isn't found right after installing it, close and reopen PowerShell.)

The setup script creates a Python environment in `%USERPROFILE%\.venvs\lumberjack` and a
**Grindstone** icon on your desktop. No GitHub account is needed.

If your game isn't in `%USERPROFILE%\2009scape`, set the `LUMBERJACK_GAME_DIR` environment
variable to its folder before running setup.

## First run

1. Double-click **Grindstone** on the desktop. It starts the singleplayer server, a bot-ready
   game client and the control panel at **http://127.0.0.1:8765**.
2. Log in. Choose **SD** graphics and the **Fixed** screen layout.
3. In the panel's **Places** tab, press **Check ★ places** once. It visits the built-in training
   spots and corrects any that are off.
4. Under **Controls**, pick **🤖 Autopilot** (or one skill) and press **Start**.

**F12** stops the bot at any time.

## Everyday use

- **Everything:** Controls → **🤖 Autopilot** → Start. Set the target level and skills to leave
  out on the **Settings** tab.
- **One skill:** Controls → pick it → Start (its options are on the **Settings** tab). *Keep going after problems* (on by default) restarts
  it where it was after a crash, a stall or a death.
- **Many skills:** Plan tab → add steps (task, minutes and/or target level, place) or use
  **★ Starter plan**. See [docs/PLANS.md](docs/PLANS.md).
- **Quitting:** just close the game - a running bot is stopped too. (Only a real crash or a
  frozen game is restarted automatically.)

## Updates

Updates are automatic. Every time you start Grindstone it downloads the newest version first.
While it's running, the panel shows **⬆ Update to vN** when a new version is out - press it
with the bot stopped and the panel restarts on the new version. A plan can also update itself
between steps (Plan tab → *Update between steps*).

What your PC learned in game is always kept. If you've edited the code yourself, the update is
skipped and the panel tells you which files are in the way.

## Documentation

- [Skills](docs/SKILLS.md) - every task, what it needs, how it levels
- [Plans](docs/PLANS.md) - plans, training routes, places, history, running overnight
- [Troubleshooting](docs/TROUBLESHOOTING.md) - logs, common problems and fixes
- [How it works](docs/HOW_IT_WORKS.md) - the add-on, game data, the code layout

## Contributing

Bug reports and improvements are welcome - see [CONTRIBUTING.md](CONTRIBUTING.md). When
reporting a problem, attach `logs\panel.log` (and `logs\client.log` if the game misbehaved).

## License

[MIT](LICENSE). Grindstone is a fan project and isn't affiliated with 2009scape or Jagex.
