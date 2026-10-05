package lumberjack;

import java.lang.reflect.Field;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.Callable;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.FutureTask;
import java.util.concurrent.TimeUnit;

/**
 * Runs lookups on the game's own thread. Several client lookups (type caches, the interface
 * table) quietly change internal state, and doing that from the add-on's thread while the game
 * uses the same structures froze and then crashed the game. The client's plugin hook LateDraw()
 * runs at the end of every frame on the game thread, so queued work runs there instead.
 */
final class GameThread extends plugin.Plugin {
    private static final ConcurrentLinkedQueue<FutureTask<?>> QUEUE = new ConcurrentLinkedQueue<>();
    private static volatile boolean registered;
    private static volatile long lastRun;
    private static final long RUNNING_MAX_MS = 8000;   // a started lookup may take this much longer

    private GameThread() {}

    @Override
    public void LateDraw(long timeDelta) {
        lastRun = System.nanoTime();
        long stop = lastRun + 40_000_000L;          // never hold a frame up for long
        FutureTask<?> t;
        while (System.nanoTime() < stop && (t = QUEUE.poll()) != null) {
            t.run();                                // FutureTask keeps any exception for the caller
        }
    }

    @Override
    public boolean OnPluginsReloaded() {
        return true;                                // stay registered through ::reloadplugins / resizes
    }

    /** Hook into the client; false if this client has no plugin system. */
    @SuppressWarnings("unchecked")
    static synchronized boolean install() {
        if (registered) return true;
        try {
            ClassLoader cl = ClassLoader.getSystemClassLoader();
            Class<?> info = Class.forName("plugin.PluginInfo", true, cl);
            Object meta = info.getConstructor(String.class, String.class, double.class)
                              .newInstance("Grindstone", "Grindstone game-thread lookups", 1.0);
            Field f = Class.forName("plugin.PluginRepository", true, cl).getDeclaredField("loadedPlugins");
            f.setAccessible(true);
            // copy-on-write swap: the game may be walking the old map right now, so never change it
            Map<Object, Object> old = (Map<Object, Object>) f.get(null);
            HashMap<Object, Object> next = new HashMap<>(old);
            next.put(meta, new GameThread());
            f.set(null, next);
            registered = true;
        } catch (Throwable e) {
            System.err.println("[lumberjack] game-thread hook unavailable: " + e);
        }
        return registered;
    }

    /** The hook is registered but the client never runs it (a client without LateDraw). */
    static final class NoHook extends RuntimeException {
        NoHook() {
            super("the game never ran the add-on's per-frame hook");
        }
    }

    static boolean ready() {
        return registered || install();
    }

    /** Run `work` on the game thread and wait for it (the game frozen or not drawing = timeout). */
    static <T> T call(Callable<T> work, long timeoutMs) throws Exception {
        java.util.concurrent.atomic.AtomicBoolean started = new java.util.concurrent.atomic.AtomicBoolean();
        FutureTask<T> t = new FutureTask<>(() -> {
            started.set(true);
            return work.call();
        });
        QUEUE.add(t);
        try {
            try {
                return t.get(timeoutMs, TimeUnit.MILLISECONDS);
            } catch (java.util.concurrent.TimeoutException e) {
                if (!started.get()) throw e;
                return t.get(RUNNING_MAX_MS, TimeUnit.MILLISECONDS);   // a big scan under way: let it finish
            }
        } catch (java.util.concurrent.TimeoutException e) {
            t.cancel(false);                        // not run yet = never runs
            if (lastRun == 0) throw new NoHook();   // this client never calls LateDraw
            throw new IllegalStateException("game busy (no frame drawn in " + timeoutMs + " ms)");
        } catch (java.util.concurrent.ExecutionException e) {
            Throwable c = e.getCause();
            if (c instanceof Exception) throw (Exception) c;
            throw new RuntimeException(c);
        }
    }
}
