import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneId;
import java.time.ZonedDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * Reference opening-range breakout, Java implementation of protocol 1.
 *
 * Must produce a trade log identical to the Python implementations. The session
 * and minute-of-day arithmetic is the part that has to match exactly: a bar is
 * bucketed by exchange-local wall clock, and the session opening at 17:00 CT
 * belongs to the NEXT trade date, which is why seven hours are added before
 * taking the date. Get that wrong and every opening range is computed over the
 * wrong bars while every individual number still looks entirely plausible.
 *
 * stdout is protocol only. Diagnostics go to stderr.
 */
public final class OrbStrategy {

    static final int PROTO = 1;
    static final int RTH_OPEN_MIN = 8 * 60 + 30;      // 08:30 exchange time
    static final int SESSION_SHIFT_HOURS = 7;

    static final String DESCRIBE =
        "{\"proto\":1,\"name\":\"orb_breakout\",\"version\":\"1.0\",\"params\":["
        + "{\"name\":\"or_minutes\",\"type\":\"int\",\"default\":30,\"min\":5,\"max\":120,\"step\":5},"
        + "{\"name\":\"buffer_ticks\",\"type\":\"int\",\"default\":2,\"min\":0,\"max\":20,\"step\":1},"
        + "{\"name\":\"stop_ticks\",\"type\":\"int\",\"default\":40,\"min\":10,\"max\":200,\"step\":5},"
        + "{\"name\":\"target_r\",\"type\":\"float\",\"default\":2.0,\"min\":0.5,\"max\":6.0,\"step\":0.5},"
        + "{\"name\":\"no_new_after\",\"type\":\"int\",\"default\":840,\"min\":600,\"max\":900,\"step\":30},"
        + "{\"name\":\"qty\",\"type\":\"int\",\"default\":1,\"min\":1,\"max\":10,\"step\":1}]}";

    int orEnd;
    double buf;
    double stopDist;
    double targetDist;
    int noNewAfter;
    int qty;
    ZoneId zone;

    LocalDate sess = null;
    Double orHi = null;
    Double orLo = null;
    boolean done = false;

    static double p(Map<String, Object> m, String k, double dflt) {
        Object v = m.get(k);
        return (v instanceof Double) ? (Double) v : dflt;
    }

    void init(Map<String, Object> params, double tickSize, String tz) {
        int orMinutes = (int) p(params, "or_minutes", 30);
        this.orEnd = RTH_OPEN_MIN + orMinutes;
        this.buf = ((int) p(params, "buffer_ticks", 2)) * tickSize;
        this.stopDist = ((int) p(params, "stop_ticks", 40)) * tickSize;
        this.targetDist = this.stopDist * p(params, "target_r", 2.0);
        this.noNewAfter = (int) p(params, "no_new_after", 840);
        this.qty = (int) p(params, "qty", 1);
        this.zone = ZoneId.of(tz);
    }

    String onBar(long index, long ts, double o, double h, double l, double c) {
        ZonedDateTime dt = Instant.ofEpochSecond(ts).atZone(zone);
        int mod = dt.getHour() * 60 + dt.getMinute();
        LocalDate s = dt.plusHours(SESSION_SHIFT_HOURS).toLocalDate();

        if (!s.equals(sess)) {
            sess = s;
            orHi = null;
            orLo = null;
            done = false;
        }

        if (mod >= RTH_OPEN_MIN && mod < orEnd) {
            orHi = (orHi == null) ? h : Math.max(orHi, h);
            orLo = (orLo == null) ? l : Math.min(orLo, l);
            return null;
        }

        if (done || orHi == null) {
            return null;
        }
        if (!(mod >= orEnd && mod < noNewAfter)) {
            return null;
        }

        boolean longBreak = h > orHi + buf;
        boolean shortBreak = l < orLo - buf;

        // A bar breaking both sides gives no ordering -- its high and low say
        // nothing about which came first -- so the session is skipped rather
        // than resolved by a coin flip.
        if (longBreak && shortBreak) {
            done = true;
            return null;
        }
        if (!longBreak && !shortBreak) {
            return null;
        }

        done = true;
        String action = longBreak ? "buy" : "sell";
        double sl = longBreak ? c - stopDist : c + stopDist;
        double tp = longBreak ? c + targetDist : c - targetDist;
        String tag = longBreak ? "orb_long" : "orb_short";
        return "{\"action\":\"" + action + "\",\"type\":\"market\",\"qty\":" + qty
             + ",\"bar\":" + index
             + ",\"stop_loss\":" + Json.num(sl)
             + ",\"take_profit\":" + Json.num(tp)
             + ",\"tag\":" + Json.esc(tag) + "}";
    }

