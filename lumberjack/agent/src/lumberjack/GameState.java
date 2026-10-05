package lumberjack;

import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.HashMap;
import java.util.Map;

/**
 * Read-only view of the 2009scape client's own data - menu entries, NPCs, the backpack,
 * skills, the player - so the bot doesn't have to recognise them from pixels.
 *
 * The client is the open-source RT4 client (gitlab.com/2009scape/rt4-client): its classes
 * keep readable names (rt4.MiniMenu, rt4.NpcList...). We read them by reflection, so this
 * add-on compiles without the game jar and a renamed field gives an error reply instead of
 * breaking the input add-on. Replies are one line of JSON.
 *
 * Coordinates: entities keep scene "fine" coords (128 per tile); world tile = scene tile +
 * Camera.originX/originY. Screen points come from the client's own projection
 * (plugin.api.API.CalculateSceneGraphScreenPosition), in canvas pixels.
 */
final class GameState {
    private static final int BACKPACK = 93;          // inventory id of the backpack
    private static final Map<String, Field> FIELDS = new HashMap<>();
    private static final Map<String, Class<?>> CLASSES = new HashMap<>();
    private static Method project;
    private static Method itemType, itemCount, objType;

    private GameState() {}

    // queries whose client calls change game state (type caches, the interface table, login)
    private static final java.util.Set<String> GAME_THREAD = new java.util.HashSet<>(
            java.util.Arrays.asList("inv", "ground", "locs", "widgets", "login", "camera", "varbit"));
    private static boolean noHook;

    static String handle(String what, String arg) throws Exception {
        if (GAME_THREAD.contains(what) && !noHook) {
            boolean ready;
            try {
                ready = GameThread.ready();
            } catch (Throwable e) {                 // client without the plugin classes
                ready = false;
            }
            if (ready) {
                try {
                    return GameThread.call(() -> query(what, arg), 3000);
                } catch (GameThread.NoHook e) {
                    System.err.println("[lumberjack] " + e.getMessage() + " - reading directly");
                }
            }
            noHook = true;
        }
        return query(what, arg);
    }

    private static String query(String what, String arg) throws Exception {
        switch (what) {
            case "probe": return probe();
            case "menu": return menu();
            case "npcs": return npcs(arg);
            case "inv": return inv(arg == null ? BACKPACK : Integer.parseInt(arg.trim()));
            case "skills": return skills();
            case "player": return player();
            case "ground": return ground(arg == null ? 15 : Integer.parseInt(arg.trim()));
            case "locs": return locs(arg);
            case "camera": return camera(arg);
            case "minimap": return minimap();
            case "widgets": return widgets(arg);
            case "login": return login(arg);
            case "loginstatus": return loginStatus();
            case "varp": return varp(arg);
            case "varbit": return varbit(arg);
            case "chat": return chat(arg == null ? 20 : Integer.parseInt(arg.trim()));
            case "tick": return "{\"loop\":" + statInt("rt4.client", "loop") + ",\"state\":" + statInt("rt4.client", "gameState") + "}";
            default: throw new IllegalArgumentException("unknown state '" + what + "'");
        }
    }

    /** Server-set varps (quest progress, settings): arg "80 281" -> {"80":4,"281":1000}. */
    private static String varp(String arg) throws Exception {
        int[] v = (int[]) stat("rt4.VarpDomain", "varp");
        StringBuilder b = new StringBuilder("{");
        boolean first = true;
        for (String part : (arg == null ? "" : arg.trim()).split("\\s+")) {
            if (part.isEmpty()) continue;
            int id = Integer.parseInt(part);
            if (id < 0 || id >= v.length) continue;
            b.append(first ? "" : ",").append('"').append(id).append("\":").append(v[id]);
            first = false;
        }
        return b.append('}').toString();
    }

    /** Varbits (farming patch states...): arg "780 708" -> {"780":8,"708":3}. Reads the varbit
     *  table (a cache): runs on the game thread. */
    private static String varbit(String arg) throws Exception {
        Method get = cls("rt4.VarpDomain").getMethod("getVarbit", int.class);
        StringBuilder b = new StringBuilder("{");
        boolean first = true;
        for (String part : (arg == null ? "" : arg.trim()).split("\\s+")) {
            if (part.isEmpty()) continue;
            int id = Integer.parseInt(part);
            b.append(first ? "" : ",").append('"').append(id).append("\":").append(get.invoke(null, id));
            first = false;
        }
        return b.append('}').toString();
    }

