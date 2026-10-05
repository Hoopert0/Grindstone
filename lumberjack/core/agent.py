"""Build the Java input agent and attach it to the running 2009scape client."""
import glob
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from lumberjack import procs

AGENT_DIR = Path(__file__).resolve().parents[1] / "agent"
JAR = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".venvs" / "lumberjack" / "lumberjack-agent.jar"  # outside the redirected folder
PORT = 47001


def find_jdk_bin():
    """Directory holding javac/jar/java from a full JDK (the game's bundled JRE can't compile or attach)."""
    candidates = []
    if os.environ.get("JAVA_HOME"):
        candidates.append(Path(os.environ["JAVA_HOME"]) / "bin")
    javac = shutil.which("javac")
    if javac:
        candidates.append(Path(javac).parent)
    for pattern in (r"C:\Program Files\Eclipse Adoptium\jdk-*\bin", r"C:\Program Files\Java\jdk*\bin",
                    r"C:\Program Files\Microsoft\jdk-*\bin"):
        candidates += [Path(p) for p in sorted(glob.glob(pattern), reverse=True)]
    for c in candidates:
        if (c / "javac.exe").exists() and (c / "jar.exe").exists():
            return c
    raise RuntimeError("No JDK found. Install one:  winget install EclipseAdoptium.Temurin.11.JDK")


def build(force=False):
    src = sorted((AGENT_DIR / "src").rglob("*.java"))
    if JAR.exists() and not force and all(JAR.stat().st_mtime > s.stat().st_mtime for s in src):
        return JAR
    jdk = find_jdk_bin()
    classes = JAR.parent / "agent-classes"
    shutil.rmtree(classes, ignore_errors=True)
    classes.mkdir(parents=True)
    # stubs/ only lets the game-thread hook compile against the game's plugin class; it isn't packaged
    procs.run([str(jdk / "javac.exe"), "--release", "11", "-sourcepath", str(AGENT_DIR / "stubs"),
               "-implicit:none", "-d", str(classes), *map(str, src)], check=True)
    procs.run([str(jdk / "jar.exe"), "cfm", str(JAR), str(AGENT_DIR / "MANIFEST.MF"), "-C", str(classes), "."], check=True)
    return JAR


TYPE_PORT = PORT + 1
TYPE_JAR = JAR.with_name("lumberjack-typeagent.jar")


def ensure_type_agent():
    """The typing add-on (chat commands need spaces). New game launches start it with the
    main agent; a client launched before it existed gets it attached here."""
    if is_running(TYPE_PORT):
        return
    jdk = find_jdk_bin()
    classes = TYPE_JAR.parent / "typeagent-classes"
    shutil.rmtree(classes, ignore_errors=True)
    classes.mkdir(parents=True)
    src = [str(AGENT_DIR / "src" / "lumberjack" / n) for n in ("TypeAgent.java", "Attach.java")]
    procs.run([str(jdk / "javac.exe"), "--release", "11", "-d", str(classes), *src], check=True)
    manifest = TYPE_JAR.parent / "typeagent-manifest.mf"
    manifest.write_text("Manifest-Version: 1.0\nAgent-Class: lumberjack.TypeAgent\n")
    procs.run([str(jdk / "jar.exe"), "cfm", str(TYPE_JAR), str(manifest), "-C", str(classes), "."], check=True)
    pid = find_client_pid()
    procs.run([str(jdk / "java.exe"), "-cp", str(TYPE_JAR), "lumberjack.Attach", str(pid), str(TYPE_JAR),
                    str(TYPE_PORT)], check=True)
    import time
    for _ in range(20):
        if is_running(TYPE_PORT):
            return
        time.sleep(0.1)
    raise RuntimeError("Typing add-on attached but not answering")


def find_client_pid():
    """PID of the 2009scape *client* JVM (not the singleplayer server JVM)."""
    return find_client()[0]


def find_client():
    """(pid, command line) of the 2009scape *client* JVM (not the singleplayer server JVM)."""
    out = procs.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name='java.exe' or Name='javaw.exe'\" | ForEach-Object { \"$($_.ProcessId)|$($_.CommandLine)\" }"],
        capture_output=True, text=True, check=True).stdout
    for line in out.splitlines():
        pid, _, cmd = line.partition("|")
        if "server.jar" not in cmd and ("2009scape.jar" in cmd or "client.jar" in cmd):
            return int(pid), cmd
    raise RuntimeError("The game isn't running (it was closed or crashed) - start it with the Grindstone icon.")


def is_running(port=PORT):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5) as s:
            s.sendall(b"ping\n")
            return s.recv(64).startswith(b"ok")
    except OSError:
        return False


def ensure_agent(port=PORT):
    """Make sure the agent is live in the client; build + attach it if not."""
    if is_running(port):
        return
    try:
        jar = build()
    except (subprocess.CalledProcessError, RuntimeError):
        if not JAR.exists():
            raise
        jar = JAR  # the game has the current jar open (locked) - it's the one it runs anyway
    jdk = find_jdk_bin()
    pid, cmd = find_client()
    if "-javaagent" in cmd:                  # Grindstone started it: the add-on is coming, the game is loading
        for _ in range(120):
            if is_running(port):
                return
            time.sleep(0.5)
        raise RuntimeError("The game is running but its add-on isn't answering - close the game and start it "
                           "again with the Grindstone icon (and send logs\\client.log if it keeps happening).")
    r = procs.run([str(jdk / "java.exe"), "-cp", str(jar), "lumberjack.Attach", str(pid), str(jar), str(port)],
                  capture_output=True, text=True)
    if r.returncode != 0:
        lines = (r.stderr or r.stdout or "").strip().splitlines()
        why = [next((l for l in lines if "Exception" in l or "Error" in l), lines[0] if lines else "")]
        raise RuntimeError("The game wasn't started by Grindstone, so its add-on isn't loaded (and can't be added "
                           "now). Close the game window - and the 2009scape launcher's singleplayer - then start "
                           "the game with the Grindstone icon." + (f" [{why[0][:160]}]" if why[0] else ""))
    for _ in range(20):
        if is_running(port):
            return
        time.sleep(0.1)
    raise RuntimeError("Agent attached but not answering on port %d" % port)