    public static void main(String[] args) throws Exception {
        PrintStream out = new PrintStream(System.out, true, StandardCharsets.UTF_8);
        PrintStream err = new PrintStream(System.err, true, StandardCharsets.UTF_8);

        for (String a : args) {
            if (a.equals("--describe")) {
                out.println(DESCRIBE);
                return;
            }
        }

        BufferedReader in = new BufferedReader(
            new InputStreamReader(System.in, StandardCharsets.UTF_8));
        OrbStrategy strat = null;
        String line;

        while ((line = in.readLine()) != null) {
            line = line.trim();
            if (line.isEmpty()) {
                continue;
            }

            @SuppressWarnings("unchecked")
            Map<String, Object> msg = (Map<String, Object>) Json.parse(line);
            String t = (String) msg.get("t");

            if ("init".equals(t)) {
                if ((int) p(msg, "proto", -1) != PROTO) {
                    err.println("unsupported proto " + msg.get("proto"));
                    System.exit(2);
                }
                @SuppressWarnings("unchecked")
                Map<String, Object> inst = (Map<String, Object>) msg.get("instrument");
                @SuppressWarnings("unchecked")
                Map<String, Object> session = (Map<String, Object>) msg.get("session");
                @SuppressWarnings("unchecked")
                Map<String, Object> params = (Map<String, Object>) msg.get("params");

                String tz = "America/Chicago";
                if (session != null && session.get("tz") != null) {
                    tz = String.valueOf(session.get("tz"));
                }
                double tick = (inst == null) ? 0.25 : p(inst, "tick_size", 0.25);
                strat = new OrbStrategy();
                strat.init(params == null ? Map.of() : params, tick, tz);
                err.println("init tick=" + tick + " tz=" + tz);
                out.println("{\"t\":\"ready\"}");

            } else if ("bars".equals(t)) {
                if (strat == null) {
                    err.println("bars before init");
                    System.exit(2);
                }
                long start = (long) p(msg, "start", 0);
                @SuppressWarnings("unchecked")
                List<Object> rows = (List<Object>) msg.get("bars");
                List<String> orders = new ArrayList<>();
                for (int k = 0; k < rows.size(); k++) {
                    @SuppressWarnings("unchecked")
                    List<Object> r = (List<Object>) rows.get(k);
                    String order = strat.onBar(
                        start + k,
                        (long) (double) (Double) r.get(0),
                        (Double) r.get(1), (Double) r.get(2),
                        (Double) r.get(3), (Double) r.get(4));
                    if (order != null) {
                        orders.add(order);
                    }
                }
                if (orders.isEmpty()) {
                    out.println("{\"t\":\"noop\"}");
                } else {
                    out.println("{\"t\":\"orders\",\"orders\":["
                                + String.join(",", orders) + "]}");
                }

            } else if ("fills".equals(t)) {
                // Entries are bracketed, so the engine owns the exits and there
                // is nothing to reconcile. Acknowledged, not ignored.
                continue;

            } else if ("end".equals(t)) {
                out.println("{\"t\":\"bye\"}");
                return;

            } else {
                err.println("unknown message " + t);
                System.exit(2);
            }
        }
    }
}