    /** The chat box, newest first: {"count": messages ever added, "lines": [{"type", "text"}]}.
     *  type 0 = the game's own messages ("You need a Mining level of 15..."). Plain array reads. */
    private static String chat(int n) throws Exception {
        Object[] msgs = (Object[]) stat("rt4.Chat", "messages");
        int[] types = (int[]) stat("rt4.Chat", "types");
        int count = statInt("rt4.Chat", "size");
        StringBuilder b = new StringBuilder("{\"count\":").append(count).append(",\"lines\":[");
        int k = Math.min(Math.max(n, 0), Math.min(msgs.length, count));
        for (int i = 0; i < k; i++) {
            b.append(i == 0 ? "" : ",").append("{\"type\":").append(types[i])
             .append(",\"text\":").append(q(text(msgs[i]))).append('}');
        }
        return b.append("]}").toString();
    }

    // ---- reflection helpers -------------------------------------------------------------
    private static Class<?> cls(String name) throws ClassNotFoundException {
        Class<?> c = CLASSES.get(name);
        if (c == null) {
            c = Class.forName(name, true, ClassLoader.getSystemClassLoader());
            CLASSES.put(name, c);
        }
        return c;
    }

    private static Field field(Class<?> c, String name) throws NoSuchFieldException {
        String key = c.getName() + "." + name;
        Field f = FIELDS.get(key);
        if (f == null) {
            for (Class<?> k = c; k != null && f == null; k = k.getSuperclass()) {
                try {
                    f = k.getDeclaredField(name);
                } catch (NoSuchFieldException e) {
                    // try the superclass (PathingEntity holds the positions)
                }
            }
            if (f == null) throw new NoSuchFieldException(key);
            f.setAccessible(true);
            FIELDS.put(key, f);
        }
        return f;
    }

    private static Object stat(String cls, String name) throws Exception {
        return field(cls(cls), name).get(null);
    }

    private static int statInt(String cls, String name) throws Exception {
        return ((Number) stat(cls, name)).intValue();
    }

    private static Object get(Object o, String name) throws Exception {
        return field(o.getClass(), name).get(o);
    }

    private static int getInt(Object o, String name) throws Exception {
        return ((Number) get(o, name)).intValue();
    }

    private static String text(Object jagString) {
        return jagString == null ? null : jagString.toString();
    }

    private static int[] screen(int xFine, int yFine, int zOffset) {
        try {
            if (project == null) {
                project = cls("plugin.api.API").getMethod("CalculateSceneGraphScreenPosition", int.class, int.class, int.class);
            }
            return (int[]) project.invoke(null, xFine, yFine, zOffset);
        } catch (Exception e) {
            return new int[]{-1, -1};
        }
    }

    // ---- JSON ---------------------------------------------------------------------------
    private static String q(String s) {
        if (s == null) return "null";
        StringBuilder b = new StringBuilder("\"");
        for (char ch : s.toCharArray()) {
            if (ch == '"' || ch == '\\') b.append('\\').append(ch);
            else if (ch < 0x20) b.append(String.format("\\u%04x", (int) ch));
            else b.append(ch);
        }
        return b.append('"').toString();
    }

    // ---- queries ------------------------------------------------------------------------
    /** Which parts of the client we can read (for the panel's check and the bot's fallback). */
    private static String probe() {
        String[][] checks = {
            {"rt4.MiniMenu", "size"}, {"rt4.MiniMenu", "ops"}, {"rt4.MiniMenu", "opBases"},
            {"rt4.NpcList", "npcs"}, {"rt4.NpcList", "ids"}, {"rt4.PlayerList", "self"},
            {"rt4.Camera", "originX"}, {"rt4.Camera", "cameraYaw"}, {"rt4.Inv", "objectContainerCache"},
            {"rt4.PlayerSkillXpTable", "baseLevels"}, {"rt4.Cs1ScriptRunner", "isMenuOpen"},
            {"rt4.InterfaceList", "menuX"}, {"rt4.client", "gameState"}, {"rt4.SceneGraph", "objStacks"}, {"rt4.SceneGraph", "tiles"}, {"rt4.MiniMap", "compassAngleOffset"},
        };
        StringBuilder missing = new StringBuilder();
        for (String[] c : checks) {
            try {
                stat(c[0], c[1]);
            } catch (Throwable e) {
                missing.append(missing.length() == 0 ? "" : ",").append(q(c[0] + "." + c[1]));
            }
        }
        boolean proj = screen(0, 0, 0) != null && project != null;
        if (!proj) missing.append(missing.length() == 0 ? "" : ",").append(q("plugin.api.API.CalculateSceneGraphScreenPosition"));
        return "{\"missing\":[" + missing + "]}";
    }

