# Skills

Every task needs the game's own data (the add-on loaded by the Grindstone icon). Tasks marked
**spawns** create what they need with the singleplayer `::item` command, so they need
*Spawn missing tools* ticked (Settings tab → Run options, on by default).

**Is every skill working?** Tools tab → **Skill check** runs each skill for 2 minutes at the right
place for your level and lists which ones earned XP and why any stopped (`logs\skill_check.txt`).

Common options (**Settings** tab → **Run options**):

| Option | What it does |
|---|---|
| Keep going after problems | A crash, getting stuck or dying restarts the task where it was |
| Drop materials at Start | Clears logs, ore, fish, bones, hides... first, so a restart has room. Tools stay |
| Keep what I'm carrying | Never drops or banks the items in your backpack at Start (tools) |
| Spawn missing tools | Spawns a missing axe, pickaxe, net, tinderbox, food, runes, supplies |
| Stop after | A time limit for this run |

## Gathering

### 🪓 Woodcutting
Chops the best allowed tree in reach (or picks trees for your level with *Best trees for my
levels*). When the backpack is full: **drop**, **bank** (the nearest booth, found from the game
data), **burn** (light the logs) or **fletch** them.
Route: trees (1) → oaks (15) → willows (30) → maples at Seers' (45) → yews at Edgeville (60).

### 🎣 Fishing (+ cooking)
Fishes with the chosen method, or switches method as you level (*Switch method as my Fishing
level goes up*). With *Cook the catch* it chops a log from a nearby tree, lights a fire and
cooks the catch; burnt fish are dropped, cooked fish dropped or banked. It remembers where it
has seen fishing spots and walks back to them when none are in range.
Route: net at Draynor (1) → lure at the Lumbridge river (20) → cage at Catherby (40) → harpoon
at Catherby (50: swordfish, sharks from 76).

### ⛏️ Mining
Mines the chosen ores (or the best for your level). It learns which rock holds which ore as it
goes, shared through `rock_ores.json`. Drops or banks when full.
Route: copper/tin (1) → iron (15) at Varrock east mine → coal (30) at the Barbarian Village mine
→ mithril + coal in the Mining guild (55).

## Combat

### ⚔️ Combat (melee)
With *Spawn missing tools* it wears the best scimitar for your Attack level and the best armour
for your Defence level, upgrading as you level. Attacks the chosen monsters, switching attack style to train the lowest of Attack, Strength and
Defence (or one you pick). Eats below the set HP%; with no food picked it spawns lobsters. Loots
the chosen drops, buries bones, and banks loot when the backpack fills.
Route: chickens (1) → cows (5) → goblins (10) → Al Kharid warriors (20) → hill giants in the
Edgeville dungeon (30) → ice warriors (45) → ankou in the Stronghold of Security (60) → fire giants (75).

### 🎯 Ranged · spawns
Wears the best shortbow for your Ranged level (shortbow → oak 5 → willow 20 → maple 30 →
yew 40 → magic 50) and the best arrows it can fire, spawned 1,000 at a time. Same monsters,
food and looting as Combat. Route: as Combat, by Ranged level.

### 🪄 Magic · spawns
| Level | Spell |
|---|---|
| 1 | Wind Strike on chickens (Water Strike 5, Earth Strike 9, Fire Strike 13, Wind Bolt 17) |
| 21 | Low Level Alchemy on a stack of arrows |
| 25 | Varrock Teleport |
| 45 | Camelot Teleport |
| 55 | High Level Alchemy |

Runes are spawned 1,000 at a time. Spells are found in the spellbook by name.

### 🧤 Thieving
Pickpockets the nearest suitable person. Being caught (HP drops) means a stun - it waits it out.
Eats below the set HP% (spawns lobsters when out). Drops junk but keeps coins and food.
Route: men & women in West Ardougne (1) → Al Kharid warriors (25) → Varrock guards (40) →
Ardougne knights (55) → paladins (70) → heroes (80).

### 💀 Slayer · spawns
Melee on the training route's monsters for your Slayer level, with a matching Slayer task set by
the admin command (`::setslayertask`, renewed before it runs out), so every kill gives Slayer XP
as well as combat XP. Gear, food and eating as Combat.
Route: chickens (1) → cows (5) → goblins (10) → hill giants (25) → ice warriors (40) → ankou (55)
→ fire giants (70).

### 🌱 Farming · spawns
Teleports to the Falador farm and works both allotments (and the herb patch from level 9): rakes
the weeds, adds supercompost, plants the best seeds for your level, grows them with the admin
`::grow` command, cures diseased crops, digs up dead ones, harvests and drops the harvest. Each
patch's state is read from the game. Allotments: potatoes (1) → onions (5) → cabbages (7) →
tomatoes (12) → sweetcorn (20) → strawberries (31) → watermelons (47). Herbs: guam (9) → … →
torstol (85).

### 🏃 Agility
Teleports to the Gnome Stronghold course (1), or the Barbarian Outpost course from 35, and runs
laps: each obstacle is found by its id in the scene, and when it gets lost, stuck or falls it
teleports back to the start. At the end it teleports
back to where you were.

### 🪤 Hunter · spawns
Teleports to the creatures for your level, lays as many traps as the level allows (1, +1 at
20/40/60/80), checks catches, picks up collapsed traps and drops the bones and meat.
Route: crimson swifts (1) → cerulean twitches (11) → tropical wagtails (19) with bird snares →
chinchompas (53) → red chinchompas (63) with box traps.

## Artisan (all spawn their supplies)

Each loads a backpack of the best supply for your level, processes it where you started, drops
the result and repeats. Stand somewhere open.

| Skill | Supplies → result |
|---|---|
| 🔥 Firemaking | logs → fires (normal 1, oak 15, willow 30, maple 45, yew 60, magic 75). Untick *Spawn missing tools* to burn banked logs instead (needs a map with a bank spot) |
| 🍳 Cooking | raw fish → cooked (shrimps 1, herring 5, trout 15, pike 20, salmon 25, tuna 30, lobster 40, swordfish 45), on its own fire |
| 🦴 Prayer | dragon bones → buried |
| 🏹 Fletching | logs + knife → arrow shafts (1), bows (u) (shortbow 5, longbow 10, oak 20/25, willow 35/40, maple 50/55, yew 65/70, magic 80/85) |
| 💎 Crafting | uncut gems + chisel → cut (opal 1, jade 13, red topaz 16, sapphire 20, emerald 27, ruby 34, diamond 43, dragonstone 55) |
| 🔨 Smithing | bars + hammer → daggers at an anvil (bronze 1, iron 15, steel 30, mithril 50, adamant 70, rune 85). Start next to an anvil, e.g. ★ Varrock anvil |
| 🌿 Herblore | unfinished potions + secondaries → the best potion for your level (attack 3 … zamorak brew 78). Herblore needs the Druidic Ritual quest: it's marked done with the admin quest command, and a level-1 account gets the quest's 250 XP |
| 🔮 Runecrafting | pure essence → runes. Teleports into the best altar room for your level (air 1, mind 2, water 5, earth 9, fire 14, body 20, cosmic 27, nature 44, law 54) and back out at the end |

## Item spawner

**Tools** tab → **Item spawner** spawns tools, gear, food, runes and supplies on demand.
