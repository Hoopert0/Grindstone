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


def test_watch_restarts_a_game_that_died_mid_run(tmp_path, monkeypatch):
    """Error exit while a run is on -> start the game again (the server too if it went down);
    a clean exit (closed on purpose) -> quit and stop the bot."""
    import types
    from lumberjack import start
    monkeypatch.setattr(start, "LOGS", tmp_path)
    marker = tmp_path / "plan_running"
    marker.write_text("{}")
    monkeypatch.setattr(start, "BOT_RUNNING", marker)
    exits = iter([(100, 1), (101, 0)])                    # crashed with code 1, then closed normally
    current = {"pid": 100}

    def wait(pid, timeout_s=None):
        p, code = next(exits)
        assert p == pid
        return code
    monkeypatch.setattr(start.savesync, "wait_for_exit", wait)
    monkeypatch.setattr(start.savesync, "release", lambda: True)
    monkeypatch.setattr(start.time, "sleep", lambda s: None)
    launched, servers, stopped, killed = [], [], [], []
    monkeypatch.setattr(start, "launch", lambda: launched.append(1) or types.SimpleNamespace(pid=101))
    monkeypatch.setattr(start, "server_up", lambda: False)
    monkeypatch.setattr(start, "start_server", lambda: servers.append(1) or 555)
    monkeypatch.setattr(start, "stop_bot", lambda: stopped.append(1))
    monkeypatch.setattr(start, "kill", lambda pid: killed.append(pid))
    start.watch(current["pid"], [7])
    assert launched == [1] and servers == [1] and stopped == [1] and sorted(killed) == [7, 555]


def test_watch_restarts_a_panel_that_stopped_mid_run(tmp_path, monkeypatch):
    from lumberjack import start
    monkeypatch.setattr(start, "LOGS", tmp_path)
    marker = tmp_path / "plan_running"
    marker.write_text("{}")
    monkeypatch.setattr(start, "BOT_RUNNING", marker)
    seq = iter([start.savesync.STILL_RUNNING] * 3 + [0])     # the panel is down for 2 looks, then the game closes
    monkeypatch.setattr(start.savesync, "wait_for_exit", lambda pid, timeout_s=None: next(seq))
    monkeypatch.setattr(start.savesync, "release", lambda: True)
    monkeypatch.setattr(start.time, "sleep", lambda s: None)
    ports = iter([False, False, True])
    monkeypatch.setattr(start, "port_open", lambda port: next(ports))
    panels = []
    monkeypatch.setattr(start, "start_panel", lambda append=False: panels.append(append) or 9)
    monkeypatch.setattr(start, "stop_bot", lambda: None)
    monkeypatch.setattr(start, "kill", lambda pid: None)
    start.watch(100, [])
    assert panels == [True]