    /** Menu entries, top (the left-click option) first, plus whether the menu is open. */
    private static String menu() throws Exception {
        int size = statInt("rt4.MiniMenu", "size");
        Object[] ops = (Object[]) stat("rt4.MiniMenu", "ops");
        Object[] bases = (Object[]) stat("rt4.MiniMenu", "opBases");
        short[] actions = (short[]) stat("rt4.MiniMenu", "actions");
        StringBuilder b = new StringBuilder("{\"open\":");
        b.append(stat("rt4.Cs1ScriptRunner", "isMenuOpen"));
        b.append(",\"targeting\":").append(stat("rt4.MiniMenu", "isTargeting"));
        b.append(",\"x\":").append(statInt("rt4.InterfaceList", "menuX"));
        b.append(",\"y\":").append(statInt("rt4.InterfaceList", "menuY"));
        b.append(",\"w\":").append(statInt("rt4.InterfaceList", "menuWidth"));
        b.append(",\"h\":").append(statInt("rt4.InterfaceList", "menuHeight"));
        b.append(",\"entries\":[");
        boolean first = true;
        for (int i = size - 1; i >= 0; i--) {
            if (bases[i] == null && ops[i] == null) continue;
            if (!first) b.append(',');
            first = false;
            // row i of the open menu has its text baseline at (size - i - 1) * 15 + menuY + 31
            b.append("{\"verb\":").append(q(text(ops[i])))
             .append(",\"subject\":").append(q(text(bases[i])))
             .append(",\"action\":").append(actions[i])
             .append(",\"row\":").append(size - i - 1).append('}');
        }
        return b.append("]}").toString();
    }

    /** NPCs in the scene (optionally only names containing `filter`, case-insensitive). */
    private static String npcs(String filter) throws Exception {
        Object[] npcs = (Object[]) stat("rt4.NpcList", "npcs");
        int[] ids = (int[]) stat("rt4.NpcList", "ids");
        int count = statInt("rt4.NpcList", "size");
        int ox = statInt("rt4.Camera", "originX"), oy = statInt("rt4.Camera", "originY");
        Object self = stat("rt4.PlayerList", "self");
        int sx = self == null ? 0 : ((int[]) get(self, "movementQueueX"))[0];
        int sy = self == null ? 0 : ((int[]) get(self, "movementQueueY"))[0];
        String f = filter == null ? null : filter.toLowerCase();
        int loop = statInt("rt4.client", "loop");
        StringBuilder b = new StringBuilder("[");
        boolean first = true;
        for (int k = 0; k < count; k++) {
            Object npc = npcs[ids[k]];
            if (npc == null) continue;
            try {
                Object type = get(npc, "type");
                if (type == null) continue;
                String name = text(get(type, "name"));
                if (f != null && (name == null || !name.toLowerCase().contains(f))) continue;
                int xf = getInt(npc, "xFine"), yf = getInt(npc, "yFine");
                int tx = ((int[]) get(npc, "movementQueueX"))[0], ty = ((int[]) get(npc, "movementQueueY"))[0];
                int[] ground = screen(xf, yf, 0);
                int[] body = screen(xf, yf, 60);
                Object[] typeOps = (Object[]) get(type, "ops");
                StringBuilder o = new StringBuilder("[");
                for (int i = 0; i < typeOps.length; i++) {
                    o.append(i == 0 ? "" : ",").append(q(text(typeOps[i])));
                }
                o.append(']');
                if (!first) b.append(',');
                first = false;
                b.append("{\"index\":").append(ids[k])
                 .append(",\"name\":").append(q(name))
                 .append(",\"id\":").append(getInt(type, "id"))
                 .append(",\"ops\":").append(o)
                 .append(",\"tile\":[").append(tx + ox).append(',').append(ty + oy).append(']')
                 .append(",\"dist\":").append(Math.max(Math.abs(tx - sx), Math.abs(ty - sy)))
                 .append(",\"screen\":[").append(ground[0]).append(',').append(ground[1]).append(']')
                 .append(",\"body\":[").append(body[0]).append(',').append(body[1]).append(']')
                 .append(",\"anim\":").append(getInt(npc, "seqId"))
                 .append(",\"hp_bar\":").append(getInt(npc, "hitpointsBar"))   // 0-255 health left
                 .append(",\"in_combat\":").append(getInt(npc, "hitpointsBarVisibleUntil") > loop)
                 .append(",\"interacting\":").append(getInt(npc, "faceEntity"))  // >= 32768: player index + 32768
                 .append('}');
            } catch (Exception e) {
                // an NPC mid-update - skip it this time
            }
        }
        return b.append(']').toString();
    }

