#!/usr/bin/env python3
"""
Dummy SMTP server + web inbox for local testing (e.g. WSO2 Identity Server).

- Accepts any username/password (AUTH PLAIN / LOGIN), no TLS.
- Never relays mail anywhere; messages are kept in memory (and optionally saved as .eml).
- Web UI renders HTML emails nicely, with Text / Headers / Raw views and attachments.

Pure Python standard library, Python 3.9+. No installs needed.

Usage:
    python3 dummy_smtp.py                      # SMTP :2525, Web UI http://localhost:8025
    python3 dummy_smtp.py --smtp-port 1025 --web-port 8080 --save-dir ./mails
"""

import argparse
import base64
import email
import email.policy
import html
import json
import os
import re
import socketserver
import threading
import uuid
from datetime import datetime
from email.utils import parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote

# --------------------------------------------------------------------------- store

STORE = []          # newest first
STORE_LOCK = threading.Lock()
SAVE_DIR = None
MAX_MESSAGES = 500


def _decode_header(value):
    if value is None:
        return ""
    return str(value)


def add_message(raw_bytes, mail_from, rcpt_to, auth_user, peer):
    msg = email.message_from_bytes(raw_bytes, policy=email.policy.default)
    msg_id = uuid.uuid4().hex[:12]

    html_body, text_body = None, None
    attachments, inline = [], {}
    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        disp = (part.get_content_disposition() or "").lower()
        filename = part.get_filename()
        cid = (part.get("Content-ID") or "").strip("<> ")
        if ctype == "text/html" and disp != "attachment" and html_body is None:
            html_body = part.get_content()
        elif ctype == "text/plain" and disp != "attachment" and not filename and text_body is None:
            text_body = part.get_content()
        else:
            payload = part.get_payload(decode=True) or b""
            entry = {"filename": filename or (cid or "part") , "ctype": ctype,
                     "data": payload, "cid": cid}
            attachments.append(entry)
            if cid:
                inline[cid] = entry

    date = msg.get("Date")
    try:
        received = parsedate_to_datetime(date).isoformat() if date else None
    except Exception:
        received = None

    record = {
        "id": msg_id,
        "received_at": datetime.now().isoformat(timespec="seconds"),
        "date": received,
        "subject": _decode_header(msg.get("Subject")) or "(no subject)",
        "from": _decode_header(msg.get("From")) or mail_from,
        "to": _decode_header(msg.get("To")) or ", ".join(rcpt_to),
        "envelope_from": mail_from,
        "envelope_to": rcpt_to,
        "auth_user": auth_user,
        "peer": peer,
        "headers": [(k, str(v)) for k, v in msg.items()],
        "html": html_body,
        "text": text_body,
        "attachments": attachments,
        "inline": inline,
        "raw": raw_bytes,
        "read": False,
    }
    with STORE_LOCK:
        STORE.insert(0, record)
        del STORE[MAX_MESSAGES:]

    if SAVE_DIR:
        os.makedirs(SAVE_DIR, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        with open(os.path.join(SAVE_DIR, f"{stamp}-{msg_id}.eml"), "wb") as f:
            f.write(raw_bytes)

    print(f"[smtp] {record['received_at']}  {mail_from} -> {', '.join(rcpt_to)}  "
          f"\"{record['subject']}\"  (id={msg_id})", flush=True)
    return record


def get_message(msg_id):
    with STORE_LOCK:
        for m in STORE:
            if m["id"] == msg_id:
                return m
    return None

# --------------------------------------------------------------------------- SMTP


class SMTPHandler(socketserver.StreamRequestHandler):
    hostname = "dummy-smtp.local"

    def reply(self, line):
        self.wfile.write((line + "\r\n").encode())
        self.wfile.flush()

    def readline(self):
        line = self.rfile.readline(65536)
        if not line:
            raise ConnectionError
        return line.decode("utf-8", "replace").rstrip("\r\n")

    def reset(self):
        self.mail_from, self.rcpt_to = None, []

    def handle(self):
        peer = f"{self.client_address[0]}:{self.client_address[1]}"
        self.auth_user = None
        self.reset()
        try:
            self.reply(f"220 {self.hostname} Dummy SMTP ready")
            while True:
                line = self.readline()
                cmd, _, arg = line.partition(" ")
                cmd = cmd.upper()
                if cmd == "EHLO":
                    self.reply(f"250-{self.hostname} Hello {arg}")
                    self.reply("250-SIZE 52428800")
                    self.reply("250-8BITMIME")
                    self.reply("250-SMTPUTF8")
                    self.reply("250-AUTH PLAIN LOGIN CRAM-MD5")
                    self.reply("250-AUTH=PLAIN LOGIN")
                    self.reply("250 HELP")
                elif cmd == "HELO":
                    self.reply(f"250 {self.hostname}")
                elif cmd == "AUTH":
                    self.do_auth(arg)
                elif cmd == "STARTTLS":
                    self.reply("454 TLS not available (dummy server) - set enable_start_tls=false")
                elif cmd == "MAIL":
                    m = re.search(r"FROM:\s*<?([^>\s]*)>?", arg, re.I)
                    self.reset()
                    self.mail_from = m.group(1) if m else arg
                    self.reply("250 OK")
                elif cmd == "RCPT":
                    m = re.search(r"TO:\s*<?([^>\s]*)>?", arg, re.I)
                    if self.mail_from is None:
                        self.reply("503 Need MAIL first")
                        continue
                    self.rcpt_to.append(m.group(1) if m else arg)
                    self.reply("250 OK")
                elif cmd == "DATA":
                    if not self.rcpt_to:
                        self.reply("503 Need RCPT first")
                        continue
                    self.reply("354 End data with <CR><LF>.<CR><LF>")
                    chunks = []
                    while True:
                        raw = self.rfile.readline(1 << 20)
                        if not raw:
                            raise ConnectionError
                        if raw in (b".\r\n", b".\n"):
                            break
                        if raw.startswith(b".."):
                            raw = raw[1:]          # dot-unstuffing
                        chunks.append(raw)
                    rec = add_message(b"".join(chunks), self.mail_from, self.rcpt_to,
                                      self.auth_user, peer)
                    self.reset()
                    self.reply(f"250 OK queued as {rec['id']}")
                elif cmd == "RSET":
                    self.reset()
                    self.reply("250 OK")
                elif cmd == "NOOP":
                    self.reply("250 OK")
                elif cmd == "VRFY":
                    self.reply("252 Cannot VRFY user, but will accept message")
                elif cmd == "QUIT":
                    self.reply("221 Bye")
                    return
                else:
                    self.reply("502 Command not implemented")
        except (ConnectionError, OSError):
            pass

    def do_auth(self, arg):
        mech, _, initial = arg.partition(" ")
        mech = mech.upper()
        b64 = lambda s: base64.b64decode(s + "===").decode("utf-8", "replace")
        try:
            if mech == "PLAIN":
                if not initial:
                    self.reply("334 ")
                    initial = self.readline()
                parts = b64(initial).split("\x00")
                user = parts[1] if len(parts) > 1 else parts[0]
            elif mech == "LOGIN":
                if initial:
                    user = b64(initial)
                else:
                    self.reply("334 VXNlcm5hbWU6")      # "Username:"
                    user = b64(self.readline())
                self.reply("334 UGFzc3dvcmQ6")          # "Password:"
                self.readline()
            elif mech == "CRAM-MD5":
                self.reply("334 " + base64.b64encode(f"<{uuid.uuid4().hex}@dummy>".encode()).decode())
                user = b64(self.readline()).split(" ")[0]
            else:
                self.reply("504 Unrecognized authentication type")
                return
        except Exception:
            self.reply("501 Malformed auth input")
            return
        self.auth_user = user
        print(f"[smtp] AUTH {mech} accepted for user '{user}'", flush=True)
        self.reply("235 2.7.0 Authentication successful")


class ThreadingSMTPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True

# --------------------------------------------------------------------------- Web UI


def summary(m):
    return {k: m[k] for k in ("id", "received_at", "subject", "from", "to", "read")} | {
        "has_html": bool(m["html"]),
        "attachments": len([a for a in m["attachments"] if not a["cid"]]),
        "snippet": re.sub(r"\s+", " ", m["text"] or re.sub(r"<[^>]+>", " ", m["html"] or ""))[:140].strip(),
    }


def detail(m):
    return summary(m) | {
        "envelope_from": m["envelope_from"], "envelope_to": m["envelope_to"],
        "auth_user": m["auth_user"], "peer": m["peer"], "headers": m["headers"],
        "text": m["text"],
        "attachment_list": [{"index": i, "filename": a["filename"], "ctype": a["ctype"],
                             "size": len(a["data"]), "inline": bool(a["cid"])}
                            for i, a in enumerate(m["attachments"])],
    }


def render_html_body(m):
    body = m["html"]
    if body is None:
        body = f"<pre style='white-space:pre-wrap;font:14px/1.5 ui-monospace,monospace'>" \
               f"{html.escape(m['text'] or '')}</pre>"
    # resolve cid: images to local URLs
    body = re.sub(r"cid:([^\"'\s>)]+)",
                  lambda mo: f"/messages/{m['id']}/cid/{mo.group(1)}", body)
    base = "<base target='_blank'>"
    if re.search(r"<head[^>]*>", body, re.I):
        body = re.sub(r"(<head[^>]*>)", r"\1" + base, body, count=1, flags=re.I)
    else:
        body = base + body
    return body


class WebHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="text/html; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def json(self, obj, code=200):
        self.send(code, json.dumps(obj), "application/json")

    def do_GET(self):
        path = urlparse(self.path).path
        parts = [unquote(p) for p in path.strip("/").split("/") if p]

        if not parts:
            return self.send(200, INDEX_HTML)
        if parts == ["api", "messages"]:
            with STORE_LOCK:
                return self.json([summary(m) for m in STORE])
        if len(parts) >= 2 and parts[0] in ("api", "messages"):
            m = get_message(parts[2] if parts[0] == "api" else parts[1])
            if not m:
                return self.send(404, "Not found", "text/plain")
            if parts[0] == "api" and len(parts) == 3:
                m["read"] = True
                return self.json(detail(m))
            sub = parts[2:] if parts[0] == "messages" else []
            if sub == ["html"]:
                return self.send(200, render_html_body(m), extra={
                    "Content-Security-Policy": "script-src 'none'; object-src 'none'"})
            if sub == ["raw"]:
                return self.send(200, m["raw"], "text/plain; charset=utf-8")
            if sub == ["download"]:
                return self.send(200, m["raw"], "message/rfc822", {
                    "Content-Disposition": f'attachment; filename="{m["id"]}.eml"'})
            if len(sub) == 2 and sub[0] == "cid" and sub[1] in m["inline"]:
                a = m["inline"][sub[1]]
                return self.send(200, a["data"], a["ctype"])
            if len(sub) == 2 and sub[0] == "attachment" and sub[1].isdigit():
                idx = int(sub[1])
                if idx < len(m["attachments"]):
                    a = m["attachments"][idx]
                    return self.send(200, a["data"], a["ctype"], {
                        "Content-Disposition": f'attachment; filename="{a["filename"]}"'})
        self.send(404, "Not found", "text/plain")

    def do_DELETE(self):
        parts = [p for p in urlparse(self.path).path.strip("/").split("/") if p]
        with STORE_LOCK:
            if parts == ["api", "messages"]:
                STORE.clear()
            elif len(parts) == 3 and parts[:2] == ["api", "messages"]:
                STORE[:] = [m for m in STORE if m["id"] != parts[2]]
        self.json({"ok": True})


INDEX_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dummy SMTP Inbox</title>
<style>
:root{--bg:#f5f6f8;--panel:#fff;--line:#e3e5ea;--text:#1d2330;--muted:#6b7280;--accent:#ff7300;--sel:#fff3e8}
@media (prefers-color-scheme:dark){:root{--bg:#14161b;--panel:#1c1f26;--line:#2c3039;--text:#e6e8ec;--muted:#9aa1ad;--sel:#2e2419}}
*{box-sizing:border-box}html,body{height:100%;margin:0}
body{font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--text);display:flex;flex-direction:column}
header{display:flex;align-items:center;gap:12px;padding:10px 16px;background:var(--panel);border-bottom:1px solid var(--line)}
header h1{font-size:16px;margin:0;font-weight:600}header h1 span{color:var(--accent)}
header .info{color:var(--muted);font-size:12px;margin-left:auto}
button{font:inherit;border:1px solid var(--line);background:var(--panel);color:var(--text);border-radius:6px;padding:5px 10px;cursor:pointer}
button:hover{border-color:var(--accent)}button.danger:hover{border-color:#d33;color:#d33}
main{flex:1;display:flex;min-height:0}
#list{width:360px;min-width:260px;border-right:1px solid var(--line);background:var(--panel);overflow:auto}
#search{width:calc(100% - 20px);margin:10px;padding:7px 10px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--text);font:inherit}
.item{padding:10px 14px;border-bottom:1px solid var(--line);cursor:pointer;position:relative}
.item:hover{background:var(--bg)}.item.sel{background:var(--sel);box-shadow:inset 3px 0 var(--accent)}
.item.unread .subj{font-weight:700}.item.unread::after{content:"";position:absolute;right:12px;top:14px;width:8px;height:8px;border-radius:50%;background:var(--accent)}
.row{display:flex;justify-content:space-between;gap:8px}.to{font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.time{color:var(--muted);font-size:12px;white-space:nowrap;margin-right:14px}
.subj{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin:2px 0}
.snip{color:var(--muted);font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#view{flex:1;display:flex;flex-direction:column;min-width:0}
.empty{margin:auto;color:var(--muted);text-align:center}
.meta{padding:14px 18px;background:var(--panel);border-bottom:1px solid var(--line)}
.meta h2{margin:0 0 8px;font-size:18px}.meta table{border-collapse:collapse;font-size:13px}
.meta td{padding:1px 10px 1px 0;vertical-align:top}.meta td:first-child{color:var(--muted)}
.tabs{display:flex;gap:4px;padding:8px 18px 0;background:var(--panel);border-bottom:1px solid var(--line)}
.tab{border:none;border-bottom:2px solid transparent;border-radius:0;background:none;padding:6px 10px;color:var(--muted)}
.tab.on{color:var(--text);border-bottom-color:var(--accent)}
.tabs .sp{flex:1}
.body{flex:1;min-height:0;overflow:auto}
iframe{width:100%;height:100%;border:0;background:#fff;display:block}
pre{margin:0;padding:16px 18px;white-space:pre-wrap;word-break:break-word;font:12.5px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}
.hdrs td{padding:4px 18px;border-bottom:1px solid var(--line);font:12.5px ui-monospace,monospace;vertical-align:top;word-break:break-all}
.hdrs td:first-child{color:var(--muted);white-space:nowrap}
.atts{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}.atts a{font-size:12px;padding:3px 8px;border:1px solid var(--line);border-radius:12px;color:var(--text);text-decoration:none}
.links a{color:var(--accent);word-break:break-all}.links li{margin:6px 0}
@media (max-width:760px){main{flex-direction:column}#list{width:auto;max-height:40vh;border-right:0;border-bottom:1px solid var(--line)}}
</style></head><body>
<header><h1><span>&#9993;</span> Dummy SMTP Inbox</h1>
<span class="info" id="info"></span>
<button onclick="load(true)">Refresh</button>
<button class="danger" onclick="clearAll()">Clear all</button></header>
<main><aside id="list"><input id="search" placeholder="Search subject, to, from..." oninput="renderList()"><div id="items"></div></aside>
<section id="view"><div class="empty">No message selected.<br><small>Waiting for mail&hellip;</small></div></section></main>
<script>
let msgs=[],cur=null,tab='html',lastIds='';
const esc=s=>(s??'').toString().replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=t=>{const d=new Date(t),n=new Date();return d.toDateString()===n.toDateString()?d.toLocaleTimeString():d.toLocaleString()};
async function load(force){
  const r=await fetch('/api/messages');msgs=await r.json();
  const ids=msgs.map(m=>m.id+m.read).join();if(!force&&ids===lastIds)return;lastIds=ids;
  document.getElementById('info').textContent=msgs.length+' message'+(msgs.length==1?'':'s')+' · SMTP '+location.hostname+':'+SMTP_PORT;
  document.title=(msgs.filter(m=>!m.read).length?'('+msgs.filter(m=>!m.read).length+') ':'')+'Dummy SMTP Inbox';
  renderList();
}
function renderList(){
  const q=document.getElementById('search').value.toLowerCase();
  document.getElementById('items').innerHTML=msgs.filter(m=>!q||(m.subject+m.to+m.from+m.snippet).toLowerCase().includes(q)).map(m=>`
   <div class="item ${m.id===cur?'sel':''} ${m.read?'':'unread'}" onclick="openMsg('${m.id}')">
    <div class="row"><span class="to">To: ${esc(m.to)}</span><span class="time">${fmt(m.received_at)}</span></div>
    <div class="subj">${esc(m.subject)}${m.attachments?' 📎':''}</div><div class="snip">${esc(m.snippet)}</div></div>`).join('')
   ||'<div class="empty" style="padding:30px">Inbox is empty</div>';
}
async function openMsg(id){cur=id;const m=await (await fetch('/api/messages/'+id)).json();cur_d=m;renderView();load(true)}
let cur_d=null;
function renderView(){
  const m=cur_d,v=document.getElementById('view');if(!m){v.innerHTML='<div class="empty">No message selected.</div>';return}
  const atts=m.attachment_list.filter(a=>!a.inline);
  v.innerHTML=`<div class="meta"><h2>${esc(m.subject)}</h2><table>
   <tr><td>From</td><td>${esc(m.from)}</td></tr><tr><td>To</td><td>${esc(m.to)}</td></tr>
   <tr><td>Received</td><td>${new Date(m.received_at).toLocaleString()}</td></tr>
   <tr><td>SMTP</td><td>auth user: <b>${esc(m.auth_user||'(none)')}</b> &middot; from ${esc(m.peer)} &middot; envelope ${esc(m.envelope_from)} &rarr; ${esc(m.envelope_to.join(', '))}</td></tr></table>
   ${atts.length?'<div class="atts">'+atts.map(a=>`<a href="/messages/${m.id}/attachment/${a.index}">📎 ${esc(a.filename)} (${(a.size/1024).toFixed(1)} KB)</a>`).join('')+'</div>':''}</div>
   <div class="tabs">${['html','text','links','headers','raw'].map(t=>`<button class="tab ${tab===t?'on':''}" onclick="tab='${t}';renderView()">${t[0].toUpperCase()+t.slice(1)}</button>`).join('')}
   <span class="sp"></span><button onclick="location='/messages/${m.id}/download'">Download .eml</button>
   <button class="danger" onclick="delMsg('${m.id}')">Delete</button></div><div class="body" id="body"></div>`;
  const b=document.getElementById('body');
  if(tab==='html') b.innerHTML=`<iframe sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox" src="/messages/${m.id}/html"></iframe>`;
  else if(tab==='text') b.innerHTML='<pre>'+esc(m.text||'(no text/plain part)')+'</pre>';
  else if(tab==='headers') b.innerHTML='<table class="hdrs">'+m.headers.map(([k,v])=>`<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join('')+'</table>';
  else if(tab==='raw') fetch('/messages/'+m.id+'/raw').then(r=>r.text()).then(t=>b.innerHTML='<pre>'+esc(t)+'</pre>');
  else if(tab==='links') fetch('/messages/'+m.id+'/html').then(r=>r.text()).then(t=>{
     const d=new DOMParser().parseFromString(t,'text/html');
     const ls=[...d.querySelectorAll('a[href]')].map(a=>[a.textContent.trim(),a.getAttribute('href')]);
     const txt=(m.text||'').match(/https?:\/\/[^\s<>"]+/g)||[];
     txt.forEach(u=>{if(!ls.some(l=>l[1]===u))ls.push(['',u])});
     b.innerHTML='<ul class="links" style="padding:10px 34px">'+(ls.map(([t,h])=>`<li>${t?esc(t)+'<br>':''}<a href="${esc(h)}" target="_blank" rel="noopener">${esc(h)}</a></li>`).join('')||'<li>No links found</li>')+'</ul>'});
}
async function delMsg(id){await fetch('/api/messages/'+id,{method:'DELETE'});cur=null;cur_d=null;renderView();load(true)}
async function clearAll(){if(!msgs.length)return;await fetch('/api/messages',{method:'DELETE'});cur=null;cur_d=null;renderView();load(true)}
load(true);setInterval(()=>load(false).catch(()=>{}),2000);
</script></body></html>"""

# --------------------------------------------------------------------------- main


def main():
    global SAVE_DIR, INDEX_HTML
    p = argparse.ArgumentParser(description="Dummy SMTP server with web inbox")
    p.add_argument("--host", default="0.0.0.0", help="bind address (default 0.0.0.0)")
    p.add_argument("--smtp-port", type=int, default=2525)
    p.add_argument("--web-port", type=int, default=8025)
    p.add_argument("--save-dir", default=None, help="also save every message as .eml here")
    a = p.parse_args()
    SAVE_DIR = a.save_dir
    INDEX_HTML = INDEX_HTML.replace("<script>", f"<script>const SMTP_PORT={a.smtp_port};", 1)

    smtp = ThreadingSMTPServer((a.host, a.smtp_port), SMTPHandler)
    web = ThreadingHTTPServer((a.host, a.web_port), WebHandler)
    threading.Thread(target=smtp.serve_forever, daemon=True).start()
    print(f"SMTP server : {a.host}:{a.smtp_port}  (any username/password accepted, no TLS)")
    print(f"Web inbox   : http://localhost:{a.web_port}")
    if SAVE_DIR:
        print(f"Saving .eml : {os.path.abspath(SAVE_DIR)}")
    print("Ctrl+C to stop.\n", flush=True)
    try:
        web.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")


if __name__ == "__main__":
    main()
