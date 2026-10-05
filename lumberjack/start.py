"""One-click start: everything the launcher + 'lumberjack client' + 'lumberjack panel' used to need.

    lumberjack            (or double-click the Grindstone desktop icon)

  1. download the latest character from GitHub and mark the save "in use" on this PC
  2. start the singleplayer server in the background (no launcher, no extra client window)
  3. start the bot-ready client
  4. start the control panel and open it in the browser
When the game window is closed: stop the server and panel, upload the save, release the lock.
"""
import os
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

from lumberjack import savesync
from lumberjack.launch_client import GAME_DIR, JAVA, launch, server_up
from lumberjack import procs

SERVER_DIR = GAME_DIR / "singleplayer" / "game"
SERVER_JAR = SERVER_DIR / "server.jar"
SERVER_JAVA = JAVA.with_name("java.exe")  # same JRE and memory settings the launcher uses
PANEL_PORT = 8765
LOGS = savesync.REPO / "logs"
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW
DETACHED = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP


def java_processes():
    """[(pid, command line)] of every running java/javaw."""
    out = procs.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name like 'java%'\" | "
         "ForEach-Object { \"$($_.ProcessId)|$($_.CommandLine)\" }"],
        capture_output=True, text=True).stdout
    found = []
    for line in out.splitlines():
        pid, _, cmd = line.partition("|")
        if pid.strip().isdigit():
            found.append((int(pid), cmd))
    return found


def port_open(port):
    try:
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
        return True
    except OSError:
        return False


def wait_for(check, seconds, what):
    end = time.time() + seconds
    while time.time() < end:
        if check():
            return True
        time.sleep(1)
    raise RuntimeError(f"{what} didn't come up within {seconds} s.")


def start_server():
    if not SERVER_JAR.exists():
        raise RuntimeError(f"Server not found at {SERVER_JAR}. Install 2009scape and start Singleplayer once from its launcher.")
    LOGS.mkdir(exist_ok=True)
    log = open(LOGS / "server.log", "w")
    p = subprocess.Popen([str(SERVER_JAVA), "-Xmx2G", "-Xms2G", "-jar", str(SERVER_JAR)],
                         cwd=str(SERVER_DIR), stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                         creationflags=NO_WINDOW | DETACHED)
    print("Starting the game server (takes up to a minute)...", flush=True)
    try:
        wait_for(lambda: server_up() or p.poll() is not None, 180, "The game server")
    except RuntimeError:
        p.kill()
        raise
    if p.poll() is not None:
        raise RuntimeError(f"The game server stopped while starting - see {LOGS / 'server.log'}")
    return p.pid


def start_panel(append=False):
    LOGS.mkdir(exist_ok=True)
    log = open(LOGS / "panel.log", "a" if append else "w")
    py = Path(sys.executable)
    pyw = py.with_name("pythonw.exe")
    p = subprocess.Popen([str(pyw if pyw.exists() else py), "-m", "lumberjack.web.server"],
                         cwd=str(savesync.REPO), stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                         creationflags=NO_WINDOW | DETACHED)
    wait_for(lambda: port_open(PANEL_PORT) or p.poll() is not None, 60, "The control panel")
    if p.poll() is not None:
        raise RuntimeError(f"The control panel stopped while starting - see {LOGS / 'panel.log'}")
    return p.pid


def start(force=False):
    clients = [(pid, cmd) for pid, cmd in java_processes() if "2009scape.jar" in cmd]
    if any("-javaagent" in cmd for _, cmd in clients):
        print("The game is already running.")
        if not port_open(PANEL_PORT):
            start_panel()
        webbrowser.open(f"http://127.0.0.1:{PANEL_PORT}")
        return
    if clients:
        raise RuntimeError("A 2009scape window from the launcher is open - the bot can't use that one. "
                           "Close it, then start Grindstone again.")

    if savesync.SAVE.exists():           # (only when the save is shared through the repo)
        print("Downloading your latest character...", flush=True)
    err = savesync.acquire(force=force)
    if err:
        raise RuntimeError(err)

    started = []  # pids to stop when the game closes
    try:
        if not server_up():
            started.append(start_server())
        print("Starting the game...", flush=True)
        client = launch()
        if not port_open(PANEL_PORT):
            started.append(start_panel())
    except Exception:
        for pid in started:
            kill(pid)
        savesync.release()
        raise
    webbrowser.open(f"http://127.0.0.1:{PANEL_PORT}")

    py = Path(sys.executable)
    pyw = py.with_name("pythonw.exe")
    subprocess.Popen([str(pyw if pyw.exists() else py), "-m", "lumberjack.start", "watch", str(client.pid),
                      *map(str, started)], cwd=str(savesync.REPO), creationflags=DETACHED)


def kill(pid):
    procs.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)


RESTARTS_PER_HOUR = 3


def stop_bot():
    """Ask the panel to stop the bot (the game was closed on purpose). Quiet if it isn't up."""
    import urllib.request
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{PANEL_PORT}/api/stop", data=b"", method="POST")
        urllib.request.urlopen(req, timeout=3).close()
    except Exception:
        pass