    /** An inventory's slots (93 = backpack, 28 slots; 94 = worn equipment): id (-1 = empty), count, name. */
    private static String inv(int invId) throws Exception {
        if (itemType == null) {
            Class<?> inv = cls("rt4.Inv");
            itemType = inv.getMethod("getItemType", int.class, int.class);
            itemCount = inv.getMethod("getItemCount", int.class, int.class);
            objType = cls("rt4.ObjTypeList").getMethod("get", int.class);
        }
        StringBuilder b = new StringBuilder("[");
        // backpack 28, equipment 14; others (bank = 95) up to the last item, scanning on until a
        // long run of empty slots
        int slots = invId == BACKPACK ? 28 : invId == 94 ? 14 : 0;
        if (slots == 0) {
            int empty = 0;
            for (int slot = 0; slot < 800 && empty < 60; slot++) {
                if ((Integer) itemType.invoke(null, invId, slot) >= 0) {
                    slots = slot + 1;
                    empty = 0;
                } else {
                    empty++;
                }
            }
        }
        for (int slot = 0; slot < slots; slot++) {
            int id = (Integer) itemType.invoke(null, invId, slot);
            int n = id < 0 ? 0 : (Integer) itemCount.invoke(null, invId, slot);
            String name = null;
            if (id >= 0) {
                try {
                    name = text(get(objType.invoke(null, id), "name"));
                } catch (Exception e) {
                    // unknown item type - keep the id
                }
            }
            b.append(slot == 0 ? "" : ",").append("{\"id\":").append(id).append(",\"count\":").append(n)
             .append(",\"name\":").append(q(name)).append('}');
        }
        return b.append(']').toString();
    }

    /** Items lying on the ground within `radius` tiles of us (same floor), nearest first. */
    private static String ground(int radius) throws Exception {
        Object self = stat("rt4.PlayerList", "self");
        if (self == null) return "[]";
        Object[][][] stacks = (Object[][][]) stat("rt4.SceneGraph", "objStacks");
        int plane = statInt("rt4.Player", "plane");
        int ox = statInt("rt4.Camera", "originX"), oy = statInt("rt4.Camera", "originY");
        int sx = ((int[]) get(self, "movementQueueX"))[0], sy = ((int[]) get(self, "movementQueueY"))[0];
        if (objType == null) objType = cls("rt4.ObjTypeList").getMethod("get", int.class);
        java.util.List<int[]> found = new java.util.ArrayList<>();   // x, y, id, amount, dist
        for (int x = Math.max(0, sx - radius); x <= Math.min(103, sx + radius); x++) {
            for (int y = Math.max(0, sy - radius); y <= Math.min(103, sy + radius); y++) {
                Object list = stacks[plane][x][y];
                if (list == null) continue;
                Object sentinel = get(list, "sentinel");
                int guard = 0;
                for (Object node = get(sentinel, "nextNode"); node != null && node != sentinel && guard++ < 128;
                     node = get(node, "nextNode")) {
                    try {
                        Object stack = get(node, "value");
                        found.add(new int[]{x, y, getInt(stack, "type"), getInt(stack, "amount"),
                                            Math.max(Math.abs(x - sx), Math.abs(y - sy))});
                    } catch (Exception e) {
                        // not an item node
                    }
                }
            }
        }
        found.sort((a, b) -> Integer.compare(a[4], b[4]));
        StringBuilder b = new StringBuilder("[");
        for (int i = 0; i < found.size(); i++) {
            int[] f = found.get(i);
            String name = null;
            try {
                name = text(get(objType.invoke(null, f[2]), "name"));
            } catch (Exception e) {
                // unknown item type
            }
            int[] p = screen(f[0] * 128 + 64, f[1] * 128 + 64, 0);
            b.append(i == 0 ? "" : ",").append("{\"id\":").append(f[2]).append(",\"count\":").append(f[3])
             .append(",\"name\":").append(q(name))
             .append(",\"tile\":[").append(f[0] + ox).append(',').append(f[1] + oy).append(']')
             .append(",\"dist\":").append(f[4])
             .append(",\"screen\":[").append(p[0]).append(',').append(p[1]).append("]}");
        }
        return b.append(']').toString();
    }

    private static Method locType;
    // loc id -> {id, name, ops json, width, length}: the client's own type cache is small, so a
    // scan of hundreds of objects kept decoding the same types (slow enough to time out).
    // Multi-locs (their look depends on a varbit) are resolved every time, not cached.
    private static final Map<Integer, String[]> LOC_INFO = new HashMap<>();
    private static final String[] NO_LOC = new String[0];

    private static String[] locInfo(int id) throws Exception {
        String[] cached = LOC_INFO.get(id);
        if (cached != null) return cached == NO_LOC ? null : cached;
        Object type = locType.invoke(null, id);
        boolean multi = type != null && get(type, "multiLocs") != null;
        String varbit = "-1";                    // what a multi-loc's look depends on (farming patches)
        if (multi) {
            varbit = String.valueOf(getInt(type, "multiLocVarbit"));
            type = type.getClass().getMethod("getMultiLoc").invoke(type);
        }
        String[] info = NO_LOC;
        if (type != null) {
            String name = text(get(type, "name"));
            if (name != null && !name.equals("null")) {
                Object[] ops = (Object[]) get(type, "ops");
                StringBuilder o = new StringBuilder("[");
                boolean firstOp = true;
                for (Object op : ops == null ? new Object[0] : ops) {
                    if (op == null) continue;
                    o.append(firstOp ? "" : ",").append(q(text(op)));
                    firstOp = false;
                }
                info = new String[]{String.valueOf(getInt(type, "id")), name, o.append(']').toString(),
                                    String.valueOf(getInt(type, "width")), String.valueOf(getInt(type, "length")),
                                    varbit};
            }
        }
        if (!multi && LOC_INFO.size() < 20000) LOC_INFO.put(id, info);
        return info == NO_LOC ? null : info;
    }

