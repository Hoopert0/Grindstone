package lumberjack;

import java.awt.Canvas;
import java.awt.Component;
import java.awt.Container;
import java.awt.EventQueue;
import java.awt.Frame;
import java.awt.event.InputEvent;
import java.awt.event.KeyEvent;
import java.awt.event.KeyListener;
import java.awt.event.MouseEvent;
import java.awt.event.MouseListener;
import java.awt.event.MouseMotionListener;
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.PrintWriter;
import java.lang.instrument.Instrumentation;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;

/**
 * Virtual mouse/keyboard for the local 2009scape client.
 *
 * Loaded into the client JVM (dynamic attach). Listens on 127.0.0.1 only and feeds
 * synthetic AWT events straight to the game canvas's listeners - the real OS cursor and
 * keyboard are never touched, and it works with the window covered or unfocused.
 *
 * Protocol: one command per line, reply "ok" or "err <msg>".
 *   ping | move x y | press x y button | release x y button | key_down vk | key_up vk | type char
 *   state probe|menu|npcs [name]|inv|skills|player|ground [radius]|locs [radius] [name]|camera [yaw pitch zoom]|minimap   (one line of JSON after "ok ", see GameState)
 */
public final class InputAgent {
    private static final int DEFAULT_PORT = 47001;
    private static volatile boolean started = false;
    private static Canvas canvas;
    private static int modifiers = 0;   // currently held mouse buttons (extended modifiers)
    private static int keyMods = 0;   // SHIFT_DOWN_MASK while the bot holds Shift (shift-click)
    private static boolean inside = false;

    public static void agentmain(String args, Instrumentation inst) { start(args); }
    public static void premain(String args, Instrumentation inst) { start(args); }

    private static synchronized void start(String args) {
        if (started) return;
        started = true;
        int port = (args != null && !args.isEmpty()) ? Integer.parseInt(args.trim()) : DEFAULT_PORT;
        Thread t = new Thread(() -> serve(port), "lumberjack-input-agent");
        t.setDaemon(true);
        t.start();
        TypeAgent.start(String.valueOf(port + 1));   // text typing (spaces etc.) on the next port
    }

