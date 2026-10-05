# How it works

## The pieces

```
 Grindstone icon ─► lumberjack.start ─► singleplayer server (java)
                                    ├─► game client (java + Grindstone add-on)  ◄──┐ localhost
                                    └─► control panel (Python, FastAPI) ───────────┘ socket
                                              │
                                              └─► browser: http://127.0.0.1:8765
```

- **The add-on** (`lumberjack/agent`, loaded with `-javaagent`) runs inside the game client. It
  injects mouse and keyboard events into the game canvas (so your own cursor is untouched) and
  answers questions about the game state by reading the client's fields:

  | Query | Answers |
  |---|---|
  | `state menu` | the right-click menu entries (verb, target, row) |
  | `state npcs [name]` | NPCs: name, options, tile, distance, screen position, combat |
  | `state inv [id]` | backpack (93), worn equipment (94), bank (95) |
  | `state skills` / `player` | levels and XP; position, animation, movement, HP bar |
  | `state locs [radius] [name]` | scenery: trees, rocks, booths, anvils, fires |
  | `state ground [radius]` | items on the ground |
  | `state widgets [id\|text]` | open interfaces: dialogs, make boxes, spellbook |
  | `state camera [yaw pitch zoom]` | read or set the camera |

  It's rebuilt automatically from source when the game starts.
- **The bots** (`lumberjack/skills`) act on that data: find a target, hover it, confirm the
  menu's top entry is what they expect, click. Screen recognition is kept as a fallback.
- **The panel** (`lumberjack/web`) runs bots and plans in a thread, recovers them, and streams
  status and the bot's view to the browser.

## Code layout

| Path | What |
|---|---|
| `lumberjack/agent/` | the Java add-on (input, game-state queries) |
| `lumberjack/core/` | game data client, input, window capture, backpack, interaction helpers |
| `lumberjack/skills/` | one module per task; `watch.py` = checks every task shares |
| `lumberjack/nav/` | places, travel, training routes, fishing-spot memory, map walking |
| `lumberjack/ui/` | reading the game's interface (backpack, menus, interfaces, stats) |
| `lumberjack/vision/` | screen recognition fallbacks |
| `lumberjack/web/` | control panel: `server.py` (API, bot/plan runner), `index.html` |
| `lumberjack/updater.py` | safe self-update |
| `lumberjack/tests/` | offline tests - no game needed |

(The Python package is still called `lumberjack`, the project's original name.)