    /**
     * Scene objects ("locs": trees, rocks, fires, booths...) within a radius, nearest first.
     * arg: "[radius] [name filter]", e.g. "12 rocks". Big objects are listed once.
     * Scenery key bits: x 0-6, y 7-13, kind 29-30 (2 = loc), loc id = key >>> 32.
     */
    private static String locs(String arg) throws Exception {
        int radius = 15;
        String filter = null;
        if (arg != null) {
            String[] a = arg.trim().split(" ", 2);
            try {
                radius = Integer.parseInt(a[0]);
                filter = a.length > 1 ? a[1].toLowerCase() : null;
            } catch (NumberFormatException e) {
                filter = arg.trim().toLowerCase();
            }
        }
        Object self = stat("rt4.PlayerList", "self");
        if (self == null) return "[]";
        Object[][][] tiles = (Object[][][]) stat("rt4.SceneGraph", "tiles");
        int plane = statInt("rt4.Player", "plane");
        int ox = statInt("rt4.Camera", "originX"), oy = statInt("rt4.Camera", "originY");
        int sx = ((int[]) get(self, "movementQueueX"))[0], sy = ((int[]) get(self, "movementQueueY"))[0];
        if (locType == null) locType = cls("rt4.LocTypeList").getMethod("get", int.class);
        java.util.Set<Object> seen = java.util.Collections.newSetFromMap(new java.util.IdentityHashMap<>());
        java.util.List<String[]> rows = new java.util.ArrayList<>();   // dist (padded), json
        for (int x = Math.max(0, sx - radius); x <= Math.min(103, sx + radius); x++) {
            for (int y = Math.max(0, sy - radius); y <= Math.min(103, sy + radius); y++) {
                Object tile = tiles[plane][x][y];
                if (tile == null) continue;
                Object[] scenery = (Object[]) get(tile, "scenery");
                int len = getInt(tile, "sceneryLen");
                for (int i = 0; i < len && i < scenery.length; i++) {
                    Object sc = scenery[i];
                    if (sc == null || !seen.add(sc)) continue;
                    try {
                        long key = (Long) get(sc, "key");
                        if (((int) key >> 29 & 0x3) != 2) continue;
                        int id = (int) (key >>> 32) & Integer.MAX_VALUE;
                        String[] info = locInfo(id);       // {id, name, ops json, width, length}
                        if (info == null) continue;
                        String name = info[1];
                        if (filter != null && !name.toLowerCase().contains(filter)) continue;
                        int tx = (int) key & 0x7F, ty = (int) key >> 7 & 0x7F;
                        int xf = getInt(sc, "xFine"), yf = getInt(sc, "yFine");
                        int[] ground = screen(xf, yf, 0), body = screen(xf, yf, 100);
                        String o = info[2];
                        int dist = Math.max(Math.abs(tx - sx), Math.abs(ty - sy));
                        rows.add(new String[]{String.format("%05d", dist),
                            "{\"id\":" + info[0] + ",\"name\":" + q(name) + ",\"ops\":" + o
                            + ",\"tile\":[" + (tx + ox) + "," + (ty + oy) + "],\"dist\":" + dist
                            + ",\"size\":[" + info[3] + "," + info[4] + "]"
                            + (info.length > 5 && !"-1".equals(info[5]) ? ",\"varbit\":" + info[5] : "")
                            + ",\"screen\":[" + ground[0] + "," + ground[1] + "]"
                            + ",\"body\":[" + body[0] + "," + body[1] + "]}"});
                    } catch (Exception e) {
                        // half-loaded object - skip
                    }
                }
            }
        }
        rows.sort((a, b) -> a[0].compareTo(b[0]));
        StringBuilder b = new StringBuilder("[");
        for (int i = 0; i < rows.size(); i++) b.append(i == 0 ? "" : ",").append(rows.get(i)[1]);
        return b.append(']').toString();
    }