    private static void serve(int port) {
        try (ServerSocket server = new ServerSocket(port, 8, InetAddress.getLoopbackAddress())) {
            while (true) {
                Socket s = server.accept();
                // one thread per client, so a bot holding a connection doesn't lock out tools
                Thread t = new Thread(() -> client(s), "lumberjack-input-client");
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
            sock.setTcpNoDelay(true);
            String line;
            while ((line = in.readLine()) != null) {
                line = line.trim();
                if (line.equals("frame")) {          // binary reply: the game's own frame, no screen grab
                    out.flush();
                    sendFrame(sock.getOutputStream());
                    continue;
                }
                out.println(handle(line));
            }
        } catch (Exception e) {
            // client went away
        }
    }

    private static final Object INPUT_LOCK = new Object();
    private static final long EDT_TIMEOUT_MS = 3000;

    /** "ok <w> <h>" + w*h*4 bytes (B, G, R, 0 per pixel), or "err ..." - read straight from the
     *  game's frame buffer: no PrintWindow, no repaint, nothing that waits on the UI thread. */
    private static void sendFrame(java.io.OutputStream os) throws Exception {
        byte[] head;
        byte[] body = null;
        try {
            Object[] f = GameState.frame();
            body = (byte[]) f[2];
            head = ("ok " + f[0] + " " + f[1] + "\n").getBytes("UTF-8");
        } catch (Exception e) {
            head = ("err " + e + "\n").getBytes("UTF-8");
        }
        os.write(head);
        if (body != null) os.write(body);
        os.flush();
    }

    private static String handle(String line) {
        // game-data questions only read fields: no lock, no AWT - they answer even if the UI is stuck
        if (line.startsWith("state ")) {
            try {
                String[] p = line.split(" ");
                return "ok " + GameState.handle(p[1], p.length > 2 ? line.substring(line.indexOf(p[2], 6)) : null);
            } catch (Exception e) {
                return "err " + e;
            }
        }
        synchronized (INPUT_LOCK) {
            return handleInput(line);
        }
    }

    private static String handleInput(String line) {
        try {
            String[] p = line.split(" ");
            Canvas c = canvas();
            if (c == null) return "err no canvas";
            switch (p[0]) {
                case "ping":
                    return "ok " + c.getWidth() + "x" + c.getHeight();
                case "move":
                    mouseMove(c, i(p[1]), i(p[2]));
                    return "ok";
                case "press":
                    mouseButton(c, i(p[1]), i(p[2]), i(p[3]), true);
                    return "ok";
                case "release":
                    mouseButton(c, i(p[1]), i(p[2]), i(p[3]), false);
                    return "ok";
                case "key_down":
                    key(c, i(p[1]), true);
                    return "ok";
                case "key_up":
                    key(c, i(p[1]), false);
                    return "ok";
                case "type":
                    typeChar(c, p[1].charAt(0));
                    return "ok";
                default:
                    return "err unknown command";
            }
        } catch (Exception e) {
            return "err " + e;
        }
    }

    private static int i(String s) { return Integer.parseInt(s); }

    // ---- finding the game canvas -------------------------------------------------------
    private static Canvas canvas() {
        if (canvas != null && canvas.isDisplayable()) return canvas;
        canvas = null;
        for (Frame f : Frame.getFrames()) {
            Canvas found = findCanvas(f);
            if (found != null && (canvas == null || area(found) > area(canvas))) canvas = found;
        }
        return canvas;
    }

    private static int area(Component c) { return c.getWidth() * c.getHeight(); }

    private static Canvas findCanvas(Container root) {
        Canvas best = null;
        for (Component child : root.getComponents()) {
            Canvas cand = null;
            if (child instanceof Canvas && child.isShowing()) cand = (Canvas) child;
            else if (child instanceof Container) cand = findCanvas((Container) child);
            if (cand != null && (best == null || area(cand) > area(best))) best = cand;
        }
        return best;
    }

    // ---- event delivery: call the game's listeners directly on the EDT ------------------
    private static void onEdt(Runnable r) throws Exception {
        if (EventQueue.isDispatchThread()) {
            r.run();
            return;
        }
        // never wait forever: a stuck UI thread must not take the add-on (and the bot) with it
        java.util.concurrent.FutureTask<Void> task = new java.util.concurrent.FutureTask<>(r, null);
        EventQueue.invokeLater(task);
        try {
            task.get(EDT_TIMEOUT_MS, java.util.concurrent.TimeUnit.MILLISECONDS);
        } catch (java.util.concurrent.TimeoutException e) {
            throw new IllegalStateException("game UI busy (no answer in " + EDT_TIMEOUT_MS + " ms)");
        }
    }

    private static void mouseMove(Canvas c, int x, int y) throws Exception {
        onEdt(() -> {
            long now = System.currentTimeMillis();
            if (!inside) {
                MouseEvent enter = new MouseEvent(c, MouseEvent.MOUSE_ENTERED, now, modifiers | keyMods, x, y, 0, false);
                for (MouseListener l : c.getMouseListeners()) l.mouseEntered(enter);
                inside = true;
            }
            boolean dragging = modifiers != 0;
            MouseEvent e = new MouseEvent(c, dragging ? MouseEvent.MOUSE_DRAGGED : MouseEvent.MOUSE_MOVED,
                    now, modifiers | keyMods, x, y, 0, false);
            for (MouseMotionListener l : c.getMouseMotionListeners()) {
                if (dragging) l.mouseDragged(e); else l.mouseMoved(e);
            }
        });
    }

    private static void mouseButton(Canvas c, int x, int y, int button, boolean down) throws Exception {
        int awtButton = button == 3 ? MouseEvent.BUTTON3 : button == 2 ? MouseEvent.BUTTON2 : MouseEvent.BUTTON1;
        int mask = button == 3 ? InputEvent.BUTTON3_DOWN_MASK : button == 2 ? InputEvent.BUTTON2_DOWN_MASK : InputEvent.BUTTON1_DOWN_MASK;
        onEdt(() -> {
            long now = System.currentTimeMillis();
            if (down) {
                modifiers |= mask;
                MouseEvent e = new MouseEvent(c, MouseEvent.MOUSE_PRESSED, now, modifiers | keyMods, x, y, 1, button == 3, awtButton);
                for (MouseListener l : c.getMouseListeners()) l.mousePressed(e);
            } else {
                modifiers &= ~mask;
                MouseEvent e = new MouseEvent(c, MouseEvent.MOUSE_RELEASED, now, modifiers | keyMods, x, y, 1, false, awtButton);
                for (MouseListener l : c.getMouseListeners()) l.mouseReleased(e);
                MouseEvent click = new MouseEvent(c, MouseEvent.MOUSE_CLICKED, now, modifiers | keyMods, x, y, 1, false, awtButton);
                for (MouseListener l : c.getMouseListeners()) l.mouseClicked(click);
            }
        });
    }

    private static void key(Canvas c, int vk, boolean down) throws Exception {
        onEdt(() -> {
            if (vk == KeyEvent.VK_SHIFT) keyMods = down ? InputEvent.SHIFT_DOWN_MASK : 0;
            KeyEvent e = new KeyEvent(c, down ? KeyEvent.KEY_PRESSED : KeyEvent.KEY_RELEASED,
                    System.currentTimeMillis(), keyMods, vk, KeyEvent.CHAR_UNDEFINED);
            for (KeyListener l : c.getKeyListeners()) {
                if (down) l.keyPressed(e); else l.keyReleased(e);
            }
        });
    }

    private static void typeChar(Canvas c, char ch) throws Exception {
        onEdt(() -> {
            KeyEvent e = new KeyEvent(c, KeyEvent.KEY_TYPED, System.currentTimeMillis(), 0, KeyEvent.VK_UNDEFINED, ch);
            for (KeyListener l : c.getKeyListeners()) l.keyTyped(e);
        });
    }
}
