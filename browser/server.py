#!/usr/bin/env python3
"""Read-only web UI for browsing restic snapshots. Shells out to the restic CLI; never restores or writes."""
import json, os, re, signal, subprocess, sys
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

PORT = int(os.environ.get("PORT", "8080"))
ALLOWED_HOSTS = {h.strip().lower() for h in os.environ.get("ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")}
PREVIEW_BYTES = 64 * 1024
SNAP_RE = re.compile(r"^[0-9a-f]{8,64}$|^latest$")
RESTIC = ["restic", "--no-lock"]


def run(args, check=True):
    p = subprocess.run(RESTIC + ["--quiet"] * (args[0] != "diff") + args, capture_output=True)
    if check and p.returncode != 0:
        raise RuntimeError(p.stderr.decode(errors="replace").strip() or "restic failed")
    return p.stdout


def jsonl(raw):
    return [json.loads(l) for l in raw.decode(errors="replace").splitlines() if l.strip()]


def snap_arg(q):
    s = q.get("snap", [""])[0]
    if not SNAP_RE.match(s):
        raise ValueError("bad snapshot id")
    return s


def path_arg(q, default="/"):
    p = q.get("path", [default])[0]
    if not p.startswith("/") or "\0" in p:
        raise ValueError("bad path")
    return p


def list_snapshots(q):
    snaps = json.loads(run(["snapshots", "--json"]) or b"[]")
    snaps.sort(key=lambda s: s["time"], reverse=True)
    return [{"id": s["short_id"], "time": s["time"], "host": s.get("hostname"), "tags": s.get("tags", []),
             "paths": s.get("paths", [])} for s in snaps]


def node(n):
    return {"name": n["name"], "path": n["path"], "type": n["type"], "size": n.get("size"), "mtime": n.get("mtime")}


def ls(q):
    nodes = [n for n in jsonl(run(["ls", "--json", snap_arg(q), "--", path_arg(q)])) if n.get("struct_type") == "node"]
    return sorted((node(n) for n in nodes), key=lambda n: (n["type"] != "dir", n["name"].lower()))


def history(q):
    path = path_arg(q)

    def one(s):
        p = run(["ls", "--json", s["id"], "--", path], check=False)
        nodes = [n for n in jsonl(p) if n.get("struct_type") == "node" and n["path"] == path]
        return {**s, "node": node(nodes[0]) if nodes else None}

    with ThreadPoolExecutor(4) as ex:
        return list(ex.map(one, list_snapshots(q)))


