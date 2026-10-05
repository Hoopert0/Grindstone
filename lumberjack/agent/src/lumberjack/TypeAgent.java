package lumberjack;

import java.awt.Canvas;
import java.awt.Component;
import java.awt.Container;
import java.awt.EventQueue;
import java.awt.Frame;
import java.awt.event.KeyEvent;
import java.awt.event.KeyListener;
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.PrintWriter;
import java.lang.instrument.Instrumentation;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;

/**
 * Types arbitrary characters (incl. space) into the game canvas: "typecode <int>" sends a
 * KEY_TYPED event with that character. Separate class so it can be attached to a client
 * that already runs an older InputAgent. Listens on 127.0.0.1 only (default port 47002).
 */
public final class TypeAgent {
    private static volatile boolean started = false;

    public static void agentmain(String args, Instrumentation inst) { start(args); }
    public static void premain(String args, Instrumentation inst) { start(args); }

    static synchronized void start(String args) {
        if (started) return;
        started = true;
        int port = (args != null && !args.isEmpty()) ? Integer.parseInt(args.trim()) : 47002;
        Thread t = new Thread(() -> serve(port), "lumberjack-type-agent");
        t.setDaemon(true);
        t.start();
    }

    private static void serve(int port) {
        try (ServerSocket server = new ServerSocket(port, 4, InetAddress.getLoopbackAddress())) {
            while (true) {
                Socket s = server.accept();
                Thread t = new Thread(() -> client(s), "lumberjack-type-client");
                t.setDaemon(true);
                t.start();
            }
        } catch (Exception e) {
            e.printStackTrace();
        }
    }

    private static void client(Socket s) {
        try (Socket sock = s;
             BufferedReader in = new BufferedReader(new InputStreamReader(sock.getInputStream()));
             PrintWriter out = new PrintWriter(sock.getOutputStream(), true)) {
            String line;
            while ((line = in.readLine()) != null) {
                out.println(handle(line.trim()));
            }
        } catch (Exception e) {
            // client went away
        }
    }

    private static synchronized String handle(String line) {
        try {
            String[] p = line.split(" ");
            if (p[0].equals("ping")) return "ok";
            if (!p[0].equals("typecode")) return "err unknown command";
            char ch = (char) Integer.parseInt(p[1]);
            Canvas c = canvas();
            if (c == null) return "err no canvas";
            Runnable r = () -> {
                KeyEvent e = new KeyEvent(c, KeyEvent.KEY_TYPED, System.currentTimeMillis(), 0, KeyEvent.VK_UNDEFINED, ch);
                for (KeyListener l : c.getKeyListeners()) l.keyTyped(e);
            };
            if (EventQueue.isDispatchThread()) {
                r.run();
            } else {                                  // never wait forever on a stuck UI thread
                java.util.concurrent.FutureTask<Void> task = new java.util.concurrent.FutureTask<>(r, null);
                EventQueue.invokeLater(task);
                task.get(3000, java.util.concurrent.TimeUnit.MILLISECONDS);
            }
            return "ok";
        } catch (Exception e) {
            return "err " + e;
        }
    }

    private static Canvas canvas() {
        Canvas best = null;
        for (Frame f : Frame.getFrames()) {
            Canvas c = find(f);
            if (c != null && (best == null || c.getWidth() * c.getHeight() > best.getWidth() * best.getHeight())) best = c;
        }
        return best;
    }

    private static Canvas find(Container root) {
        Canvas best = null;
        for (Component child : root.getComponents()) {
            Canvas cand = null;
            if (child instanceof Canvas && child.isShowing()) cand = (Canvas) child;
            else if (child instanceof Container) cand = find((Container) child);
            if (cand != null && (best == null || cand.getWidth() * cand.getHeight() > best.getWidth() * best.getHeight())) best = cand;
        }
        return best;
    }
}
