"""Turning the old shared-save junctions back into the game's own folders."""
import subprocess
import sys
import types

from lumberjack import site_fixes


def test_unlink_save_copies_and_keeps_the_repo_copy(tmp_path, monkeypatch):
    repo, game = tmp_path / "repo", tmp_path / "2009scape"
    data = game / "singleplayer" / "game" / "data"
    data.mkdir(parents=True)
    for f, files in (("players", {"hero.json": '{"x": 1}'}), ("playerstats", {"player_stats.db": "db"}),
                     ("eco", {}), ("serverstore", {})):
        (repo / "save" / f).mkdir(parents=True)
        for n, t in files.items():
            (repo / "save" / f / n).write_text(t)
        (data / f).symlink_to(repo / "save" / f, target_is_directory=True)     # stands in for a junction
    monkeypatch.setattr(site_fixes, "REPO", repo)
    monkeypatch.setattr(site_fixes, "_is_junction", lambda p: p.is_symlink())
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "lumberjack.launch_client", types.SimpleNamespace(GAME_DIR=game))
    monkeypatch.setitem(sys.modules, "lumberjack.start", types.SimpleNamespace(java_processes=lambda: []))
    site_fixes.unlink_save(log=lambda *a: None)
    for f in site_fixes.SAVE_FOLDERS:
        assert (data / f).is_dir() and not (data / f).is_symlink()
    assert (data / "players" / "hero.json").read_text() == '{"x": 1}'
    assert (repo / "save" / "players" / "hero.json").exists()             # the repo copy is untouched


def test_unlink_save_waits_while_the_game_runs(tmp_path, monkeypatch):
    repo, game = tmp_path / "repo", tmp_path / "2009scape"
    data = game / "singleplayer" / "game" / "data"
    data.mkdir(parents=True)
    (repo / "save" / "players").mkdir(parents=True)
    (data / "players").symlink_to(repo / "save" / "players", target_is_directory=True)
    monkeypatch.setattr(site_fixes, "REPO", repo)
    monkeypatch.setattr(site_fixes, "_is_junction", lambda p: p.is_symlink())
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "lumberjack.launch_client", types.SimpleNamespace(GAME_DIR=game))
    monkeypatch.setitem(sys.modules, "lumberjack.start", types.SimpleNamespace(
        java_processes=lambda: [(1, "java -jar server.jar")]))
    site_fixes.unlink_save(log=lambda *a: None)
    assert (data / "players").is_symlink()                                      # left alone for now


def test_restore_from_history_after_the_repo_copy_is_deleted(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "save" / "players").mkdir(parents=True)
    (repo / "save" / "players" / "hero.json").write_text("saved")
    g = lambda *a: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=repo,
                                  check=True, capture_output=True)
    g("init", "-q")
    g("add", "-A")
    g("commit", "-qm", "save")
    g("rm", "-rq", "save")
    g("commit", "-qm", "remove the shared save")
    monkeypatch.setattr(site_fixes, "REPO", repo)
    dest = tmp_path / "players"
    assert site_fixes._restore_from_history("players", dest)
    assert (dest / "hero.json").read_text() == "saved"