    /**
     * Read or set the camera: "camera" reads; "camera <yaw> <pitch> <zoom>" sets, "-" keeps a
     * value. Yaw 0-2047 (0 = north), pitch 128-383 (383 = most top-down), zoom 1-2000 (600
     * default). The client eases toward the target (plugin.api.API.SetCamera*).
     */
    private static String camera(String arg) throws Exception {
        Class<?> api = cls("plugin.api.API");
        if (arg != null && !arg.trim().isEmpty()) {
            String[] a = arg.trim().split(" ");
            if (a.length > 0 && !a[0].equals("-")) api.getMethod("SetCameraYaw", double.class).invoke(null, Double.parseDouble(a[0]));
            if (a.length > 1 && !a[1].equals("-")) api.getMethod("SetCameraPitch", double.class).invoke(null, Double.parseDouble(a[1]));
            if (a.length > 2 && !a[2].equals("-")) api.getMethod("SetCameraZoom", int.class).invoke(null, Integer.parseInt(a[2]));
        }
        return "{\"yaw\":" + statInt("rt4.Camera", "cameraYaw")
            + ",\"pitch\":" + statInt("rt4.Camera", "cameraPitch")
            + ",\"yaw_target\":" + api.getMethod("GetCameraYaw").invoke(null)
            + ",\"pitch_target\":" + api.getMethod("GetCameraPitch").invoke(null)
            + ",\"zoom\":" + api.getMethod("GetCameraZoom").invoke(null) + "}";
    }

    /**
     * How the minimap is drawn. The client skews it at login: rotated compassAngleOffset
     * (+-60 of 2048) past the camera yaw - the compass needle doesn't show that - and scaled
     * by 256 / (256 + zoomOffset) (zoomOffset -20..9). angle = (yawTarget + offset) & 2047,
     * clockwise from screen-up to north; scale = minimap px per map px (4 map px per tile).
     */
    private static String minimap() throws Exception {
        int yaw = (int) ((Number) stat("rt4.Camera", "yawTarget")).doubleValue();
        int off = statInt("rt4.MiniMap", "compassAngleOffset");
        int zoom = statInt("rt4.MiniMap", "zoomOffset");
        return "{\"yaw\":" + yaw + ",\"angle_offset\":" + off + ",\"zoom_offset\":" + zoom
            + ",\"angle\":" + ((yaw + off) & 0x7FF) + ",\"scale\":" + (256.0 / (256 + zoom)) + "}";
    }

    private static String skills() throws Exception {
        int[] base = (int[]) stat("rt4.PlayerSkillXpTable", "baseLevels");
        int[] boosted = (int[]) stat("rt4.PlayerSkillXpTable", "boostedLevels");
        int[] xp = (int[]) stat("rt4.PlayerSkillXpTable", "experience");
        StringBuilder b = new StringBuilder("{\"base\":[");
        for (int i = 0; i < base.length; i++) b.append(i == 0 ? "" : ",").append(base[i]);
        b.append("],\"boosted\":[");
        for (int i = 0; i < boosted.length; i++) b.append(i == 0 ? "" : ",").append(boosted[i]);
        b.append("],\"xp\":[");
        for (int i = 0; i < xp.length; i++) b.append(i == 0 ? "" : ",").append(xp[i]);
        return b.append("]}").toString();
    }

    private static String player() throws Exception {
        Object self = stat("rt4.PlayerList", "self");
        boolean loggedIn = statInt("rt4.client", "gameState") == 30;
        if (self == null || !loggedIn) return "{\"logged_in\":false}";
        int ox = statInt("rt4.Camera", "originX"), oy = statInt("rt4.Camera", "originY");
        int tx = ((int[]) get(self, "movementQueueX"))[0], ty = ((int[]) get(self, "movementQueueY"))[0];
        int xf = getInt(self, "xFine"), yf = getInt(self, "yFine");
        int[] s = screen(xf, yf, 0);
        int loop = statInt("rt4.client", "loop");
        return "{\"logged_in\":true"
            + ",\"index\":" + statInt("rt4.PlayerList", "selfId")
            + ",\"in_combat\":" + (getInt(self, "hitpointsBarVisibleUntil") > loop)
            + ",\"tile\":[" + (tx + ox) + "," + (ty + oy) + "]"
            + ",\"plane\":" + statInt("rt4.Player", "plane")
            + ",\"anim\":" + getInt(self, "seqId")
            + ",\"moving\":" + (xf != tx * 128 + 64 || yf != ty * 128 + 64)
            + ",\"queue\":" + getInt(self, "movementQueueSize")
            + ",\"interacting\":" + getInt(self, "faceEntity")
            + ",\"hp_bar\":" + getInt(self, "hitpointsBar")
            + ",\"screen\":[" + s[0] + "," + s[1] + "]"
            + ",\"yaw\":" + statInt("rt4.Camera", "cameraYaw")
            + ",\"pitch\":" + statInt("rt4.Camera", "cameraPitch")
            + "}";
    }

