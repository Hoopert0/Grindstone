"""The updater against real git repos: a 'GitHub' bare repo, the author's copy and a friend's."""
import subprocess

import pytest

from lumberjack import savesync, updater, version


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd,
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repos(tmp_path, monkeypatch):
    hub, author, friend = tmp_path / "hub.git", tmp_path / "author", tmp_path / "friend"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(hub))
    git(tmp_path, "clone", "-q", str(hub), str(author))
    git(author, "checkout", "-q", "-b", "main")
    (author / "lumberjack" / "assets" / "templates").mkdir(parents=True)
    (author / "lumberjack" / "code.py").write_text("v = 1\n")
    (author / "lumberjack" / "assets" / "templates" / "eat.png").write_text("shipped")
    git(author, "add", "-A")
    git(author, "commit", "-q", "-m", "one")
    git(author, "push", "-q", "-u", "origin", "main")
    git(tmp_path, "clone", "-q", str(hub), str(friend))
    for mod in (updater, version):
        monkeypatch.setattr(mod, "REPO", friend)
    monkeypatch.setattr(savesync, "SAVE", friend / "save")          # no shared save: plain copy
    return author, friend


def publish(author, text="v = 2\n"):
    (author / "lumberjack" / "code.py").write_text(text)
    git(author, "commit", "-qam", "two")
    git(author, "push", "-q")


def test_updates_and_keeps_what_this_pc_learned(repos):
    author, friend = repos
    assert updater.update()["message"] == "already up to date"
    publish(author)
    t = friend / "lumberjack" / "assets" / "templates"
    (t / "eat.png").write_text("learned here")                         # a re-learned shipped template
    (t / "new_npc.png").write_text("learned here too")                 # a brand-new one
    r = updater.update()
    assert r["ok"] and r["updated"] and not r["addon_changed"], r
    assert (friend / "lumberjack" / "code.py").read_text() == "v = 2\n"
    assert (t / "eat.png").read_text() == "learned here" and (t / "new_npc.png").exists()


def test_learned_file_that_upstream_also_adds(repos):
    author, friend = repos
    (author / "lumberjack" / "assets" / "templates" / "cow.png").write_text("shipped cow")
    git(author, "add", "-A")
    git(author, "commit", "-qm", "cow")
    git(author, "push", "-q")
    (friend / "lumberjack" / "assets" / "templates" / "cow.png").write_text("my cow")
    r = updater.update()
    assert r["ok"] and r["updated"], r
    assert (friend / "lumberjack" / "assets" / "templates" / "cow.png").read_text() == "my cow"


def test_edited_code_is_never_overwritten(repos):
    author, friend = repos
    publish(author)
    (friend / "lumberjack" / "code.py").write_text("v = 'mine'\n")
    r = updater.update()
    assert not r["ok"] and "code.py" in r["message"]
    assert (friend / "lumberjack" / "code.py").read_text() == "v = 'mine'\n"


def test_follows_a_replaced_history_of_releases_only(repos):
    author, friend = repos
    for repo in (author,):
        git(repo, "commit", "-q", "--amend", "-m", "Grindstone v1")          # the published release
        git(repo, "push", "-q", "-f")
    git(friend, "fetch", "-q")
    git(friend, "reset", "-q", "--hard", "origin/main")                     # friend has release v1
    # the author replaces the published history with one fresh commit
    git(author, "checkout", "-q", "--orphan", "fresh")
    (author / "lumberjack" / "code.py").write_text("v = 3\n")
    git(author, "add", "-A")
    git(author, "commit", "-q", "-m", "Grindstone v2")
    git(author, "push", "-q", "-f", "origin", "fresh:main")
    (friend / "lumberjack" / "assets" / "templates" / "eat.png").write_text("learned here")
    r = updater.update()
    assert r["ok"] and r["updated"], r
    assert (friend / "lumberjack" / "code.py").read_text() == "v = 3\n"
    assert (friend / "lumberjack" / "assets" / "templates" / "eat.png").read_text() == "learned here"


def test_a_replaced_history_never_drops_local_work(repos):
    author, friend = repos
    (friend / "lumberjack" / "mine.py").write_text("x = 1\n")
    git(friend, "add", "-A")
    git(friend, "commit", "-q", "-m", "my own change")
    git(author, "checkout", "-q", "--orphan", "fresh")
    git(author, "commit", "-q", "-m", "Grindstone v2")
    git(author, "push", "-q", "-f", "origin", "fresh:main")
    r = updater.update()
    assert not r["ok"] and (friend / "lumberjack" / "mine.py").exists()
