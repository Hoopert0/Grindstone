"""Start the 2009scape client set up for the bot.

    %USERPROFILE%\\.venvs\\lumberjack\\Scripts\\python.exe -m lumberjack.launch_client

Start Singleplayer from the 2009scape launcher first (that runs the local server), close
the client window it opens, then run this. Differences from the launcher's own client:
  * -Dsun.java2d.d3d=false  draw with plain GDI instead of Direct3D. Capturing a Direct3D
                            window with PrintWindow can wedge the game's render thread
                            (it froze once inside D3DRenderQueue.flushBuffer).
  * -javaagent              the input agent is loaded at startup - no attach step needed.
"""
import os
import socket
import subprocess
import sys
from pathlib import Path

from lumberjack.core.agent import PORT, build

GAME_DIR = Path(os.environ.get("LUMBERJACK_GAME_DIR", Path.home() / "2009scape"))
JAVA = GAME_DIR / "jre11" / "bin" / "javaw.exe"
CLIENT_JAR = GAME_DIR / "2009scape.jar"
SERVER_PORT = 43595  # the singleplayer server only listens here (login + cache)


def server_up():
    try:
        socket.create_connection(("127.0.0.1", SERVER_PORT), timeout=1).close()
        return True
    except OSError:
        return False


def launch():
    """Start the bot-ready client. Returns the client's Popen."""
    jar = build()
    logs = Path(__file__).resolve().parents[1] / "logs"
    logs.mkdir(exist_ok=True)
    cmd = [
        str(JAVA),
        f"-javaagent:{jar}={PORT}",
        f"-XX:ErrorFile={logs}/game_crash_%p.log",      # a hard crash leaves its report here
        "-Dsun.java2d.d3d=false",
        "-Dsun.java2d.uiScale=1",
        "-DclientFps=0",
        f"-DclientHomeOverride={GAME_DIR}/",
        "-jar", str(CLIENT_JAR),
    ]
    out = open(logs / "client.log", "a", encoding="utf-8", errors="replace")   # the game's own messages
    out.write(f"\n===== game started {__import__('time').strftime('%Y-%m-%d %H:%M:%S')} =====\n")
    out.flush()
    return subprocess.Popen(cmd, cwd=str(GAME_DIR), stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                            creationflags=subprocess.DETACHED_PROCESS)


def main():
    from lumberjack import savesync
    if not server_up():
        sys.exit("The singleplayer server isn't running. Start Singleplayer from the 2009scape launcher first.")
    # fetch the latest character from GitHub before logging in (the server reads it at login)
    err = savesync.acquire(force="--force" in sys.argv)
    if err:
        sys.exit(err)
    client = launch()
    if savesync.SAVE.exists():
        savesync.start_watcher(client.pid)   # uploads the save when you close the game
        print("Save will upload to GitHub automatically when you close the game.")
    print("Client starting (Direct3D off, input agent preloaded). Log in, then start the bot.")


if __name__ == "__main__":
    main()