    // ---- interfaces (dialogs, make boxes, the bank, side tabs...) ------------------------
    /**
     * Visible interface components with something on them (text, options or an item), at their
     * canvas position - laid out the way Cs1ScriptRunner.renderComponent draws them: a child sits
     * at parent + its x/y (minus the parent's scroll), and an interface opened inside a component
     * (InterfaceList.openInterfaces) at that component's position. Optional arg: an interface id,
     * or text to match (case-insensitive) in the component's text/options.
     */
    private static String widgets(String arg) throws Exception {
        Object[][] all = (Object[][]) stat("rt4.InterfaceList", "components");
        int top = statInt("rt4.InterfaceList", "topLevelInterface");
        Integer onlyIf = null;
        String match = null;
        if (arg != null && !arg.trim().isEmpty()) {
            try { onlyIf = Integer.parseInt(arg.trim()); } catch (NumberFormatException e) { match = arg.trim().toLowerCase(); }
        }
        StringBuilder b = new StringBuilder("[");
        int[] count = {0};
        Walk w = new Walk();
        if (top >= 0 && all != null && top < all.length) {
            walkInterface(all, top, 0, 0, b, count, onlyIf, match, 0, w);
        }
        return b.append("]").toString();
    }

    /** Guards for one walk: each component and interface once, a node budget and a time limit -
     *  a component tree that points back into itself must never keep the add-on busy. */
    private static final class Walk {
        final java.util.Set<Object> seen = java.util.Collections.newSetFromMap(new java.util.IdentityHashMap<>());
        final java.util.Set<Integer> interfaces = new java.util.HashSet<>();
        final long deadline = System.nanoTime() + 150_000_000L;     // 150 ms
        int nodes = 0;

        boolean over() { return ++nodes > 4000 || System.nanoTime() > deadline; }
    }

    private static void walkInterface(Object[][] all, int iface, int px, int py, StringBuilder b, int[] count,
                                      Integer onlyIf, String match, int depth, Walk w) throws Exception {
        if (iface < 0 || iface >= all.length || all[iface] == null || depth > 12) return;
        if (!w.interfaces.add(iface)) return;                    // each interface once per walk
        walkLayer(all, all[iface], -1, px, py, b, count, onlyIf, match, depth, w);
    }

    private static void walkLayer(Object[][] all, Object[] comps, int layer, int px, int py, StringBuilder b,
                                  int[] count, Integer onlyIf, String match, int depth, Walk w) throws Exception {
        if (depth > 12) return;
        Object open = stat("rt4.InterfaceList", "openInterfaces");
        Method getPtr = open.getClass().getMethod("get", long.class);
        for (Object c : comps) {
            if (c == null || getInt(c, "overlayer") != layer) continue;
            if (!w.seen.add(c) || w.over()) continue;            // each component once; budget spent: stop
            if ((Boolean) get(c, "if3") && (Boolean) get(c, "hidden")) continue;
            int x = px + getInt(c, "x"), y = py + getInt(c, "y");
            int id = getInt(c, "id");
            emitWidget(c, id, x, y, b, count, onlyIf, match);
            if (getInt(c, "type") == 0) {
                int sx = x - getInt(c, "scrollX"), sy = y - getInt(c, "scrollY");
                walkLayer(all, comps, id, sx, sy, b, count, onlyIf, match, depth + 1, w);
                Object[] created = (Object[]) get(c, "createdComponents");
                if (created != null) walkLayer(all, created, id, sx, sy, b, count, onlyIf, match, depth + 1, w);
            }
            Object ptr = getPtr.invoke(open, (long) id);
            if (ptr != null) walkInterface(all, getInt(ptr, "interfaceId"), x, y, b, count, onlyIf, match, depth + 1, w);
        }
    }

    private static String str(Object o, String field) {
        try {
            Object v = get(o, field);
            return v == null ? "" : v.toString();
        } catch (Exception e) {
            return "";
        }
    }

    private static void emitWidget(Object c, int id, int x, int y, StringBuilder b, int[] count,
                                   Integer onlyIf, String match) throws Exception {
        if (count[0] >= 400) return;
        if (onlyIf != null && (id >>> 16) != onlyIf) return;
        String txt = text(get(c, "text"));
        String option = text(get(c, "option"));
        // targeting components (spells): "Cast" + the spell's name live in these
        String name = (str(c, "optionBase") + " " + str(c, "optionCircumfix") + " " + str(c, "optionSuffix")).trim();
        Object[] ops = (Object[]) get(c, "ops");
        int obj = getInt(c, "objId");
        StringBuilder opsJson = new StringBuilder("[");
        StringBuilder all = new StringBuilder(txt == null ? "" : txt).append(' ').append(option == null ? "" : option)
            .append(' ').append(name);
        if (ops != null) {
            for (Object o : ops) {
                if (o == null) continue;
                if (opsJson.length() > 1) opsJson.append(',');
                opsJson.append(q(text(o)));
                all.append(' ').append(text(o));
            }
        }
        opsJson.append(']');
        boolean hasText = txt != null && !txt.isEmpty();
        boolean hasOps = opsJson.length() > 2 || (option != null && !option.isEmpty()) || !name.isEmpty();
        if (!hasText && !hasOps && obj <= 0) return;
        if (match != null && !all.toString().toLowerCase().contains(match)) return;
        if (b.length() > 1) b.append(',');
        b.append("{\"if\":").append(id >>> 16).append(",\"idx\":").append(id & 0xffff)
         .append(",\"type\":").append(getInt(c, "type"))
         .append(",\"x\":").append(x).append(",\"y\":").append(y)
         .append(",\"w\":").append(getInt(c, "width")).append(",\"h\":").append(getInt(c, "height"))
         .append(",\"text\":").append(q(txt)).append(",\"option\":").append(q(option))
         .append(",\"name\":").append(q(name))
         .append(",\"ops\":").append(opsJson)
         .append(",\"obj\":").append(obj).append(",\"count\":").append(getInt(c, "objCount"))
         .append('}');
        count[0]++;
    }

