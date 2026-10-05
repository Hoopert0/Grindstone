# Contributing

Thanks for helping! Bug reports, fixes and new skills are all welcome.

## Reporting a bug

Open an issue with:
- what you were doing (task, plan, settings) and what happened instead
- the version (shown at the top of the panel, e.g. `v12`)
- `logs\panel.log` - and `logs\client.log` / `logs\game_crash_*.log` if the game misbehaved
- a screenshot of the panel if it helps

## Development setup

After running `setup_pc.ps1`, use its Python environment:

```powershell
$py = "$env:USERPROFILE\.venvs\lumberjack\Scripts\python.exe"
& $py -m pip install pytest pyflakes
& $py -m pytest lumberjack/tests
```

The tests run offline (no game) and also on Linux/macOS: Windows-only modules are stubbed.

## Guidelines

- **Game data first, pixels as fallback.** Read what you need through the add-on
  (`core/gamestate.py`, `ui/widgets.py`) and confirm a click with the menu's top entry before
  clicking. Keep any pixel path working for when the add-on can't answer.
- **Never get stuck silently.** A bot should either make progress or stop with a clear
  `stop_reason`; the plan runner recovers from there.
- **Never drop what you can't name.** Only drop items positively known as products.
- **Add a test** for logic that can run without the game (see `lumberjack/tests`).
- Keep the style of the surrounding code: small functions, short docstrings saying *why*.
- Changes to `lumberjack/agent` need a game restart to take effect - mention it in the PR.

## Adding a skill

1. A bot class in `lumberjack/skills/` (see `spawn_tasks.py` for the spawn-process-repeat
   pattern, or `thieving_task.py` for an NPC-driven one) with `run()`, `progress()` and `stats()`.
2. Register it in `web/server.py`: `build_bot`, `TASK_LEVEL_SKILLS`, `TASK_SKILLS`, checks.
3. Add it to the Task list and `TASKS` in `web/index.html`.
4. Optional: a training route in `nav/training.py` and item ids in `items.py`.
5. Tests and a line in `docs/SKILLS.md`.
