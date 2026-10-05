package plugin;

/** Compile-time stand-in for the client's plugin.Plugin (never packaged - the game's own class is used). */
public abstract class Plugin {
    public void LateDraw(long timeDelta) {}

    public boolean OnPluginsReloaded() {
        return false;
    }
}
