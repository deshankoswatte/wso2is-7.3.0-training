import com.sun.net.httpserver.*;
import java.io.*;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.time.LocalTime;
import java.util.*;

/**
 * Minimal webhook receiver for the WSO2 IS SMS OTP (webhook) backup lab.
 * Run:  java WebhookReceiver.java          (listens on port 8090)
 *       java WebhookReceiver.java 9090     (custom port)
 * POST anything to http://localhost:8090/sms  -> printed in the console and kept in memory.
 * Open  http://localhost:8090/               -> shows the last 20 messages (auto-refreshes).
 */
public class WebhookReceiver {
    static final Deque<String> LAST = new ArrayDeque<>();

    public static void main(String[] args) throws Exception {
        int port = args.length > 0 ? Integer.parseInt(args[0]) : 8090;
        HttpServer server = HttpServer.create(new InetSocketAddress(port), 0);
        server.createContext("/", WebhookReceiver::handle);
        server.start();
        System.out.println("Webhook receiver listening on http://localhost:" + port + "/sms");
        System.out.println("Open http://localhost:" + port + "/ in a browser to see received messages.");
    }

    static void handle(HttpExchange ex) throws IOException {
        String method = ex.getRequestMethod();
        if ("GET".equals(method)) {
            StringBuilder html = new StringBuilder("<html><head><meta http-equiv='refresh' content='3'>"
                + "<title>Webhook receiver</title></head><body style='font-family:sans-serif'>"
                + "<h2>Messages received (latest first)</h2>");
            synchronized (LAST) {
                if (LAST.isEmpty()) html.append("<p>Nothing yet. Waiting for POST requests...</p>");
                for (String m : LAST) {
                    java.util.regex.Matcher otp = java.util.regex.Pattern.compile("\\b(\\d{4,8})\\b").matcher(m.substring(m.indexOf('\n') + 1));
                    String code = null;
                    while (otp.find()) { if (code == null) code = otp.group(1); }
                    if (code != null) html.append("<div style='font-size:22px;margin-top:14px'>Possible OTP: <b>").append(code).append("</b></div>");
                    html.append("<pre style='background:#f4f4f4;padding:10px;white-space:pre-wrap'>")
                    .append(m.replace("&", "&amp;").replace("<", "&lt;")).append("</pre>");
                }
            }
            html.append("</body></html>");
            send(ex, 200, html.toString(), "text/html; charset=utf-8");
            return;
        }
        String body = new String(ex.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
        StringBuilder sb = new StringBuilder();
        sb.append(LocalTime.now().withNano(0)).append("  ").append(method).append(' ').append(ex.getRequestURI()).append('\n');
        ex.getRequestHeaders().forEach((k, v) -> {
            if (k.equalsIgnoreCase("Content-Type") || k.equalsIgnoreCase("Authorization") || k.toLowerCase().startsWith("x-"))
                sb.append(k).append(": ").append(String.join(",", v)).append('\n');
        });
        sb.append(body);
        String msg = sb.toString();
        System.out.println("-----------------------------------------------\n" + msg);
        synchronized (LAST) { LAST.addFirst(msg); while (LAST.size() > 20) LAST.removeLast(); }
        send(ex, 200, "{\"status\":\"received\"}", "application/json");
    }

    static void send(HttpExchange ex, int code, String s, String type) throws IOException {
        byte[] b = s.getBytes(StandardCharsets.UTF_8);
        ex.getResponseHeaders().set("Content-Type", type);
        ex.sendResponseHeaders(code, b.length);
        try (OutputStream os = ex.getResponseBody()) { os.write(b); }
    }
}