def watch(client_pid, stop_pids):
    """Runs in the background: after the game closes, stop what we started and upload the save.
    Only a real crash restarts the game - Java wrote a crash report, or the panel's freeze watchdog
    ended a frozen game (at most RESTARTS_PER_HOUR); log back in and the bot carries on. Closing
    the game yourself always quits (and stops a running bot)."""
    restarts = []
    while True:
        code = savesync.wait_for_exit(client_pid)
        crashed = ((LOGS / f"game_crash_{client_pid}.log").exists()     # Java's own crash report
                   or (LOGS / f"game_frozen_{client_pid}.log").exists())  # the panel's freeze watchdog
        recent = [t for t in restarts if time.time() - t < 3600]
        if crashed and len(recent) < RESTARTS_PER_HOUR:
            with open(LOGS / "client.log", "a", encoding="utf-8") as f:
                f.write(f"\n===== the game crashed or froze (exit code {code}) - starting it again =====\n")
            restarts = recent + [time.time()]
            time.sleep(5)
            client_pid = launch().pid
            continue
        stop_bot()                           # closed on purpose (or crashing over and over): quit
        break
    time.sleep(30)  # give the server time to write the character after logout
    for pid in stop_pids:
        kill(pid)
    time.sleep(2)
    savesync.release()


def restart_panel(old_pid):
    """After the panel updated itself: wait for the old one to exit, start the new code. A plan
    that was running resumes by itself (web.server.resume_plan)."""
    savesync.wait_for_exit(old_pid)
    for _ in range(30):                     # the old one's port can take a moment to free up
        if not port_open(PANEL_PORT):
            break
        time.sleep(1)
    start_panel(append=True)


ICON = savesync.REPO / "lumberjack" / "assets" / "grindstone.ico"


def rename_old_icon():
    """The desktop icon was called 'Lumberjack' before the rename: call it 'Grindstone' now, and
    give it the Grindstone picture (once - a marker file remembers)."""
    home = Path(os.environ.get("USERPROFILE", Path.home()))
    for desktop in (home / "Desktop", home / "OneDrive" / "Desktop"):
        old, new = desktop / "Lumberjack.lnk", desktop / "Grindstone.lnk"
        try:
            if old.exists() and not new.exists():
                old.rename(new)
        except OSError:
            pass
        done = savesync.REPO / "lumberjack" / "configs" / "icon_set_v2"     # (v2: the anvil icon)
        if new.exists() and ICON.exists() and not done.exists():
            ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{new}');"
                  f"$s.IconLocation='{ICON}';$s.Save()")
            try:
                if procs.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=20).returncode == 0:
                    done.parent.mkdir(parents=True, exist_ok=True)
                    done.write_text("1")
            except Exception:
                pass


def update_first():
    """Before starting anything: get the newest version from GitHub. When the code changed, run
    the new start-up (this process has the old code loaded) and return its exit code; else None.
    Skipped with --no-update, and while the panel is already up (the game's running)."""
    from lumberjack import updater
    print("Checking for updates...", flush=True)
    try:
        r = updater.update()
    except Exception as e:                  # never let an update problem stop the game starting
        print(f"  (update check failed: {e})")
        return None
    print(f"  {r['message']}", flush=True)
    if not r["updated"]:
        return None
    print("Starting the new version...", flush=True)
    return subprocess.run([sys.executable, "-m", "lumberjack.start", "--updated",
                           *[a for a in sys.argv[1:] if a != "--updated"]]).returncode


def use_gh_signin():
    """Let git reuse the GitHub CLI's sign-in (gh auth login), so it never asks again. Once per PC."""
    import shutil
    done = savesync.REPO / "lumberjack" / "configs" / "gh_git_setup"
    if done.exists() or not shutil.which("gh"):
        return
    try:
        if procs.run(["gh", "auth", "status"], capture_output=True, timeout=20).returncode == 0:
            procs.run(["gh", "auth", "setup-git"], capture_output=True, timeout=20)
            done.parent.mkdir(parents=True, exist_ok=True)
            done.write_text("1")
    except Exception:
        pass


def main():
    os.chdir(savesync.REPO)
    if not (len(sys.argv) > 1 and sys.argv[1] in ("watch", "restart-panel")):
        procs.INTERACTIVE = True             # this console may ask for a GitHub sign-in (once)
        use_gh_signin()
        try:
            from lumberjack import site_fixes      # (only in this setup's own copy)
            site_fixes.run()
        except ImportError:
            pass
    if len(sys.argv) > 1 and sys.argv[1] == "restart-panel":
        restart_panel(int(sys.argv[2]))
        return
    if len(sys.argv) > 1 and sys.argv[1] == "watch":
        watch(int(sys.argv[2]), [int(p) for p in sys.argv[3:]])
        return
    rename_old_icon()
    if not {"--no-update", "--updated"} & set(sys.argv) and not port_open(PANEL_PORT):
        relaunched = update_first()
        if relaunched is not None:
            sys.exit(relaunched)
    try:
        start(force="--force" in sys.argv)
    except Exception as e:
        print(f"\nCouldn't start: {e}")
        input("Press Enter to close this window.")
        sys.exit(1)
    from lumberjack import version
    print(f"\nAll running - Grindstone {version.label()}. Log in, pick SD, then use the control panel in your browser.")
    print("Closing the game window uploads your save and stops the server.")
    time.sleep(8)


if __name__ == "__main__":
    main()
