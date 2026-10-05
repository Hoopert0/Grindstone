"""Start-up checks."""

def test_incomplete_singleplayer_install_is_named(tmp_path, monkeypatch):
    from lumberjack import start
    game = tmp_path / "game"
    (game / "worldprops").mkdir(parents=True)
    (game / "server.jar").write_text("x")
    monkeypatch.setattr(start, "SERVER_DIR", game)
    monkeypatch.setattr(start, "SERVER_JAR", game / "server.jar")
    assert "worldprops/default.conf" in start.install_problem()
    (game / "worldprops" / "default.conf").write_text("")
    assert start.install_problem() is None
