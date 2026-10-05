# Troubleshooting

## Logs

| File | What's in it |
|---|---|
| `logs\panel.log` | Everything the bot did and why it stopped - start here |
| `logs\client.log` | The game client's own output |
| `logs\game_crash_<number>.log` | Java's report when the game crashed |
| `logs\game_freeze_<time>.log` | What the game was doing when it froze (saved by the watchdog) |
| `logs\server.log` | The singleplayer server |
| `logs\runs.jsonl` | One line per run (the Plan tab's history) |

When asking for help, attach `panel.log` (and the client/crash logs if the game misbehaved).

## Common problems

**"Game data isn't readable" / the panel asks you to restart the game**
The add-on is loaded when the game starts. Close the game and start it with the Grindstone icon
(not the 2009scape launcher's own client).

**"A 2009scape window from the launcher is open"**
Close the client the launcher opened; Grindstone starts its own.

**The bot clicks in the wrong places**
Use **SD** graphics and the **Fixed** layout, and leave the game window at its normal size.

**A built-in place is off** (no trees / rocks / monsters there)
Places tab → **Check ★ places**, or stand at the right spot and save a place with the same name.

**It stops with "no fishing spot found"**
Fishing spots move. The bot remembers where it saw them and walks back; if it has never seen
any near you, go to a fishing spot once (or use a plan step with place *auto*).

**The game closed and came back by itself**
That's the crash recovery: after a crash, or when the game froze for 90 seconds while a bot was
running, it's restarted (up to 3 times an hour). Log in and the bot carries on. A frozen game
leaves `logs\game_freeze_<time>.log` - please include it when reporting.

**The game freezes**
Grindstone reads the game's picture from inside the client and never waits on the game's
window, so it shouldn't cause freezes. If one happens while a bot runs, the watchdog saves what
the game was doing to `logs\game_freeze_<time>.log` and restarts it.

**The update was skipped**
You've changed files the update would replace. The panel log names them; undo the change
(`git checkout -- <file>`) or keep your version and skip updates.

**Windows Defender / SmartScreen asks about Java or Python**
Allow it: the game and the bot talk to each other over localhost (port 8765 for the panel,
47001-47002 inside the game).

**GitHub keeps asking me to sign in**
Grindstone never asks from the background any more; it reuses the GitHub CLI's sign-in if you
have one (`gh auth login`). A public copy needs no sign-in at all.

**Something else**
Check `logs\panel.log` for the last lines before it went wrong and open an issue with it.
