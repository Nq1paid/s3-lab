import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Minimal JSON reader/writer.
 *
 * Hand-rolled on purpose. The protocol has to be implementable in any language
 * with stdin and stdout, and requiring Maven, Gradle or a third-party jar just
 * to join the conversation would make that claim false. This is the entire
 * dependency footprint of a Java strategy.
 */
final class Json {

    private final String s;
    private int i;

    private Json(String s) {
        this.s = s;
    }

    static Object parse(String text) {
        Json p = new Json(text);
        p.ws();
        return p.value();
    }

    private void ws() {
        while (i < s.length() && Character.isWhitespace(s.charAt(i))) {
            i++;
        }
    }

    private Object value() {
        char c = s.charAt(i);
        if (c == '{') {
            return object();
        }
        if (c == '[') {
            return array();
        }
        if (c == '"') {
            return string();
        }
        if (c == 't') {
            i += 4;
            return Boolean.TRUE;
        }
        if (c == 'f') {
            i += 5;
            return Boolean.FALSE;
        }
        if (c == 'n') {
            i += 4;
            return null;
        }
        return number();
    }

    private Map<String, Object> object() {
        Map<String, Object> m = new LinkedHashMap<>();
        i++;
        ws();
        if (s.charAt(i) == '}') {
            i++;
            return m;
        }
        while (true) {
            ws();
            String k = string();
            ws();
            i++;                      // colon
            ws();
            m.put(k, value());
            ws();
            if (s.charAt(i++) == '}') {
                return m;
            }
        }
    }

    private List<Object> array() {
        List<Object> a = new ArrayList<>();
        i++;
        ws();
        if (s.charAt(i) == ']') {
            i++;
            return a;
        }
        while (true) {
            ws();
            a.add(value());
            ws();
            if (s.charAt(i++) == ']') {
                return a;
            }
        }
    }

    private String string() {
        StringBuilder b = new StringBuilder();
        i++;                          // opening quote
        while (true) {
            char c = s.charAt(i++);
            if (c == '"') {
                return b.toString();
            }
            if (c == 92) {            // backslash
                char e = s.charAt(i++);
                if (e == 'n') {
                    b.append('\n');
                } else if (e == 't') {
                    b.append('\t');
                } else if (e == 'r') {
                    b.append('\r');
                } else if (e == 'b') {
                    b.append('\b');
                } else if (e == 'f') {
                    b.append('\f');
                } else if (e == 'u') {
                    b.append((char) Integer.parseInt(s.substring(i, i + 4), 16));
                    i += 4;
                } else {
                    b.append(e);
                }
            } else {
                b.append(c);
            }
        }
    }

    private Double number() {
        int start = i;
        while (i < s.length() && "-+.eE0123456789".indexOf(s.charAt(i)) >= 0) {
            i++;
        }
        return Double.valueOf(s.substring(start, i));
    }

    // ---- writing

    static String esc(String v) {
        StringBuilder b = new StringBuilder();
        b.append('"');
        for (char c : v.toCharArray()) {
            if (c == '"' || c == 92) {
                b.append((char) 92).append(c);
            } else if (c == '\n') {
                b.append((char) 92).append('n');
            } else {
                b.append(c);
            }
        }
        b.append('"');
        return b.toString();
    }

    /**
     * Shortest round-trip form, with whole numbers written without a trailing
     * ".0" so the wire matches what Python emits for the same value.
     */
    static String num(double v) {
        if (v == Math.rint(v) && !Double.isInfinite(v) && Math.abs(v) < 1e15) {
            return Long.toString((long) v);
        }
        return Double.toString(v);
    }
}