    // ---- the frame ---------------------------------------------------------------------
    /** {width, height, bytes}: the software renderer's frame buffer as B,G,R,0 bytes per pixel. */
    static Object[] frame() throws Exception {
        Object fb = stat("rt4.SoftwareRaster", "frameBuffer");
        if (fb == null) return glFrame();
        int[] px = (int[]) get(fb, "pixels");
        int w = getInt(fb, "width"), h = getInt(fb, "height");
        if (px == null || w <= 0 || h <= 0 || px.length < w * h) throw new IllegalStateException("frame not ready");
        java.nio.ByteBuffer buf = java.nio.ByteBuffer.allocate(w * h * 4).order(java.nio.ByteOrder.LITTLE_ENDIAN);
        buf.asIntBuffer().put(px, 0, w * h);
        return new Object[]{w, h, buf.array()};
    }

    /** HD mode: the client reads its own picture back after every frame (GlRenderer.pixelData,
     *  bottom row first) - copy that, top row first. Same pixel layout as the software buffer. */
    private static Object[] glFrame() throws Exception {
        Class<?> gl = cls("rt4.GlRenderer");
        if (!(Boolean) field(gl, "enabled").get(null)) throw new IllegalStateException("no frame buffer (HD mode?)");
        int[] px = (int[]) field(gl, "pixelData").get(null);
        int w = statInt("rt4.GlRenderer", "canvasWidth"), h = statInt("rt4.GlRenderer", "canvasHeight");
        if (px == null || w <= 0 || h <= 0 || px.length < w * h) throw new IllegalStateException("HD frame not ready");
        java.nio.ByteBuffer buf = java.nio.ByteBuffer.allocate(w * h * 4).order(java.nio.ByteOrder.LITTLE_ENDIAN);
        java.nio.IntBuffer ib = buf.asIntBuffer();
        for (int y = h - 1; y >= 0; y--) ib.put(px, y * w, w);
        return new Object[]{w, h, buf.array()};
    }

    // ---- logging in (the login screen's own action, as its Login button does) ------------
    /** arg: base64(username) + " " + base64(password). Only at an idle login screen - the same
     *  checks the client's login script makes before LoginManager.startLogin. */
    private static String login(String arg) throws Exception {
        String[] p = arg == null ? new String[0] : arg.trim().split(" ");
        if (p.length != 2) return "{\"ok\":false,\"why\":\"bad arguments\"}";
        java.util.Base64.Decoder b64 = java.util.Base64.getDecoder();
        String user = new String(b64.decode(p[0]), "UTF-8"), pass = new String(b64.decode(p[1]), "UTF-8");
        int state = statInt("rt4.client", "gameState");
        if (state == 30) return "{\"ok\":false,\"why\":\"already logged in\"}";
        boolean idle = state == 10 && statInt("rt4.LoginManager", "hopStep") == 0
            && statInt("rt4.LoginManager", "step") == 0 && statInt("rt4.CreateManager", "step") == 0
            && statInt("rt4.WorldList", "step") == 0;
        if (!idle) return "{\"ok\":false,\"why\":\"not at an idle login screen (state " + state + ")\"}";
        Class<?> js = cls("rt4.JagString");
        Method parse = js.getMethod("parse", String.class);
        Method start = cls("rt4.LoginManager").getMethod("startLogin", js, js, int.class);
        start.invoke(null, parse.invoke(null, user), parse.invoke(null, pass), 0);
        return "{\"ok\":true}";
    }

    /** {state, step, reply}: how a login attempt is going (reply: the server's answer code). */
    static String loginStatus() throws Exception {
        return "{\"state\":" + statInt("rt4.client", "gameState") + ",\"step\":" + statInt("rt4.LoginManager", "step")
            + ",\"reply\":" + statInt("rt4.LoginManager", "reply") + "}";
    }
}