def preview(q):
    snap, path = snap_arg(q), path_arg(q)
    p = subprocess.Popen(RESTIC + ["--quiet", "dump", snap, "--", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    data = p.stdout.read(PREVIEW_BYTES + 1)
    p.stdout.close()
    p.kill()
    p.wait()
    truncated = len(data) > PREVIEW_BYTES
    data = data[:PREVIEW_BYTES]
    try:
        return {"text": data.decode("utf-8"), "truncated": truncated}
    except UnicodeDecodeError:
        if truncated:  # may have cut a multibyte char
            try:
                return {"text": data.decode("utf-8", errors="ignore"), "truncated": True}
            except Exception:
                pass
        return {"text": None, "truncated": truncated}


def diff(q):
    a = q.get("a", [""])[0]
    b = q.get("b", [""])[0]
    if not (SNAP_RE.match(a) and SNAP_RE.match(b)):
        raise ValueError("bad snapshot id")
    return {"text": run(["diff", a, b]).decode(errors="replace")}


API = {"/api/snapshots": list_snapshots, "/api/ls": ls, "/api/history": history,
       "/api/preview": preview, "/api/diff": diff}


class H(BaseHTTPRequestHandler):
    def send(self, code, body, ctype="application/json", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        if host not in ALLOWED_HOSTS:  # blocks DNS-rebinding; extend with ALLOWED_HOSTS
            return self.send(403, {"error": "host not allowed"})
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path == "/":
                return self.send(200, PAGE, "text/html; charset=utf-8")
            if u.path == "/download":
                return self.download(q)
            if u.path in API:
                return self.send(200, API[u.path](q))
            self.send(404, {"error": "not found"})
        except (ValueError, RuntimeError) as e:
            self.send(400, {"error": str(e)})

    def download(self, q):
        snap, path = snap_arg(q), path_arg(q)
        name = os.path.basename(path).replace('"', "").replace("\r", "").replace("\n", "") or "file"
        p = subprocess.Popen(RESTIC + ["--quiet", "dump", snap, "--", path], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        first = p.stdout.read(65536)
        if not first and p.wait() != 0:
            return self.send(400, {"error": "could not read file"})
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()  # no length: closing the connection ends the body
        self.close_connection = True
        self.wfile.write(first)
        for chunk in iter(lambda: p.stdout.read(65536), b""):
            self.wfile.write(chunk)
        p.wait()

    def log_message(self, fmt, *a):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % a))


PAGE = r"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Backup Browser</title>
<style>
:root{--bg:#fff;--fg:#1b1f24;--mut:#6a737d;--line:#d8dee4;--acc:#0b5fff;--sel:#e8f0ff;--warn:#b35900}
@media(prefers-color-scheme:dark){:root{--bg:#14171a;--fg:#e6e9ec;--mut:#8b949e;--line:#2b3138;--acc:#6ea1ff;--sel:#1d2a42;--warn:#e0a050}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.4 system-ui,sans-serif;display:flex;height:100vh}
aside{width:300px;border-right:1px solid var(--line);overflow:auto;flex:none}main{flex:1;overflow:auto;padding:16px;min-width:0}
h1{font-size:15px;margin:0;padding:12px 14px;border-bottom:1px solid var(--line)}h2{font-size:14px;margin:18px 0 6px}
.snap{padding:8px 14px;border-bottom:1px solid var(--line);cursor:pointer}.snap:hover{background:var(--sel)}.snap.on{background:var(--sel);box-shadow:inset 3px 0 var(--acc)}
.mut{color:var(--mut);font-size:12px}table{border-collapse:collapse;width:100%}td,th{text-align:left;padding:5px 8px;border-bottom:1px solid var(--line);vertical-align:top}
a,.lnk{color:var(--acc);cursor:pointer;text-decoration:none}a:hover,.lnk:hover{text-decoration:underline}
pre{background:var(--sel);padding:10px;overflow:auto;max-height:50vh;white-space:pre-wrap;word-break:break-all}
.crumb{margin-bottom:10px}.chg{color:var(--warn);font-weight:600}.gone{color:var(--mut)}
button{font:inherit;padding:3px 9px;border:1px solid var(--line);background:var(--bg);color:var(--fg);border-radius:4px;cursor:pointer}
.bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
@media(max-width:700px){body{flex-direction:column;height:auto}aside{width:auto;max-height:35vh;border-right:0;border-bottom:1px solid var(--line)}}
</style>
<aside><h1>Snapshots</h1><div id="snaps"></div></aside>
<main><div class="mut" style="margin-bottom:10px">Read-only view of the restic repository. Nothing here restores or changes files.</div>
<div id="view">Loading…</div></main>
<script>
const $=id=>document.getElementById(id);
const E=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const enc=encodeURIComponent;
const size=n=>n==null?'':n<1024?n+' B':n<1048576?(n/1024).toFixed(1)+' KB':(n/1048576).toFixed(1)+' MB';
const when=t=>new Date(t).toLocaleString();
let snaps=[],cur=null,cmp=null;
async function api(u){const r=await fetch(u);const j=await r.json();if(!r.ok)throw new Error(j.error||r.status);return j}
function fail(e){$('view').innerHTML='<p class="chg">'+E(e.message)+'</p>'}
async function init(){
  try{snaps=await api('/api/snapshots')}catch(e){return fail(e)}
  $('snaps').innerHTML=snaps.map(s=>`<div class="snap" data-id="${s.id}"><b>${when(s.time)}</b><div class="mut">${s.id} · ${E(s.host)} ${s.tags.map(E).join(',')}</div></div>`).join('')||'<p class="mut" style="padding:14px">No snapshots yet.</p>';
  document.querySelectorAll('.snap').forEach(d=>d.onclick=ev=>{ev.shiftKey&&cur?compare(cur,d.dataset.id):open(d.dataset.id,'/')});
  if(snaps.length)open(snaps[0].id,'/');else $('view').textContent='';
}
function mark(id){document.querySelectorAll('.snap').forEach(d=>d.classList.toggle('on',d.dataset.id===id))}
async function open(id,path){
  cur=id;mark(id);$('view').textContent='Loading…';
  let n;try{n=await api(`/api/ls?snap=${id}&path=${enc(path)}`)}catch(e){return fail(e)}
  const parts=path.split('/').filter(Boolean);let acc='';
  const crumb=['<a data-p="/">root</a>',...parts.map(p=>{acc+='/'+p;return `<a data-p="${E(acc)}">${E(p)}</a>`})].join(' / ');
  $('view').innerHTML=`<div class="crumb">Snapshot <b>${id}</b> (${when(snaps.find(s=>s.id===id).time)}) <span class="mut">— shift-click another snapshot to compare</span></div><div class="crumb">${crumb}</div>
  <table><tr><th>Name</th><th>Size</th><th>Modified</th></tr>${n.map(x=>`<tr><td>${x.type==='dir'?`<a class="d" data-p="${E(x.path)}">📁 ${E(x.name)}</a>`:`<a class="f" data-p="${E(x.path)}">${E(x.name)}</a>`}</td><td>${size(x.size)}</td><td>${x.mtime?when(x.mtime):''}</td></tr>`).join('')||'<tr><td colspan=3 class="mut">Empty</td></tr>'}</table>`;
  document.querySelectorAll('#view a').forEach(a=>a.onclick=()=>a.classList.contains('f')?file(id,a.dataset.p):open(id,a.dataset.p));
}
async function file(id,path){
  $('view').textContent='Loading history…';
  let h;try{h=await api(`/api/history?path=${enc(path)}`)}catch(e){return fail(e)}
  let prev=null;
  // snapshots are newest first; a version differs from the next-older one if size or mtime differ
  const rows=h.map((s,i)=>{const o=h[i+1]?.node,n=s.node;
    const chg=n&&(!o||o.size!==n.size||o.mtime!==n.mtime);
    return `<tr class="${n?'':'gone'}"><td>${when(s.time)}<div class="mut">${s.id}</div></td><td>${n?size(n.size):'not present'}</td><td>${n&&n.mtime?when(n.mtime):''}</td><td>${chg?'<span class="chg">changed</span>':n?'<span class="mut">same as older</span>':''}</td>
    <td>${n?`<span class="lnk" data-v="${s.id}">preview</span> · <a href="/download?snap=${s.id}&path=${enc(path)}">download</a>`:''}</td></tr>`}).join('');
  $('view').innerHTML=`<div class="bar"><button id="back">← Back</button><b>${E(path)}</b></div>
  <h2>Versions across all snapshots</h2><div class="mut">"changed" means size or modified time differs from the next-older snapshot. Preview before restoring.</div>
  <table><tr><th>Snapshot</th><th>Size</th><th>Modified</th><th></th><th></th></tr>${rows}</table><div id="pv"></div>`;
  $('back').onclick=()=>open(id,path.replace(/\/[^/]*$/,'')||'/');
  document.querySelectorAll('[data-v]').forEach(a=>a.onclick=async()=>{
    $('pv').innerHTML='<h2>Loading preview…</h2>';
    try{const p=await api(`/api/preview?snap=${a.dataset.v}&path=${enc(path)}`);
      $('pv').innerHTML=`<h2>Preview — snapshot ${a.dataset.v}</h2>`+(p.text==null?'<p class="mut">Binary file; no text preview. Download it to inspect (e.g. open a KeePass database in KeePass).</p>':`<pre>${E(p.text)}</pre>${p.truncated?'<p class="mut">Truncated to first 64 KB.</p>':''}`);
      $('pv').scrollIntoView()}catch(e){$('pv').innerHTML='<p class="chg">'+E(e.message)+'</p>'}});
}
async function compare(a,b){
  if(a===b)return;$('view').textContent='Comparing…';
  let d;try{d=await api(`/api/diff?a=${b}&b=${a}`)}catch(e){return fail(e)}
  $('view').innerHTML=`<div class="bar"><button id="back">← Back</button><b>Changes from ${b} → ${a}</b></div><pre>${E(d.text)||'No differences.'}</pre>
  <div class="mut">+ added, - removed, M modified, U metadata only.</div>`;
  $('back').onclick=()=>open(a,'/');
}
init();
</script></html>"""

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: os._exit(0))  # PID 1 ignores SIGTERM by default
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
