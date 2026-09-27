#!/usr/bin/env python3
"""Hotel Lobby Remix — one recipe, three inputs, one button.

stdlib-only HTTP server. Every Higgsfield call is made with the visitor's own
PromptQL token (X-PromptQL-Visitor-Token, read per request, never cached, never
sent to the browser) through the platform integration proxy, so renders bill to
the visitor's Higgsfield key.
"""
import base64, json, os, re, shutil, sqlite3, subprocess, threading, time, uuid
import urllib.error, urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(ROOT, "static")
DATA = os.path.join(ROOT, "data")
UPLOADS = os.path.join(DATA, "uploads")
OUTPUTS = os.path.join(DATA, "outputs")
DB = os.path.join(DATA, "app.sqlite")
PORT = int(os.environ.get("PORT", "8790"))
PLATFORM = (os.environ.get("PROMPTQL_PLATFORM_API_URL") or "").rstrip("/")
PROVIDER = os.environ.get("HF_PROVIDER", "higgsfield-user-private")   # integration provider id
PROVIDER_LABEL = os.environ.get("HF_PROVIDER_LABEL", "Higgsfield")     # its display name in "My Data"
HF_HOST = "api.higgsfield.ai"

# --- The recipe. Fixed on purpose: exactly what produced the original render. --
PROMPT = "keep Everything the same in the video just replace the faces with the two people in the 2 images provided."
MODEL_PATH = "/higgsfiled/genjutsu/motion-transfer/v1.0"  # spelling is Higgsfield's
RESOLUTION = "720p"
PRICE_PER_SEC = 0.681   # USD per second of *input* video at 720p, rounded up
POLL_EVERY = 10         # seconds between upstream status checks per job
READY = False


# --- storage ------------------------------------------------------------------
def db():
    con = sqlite3.connect(DB, timeout=15)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    for d in (UPLOADS, OUTPUTS):
        os.makedirs(d, exist_ok=True)
    con = db()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS uploads(id TEXT PRIMARY KEY, owner TEXT, kind TEXT, path TEXT, mime TEXT,
        name TEXT, duration REAL, width INTEGER, height INTEGER, created_at TEXT);
    CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, owner TEXT, owner_name TEXT, created_at TEXT,
        video_label TEXT, video_path TEXT, duration REAL, est_usd REAL, request_id TEXT,
        state TEXT, hf_status TEXT, error TEXT, output_path TEXT, last_poll REAL, finished_at TEXT);
    """)
    con.commit(); con.close()

def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def price(duration):
    import math
    return round(math.ceil(duration) * PRICE_PER_SEC, 2)


# --- visitor -------------------------------------------------------------------
def visitor(headers):
    tok = headers.get("X-PromptQL-Visitor-Token")
    if not tok:
        return None
    try:
        seg = tok.split(".")[1]
        seg += "=" * (-len(seg) % 4)
        claims = json.loads(base64.urlsafe_b64decode(seg))
        ns = claims.get("https://promptql.hasura.io") or {}
        sub = claims.get("sub") or ns.get("x-hasura-promptql-user-id")
        if not sub:
            return None
        return {"sub": sub, "name": claims.get("display_name") or ns.get("x-hasura-email") or "you", "token": tok}
    except Exception:
        return None


# --- Higgsfield via the platform integration proxy -----------------------------
def hf_call(token, method, path, body=None, desc="Hotel Lobby Remix"):
    url = f"{PLATFORM}/v1/integration/{PROVIDER}/{HF_HOST}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json",
        "User-Agent": "hotel-lobby-remix/1.0", "X-PromptQL-Description": desc})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            status, raw = r.status, r.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    except Exception as e:
        return 599, {"error": {"message": f"proxy unreachable: {type(e).__name__}"}}
    try:
        j = json.loads(raw or b"null")
    except Exception:
        j = {"raw": raw[:300].decode(errors="replace")}
    return status, j

def hf_problem(status, j):
    msg = None
    if isinstance(j, dict):
        e = j.get("error")
        if isinstance(e, dict):
            msg = e.get("message")
        msg = msg or j.get("detail") or j.get("message") or j.get("raw")
        if isinstance(msg, (list, dict)):
            msg = json.dumps(msg)[:300]
    msg = msg or f"upstream HTTP {status}"
    if "Unknown integration" in msg:          # proxy: HF_PROVIDER doesn't exist in this project (server misconfig, not the visitor)
        kind = "misconfigured"
    elif "not configured" in msg or "Add it in" in msg or "credentials" in msg.lower():
        kind = "not_connected"
    elif status == 403:
        kind = "consent"
    else:
        kind = "upstream"
    return {"kind": kind, "status": status, "message": msg}

def s3_put(url, headers, data):
    req = urllib.request.Request(url, data=data, method="PUT", headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code

def stage_to_higgsfield(token, path, mime, what):
    st, j = hf_call(token, "POST", "/files/generate-upload-url", {"content_type": mime},
                    desc=f"Hotel Lobby Remix: presigned upload slot for {what} (no paid generation)")
    if st not in (200, 201) or not isinstance(j, dict) or not j.get("upload_url"):
        raise HFError(hf_problem(st, j))
    with open(path, "rb") as f:
        data = f.read()
    code = s3_put(j["upload_url"], j.get("upload_headers") or {}, data)
    if code not in (200, 201, 204):
        raise HFError({"kind": "upstream", "status": code, "message": f"storage upload for {what} returned {code}"})
    return j["public_url"]

class HFError(Exception):
    def __init__(self, p):
        super().__init__(p["message"]); self.p = p


# --- media helpers --------------------------------------------------------------
def probe(path):
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                              "stream=width,height:format=duration", "-of", "json", path],
                             capture_output=True, text=True, timeout=30).stdout
        j = json.loads(out)
        s = (j.get("streams") or [{}])[0]
        return float(j["format"]["duration"]), int(s.get("width", 0)), int(s.get("height", 0))
    except Exception:
        return None

def has_audio(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type",
                          "-of", "csv=p=0", path], capture_output=True, text=True).stdout
    return "audio" in out

def sniff_image(b):
    if b[:3] == b"\xff\xd8\xff": return "image/jpeg", "jpg"
    if b[:8] == b"\x89PNG\r\n\x1a\n": return "image/png", "png"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP": return "image/webp", "webp"
    return None, None

_HF_STATUS = {}  # visitor -> (ts, state); cached so the pill doesn't hammer the proxy

def hf_status(v):
    """Cheap connection check: a price estimate (never a generation). Classifies the proxy reply."""
    hit = _HF_STATUS.get(v["sub"])
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    st, j = hf_call(v["token"], "POST", "/estimate" + MODEL_PATH,
                    {"video_url": "https://example.com/clip.mp4", "image_urls": [], "prompt": PROMPT, "resolution": RESOLUTION},
                    desc="Hotel Lobby Remix: connection check via price estimate (no generation, no charge)")
    if st == 599 or st == 401:          # proxy unreachable / visitor token rejected by the proxy itself
        state = "unknown"
    else:
        kind = hf_problem(st, j)["kind"]
        if kind == "not_connected": state = "not_connected"
        elif kind == "consent": state = "consent"
        elif kind == "misconfigured": state = "misconfigured"
        elif 200 <= st < 500: state = "connected"   # any answer from Higgsfield itself means the key went through
        else: state = "unknown"
    _HF_STATUS[v["sub"]] = (time.time(), state)
    return state

def sample_by_id(sid):
    with open(os.path.join(STATIC, "samples", "samples.json")) as f:
        for s in json.load(f):
            if s["id"] == sid:
                return s
    return None


# --- job lifecycle ----------------------------------------------------------------
def row(j):
    if j is None: return None
    d = dict(j)
    d.pop("video_path", None); d.pop("last_poll", None)
    d["output"] = f"/outputs/{d['id']}.mp4" if d.get("output_path") else None
    d.pop("output_path", None)
    return d

def refresh_job(job, token):
    """Poll Higgsfield for a live job (rate-limited) and advance its state."""
    if job["state"] not in ("queued", "in_progress"):
        return job
    if time.time() - (job["last_poll"] or 0) < POLL_EVERY:
        return job
    st, j = hf_call(token, "GET", f"/requests/{job['request_id']}/status",
                    desc="Hotel Lobby Remix: check render status")
    con = db()
    con.execute("UPDATE jobs SET last_poll=? WHERE id=?", (time.time(), job["id"]))
    if st == 200 and isinstance(j, dict) and j.get("status"):
        hs = j["status"]
        if hs == "completed":
            url = ((j.get("video") or {}).get("url")) or j.get("video_url")
            con.execute("UPDATE jobs SET state='finalizing', hf_status=? WHERE id=?", (hs, job["id"]))
            con.commit()
            threading.Thread(target=finalize, args=(job["id"], url), daemon=True).start()
        elif hs in ("failed", "nsfw", "cancelled", "canceled"):
            why = "Higgsfield's content filter rejected this render (no charge)." if hs == "nsfw" else \
                  f"Higgsfield reported the render as {hs} (no charge). Try again."
            con.execute("UPDATE jobs SET state='failed', hf_status=?, error=?, finished_at=? WHERE id=?",
                        (hs, why, now(), job["id"]))
        else:
            st2 = "in_progress" if hs == "in_progress" else "queued"
            con.execute("UPDATE jobs SET state=?, hf_status=? WHERE id=?", (st2, hs, job["id"]))
    elif st in (401, 403, 502):
        p = hf_problem(st, j)
        con.execute("UPDATE jobs SET error=? WHERE id=?", (f"status check: {p['message']}", job["id"]))
    con.commit()
    job = con.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone(); con.close()
    return job

def finalize(job_id, url):
    con = db(); job = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone(); con.close()
    raw = os.path.join(OUTPUTS, f"{job_id}.raw.mp4")
    out = os.path.join(OUTPUTS, f"{job_id}.mp4")
    err = None
    try:
        if not url:
            raise RuntimeError("completed job had no video url")
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "hotel-lobby-remix/1.0"}), timeout=300) as r, open(raw, "wb") as f:
            shutil.copyfileobj(r, f)
        src = job["video_path"]
        if src and os.path.exists(src) and has_audio(src):
            # Re-mux the source's own audio under Higgsfield's picture for exact sync.
            cmd = ["ffmpeg", "-v", "error", "-y", "-i", raw, "-i", src, "-map", "0:v:0", "-map", "1:a:0",
                   "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", out]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if r.returncode != 0:
                raise RuntimeError("audio re-mux failed: " + r.stderr[-300:])
        else:
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", raw, "-c", "copy", "-movflags", "+faststart", out],
                           capture_output=True, text=True, timeout=600)
            if not os.path.exists(out):
                shutil.copyfile(raw, out)
    except Exception as e:
        err = str(e)[:400]
    finally:
        try: os.remove(raw)
        except OSError: pass
    con = db()
    if err:
        con.execute("UPDATE jobs SET state='failed', error=?, finished_at=? WHERE id=?", (err, now(), job_id))
    else:
        con.execute("UPDATE jobs SET state='done', output_path=?, finished_at=? WHERE id=?", (out, now(), job_id))
    con.commit(); con.close()


# --- HTTP -------------------------------------------------------------------------
class H(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=STATIC, **k)
    def log_message(self, *a):
        pass
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()
    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def _err(self, code, msg, kind="bad_request"):
        return self._json(code, {"error": {"kind": kind, "message": msg}})
    def _body(self, limit):
        n = int(self.headers.get("Content-Length", "0") or 0)
        if n > limit:
            return None
        return self.rfile.read(n) if n else b""

    # Range-capable file serving for video (SimpleHTTPRequestHandler can't seek).
    def _serve_file(self, path, ctype="video/mp4"):
        if not os.path.isfile(path):
            return self._err(404, "not found", "not_found")
        size = os.path.getsize(path); start, end = 0, size - 1
        rng = self.headers.get("Range")
        code = 200
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m:
                if m.group(1): start = int(m.group(1))
                if m.group(2): end = min(int(m.group(2)), size - 1)
                if not m.group(1) and m.group(2): start = max(size - int(m.group(2)), 0); end = size - 1
                code = 206
        length = end - start + 1
        self.send_response(code)
        self.send_header("Content-Type", ctype); self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start); left = length
            while left > 0:
                chunk = f.read(min(1 << 20, left))
                if not chunk: break
                self.wfile.write(chunk); left -= len(chunk)

    def do_GET(self):
        u = urlparse(self.path); p = u.path
        if p == "/readyz":
            self.send_response(204 if READY else 503); self.end_headers(); return
        if p == "/":
            self.path = "/index.html"; return super().do_GET()
        if p.startswith("/samples/") and p.endswith(".mp4"):
            return self._serve_file(os.path.join(STATIC, "samples", os.path.basename(p)))
        v = visitor(self.headers)
        if p == "/api/me":
            return self._json(200, {"anonymous": v is None, "name": v and v["name"], "provider": PROVIDER,
                                    "provider_label": PROVIDER_LABEL, "platform": bool(PLATFORM), "resolution": RESOLUTION,
                                    "price_per_sec": PRICE_PER_SEC, "prompt": PROMPT})
        if p == "/api/hf-status":
            if not v:
                return self._json(200, {"state": "anon"})
            return self._json(200, {"state": hf_status(v), "provider": PROVIDER})
        if p == "/api/jobs" or p.startswith("/api/jobs/"):
            if not v:
                return self._err(401, "Open this app inside PromptQL to see your renders.", "anonymous")
            con = db()
            if p == "/api/jobs":
                jobs = con.execute("SELECT * FROM jobs WHERE owner=? ORDER BY created_at DESC LIMIT 20", (v["sub"],)).fetchall()
                con.close()
                return self._json(200, [row(refresh_job(j, v["token"])) for j in jobs])
            job = con.execute("SELECT * FROM jobs WHERE id=? AND owner=?", (p.rsplit("/", 1)[1], v["sub"])).fetchone(); con.close()
            if not job:
                return self._err(404, "no such render", "not_found")
            return self._json(200, row(refresh_job(job, v["token"])))
        if p.startswith("/outputs/") and p.endswith(".mp4"):
            if not v:
                return self._err(401, "sign in via PromptQL", "anonymous")
            jid = os.path.basename(p)[:-4]
            con = db(); job = con.execute("SELECT output_path FROM jobs WHERE id=? AND owner=?", (jid, v["sub"])).fetchone(); con.close()
            if not job or not job["output_path"]:
                return self._err(404, "not found", "not_found")
            return self._serve_file(job["output_path"])
        return super().do_GET()

    def do_PUT(self):
        u = urlparse(self.path)
        if u.path != "/api/upload":
            return self._err(404, "not found", "not_found")
        v = visitor(self.headers)
        if not v:
            return self._err(401, "Open this app inside PromptQL to upload.", "anonymous")
        q = parse_qs(u.query); kind = (q.get("kind") or ["face"])[0]; name = (q.get("name") or [""])[0][:120]
        if kind != "face":
            return self._err(400, "Only face photos can be uploaded; the clip is fixed.", "bad_request")
        limit = 15_000_000
        data = self._body(limit)
        if data is None or not data:
            return self._err(413 if data is None else 400, "file too large" if data is None else "empty upload")
        uid = uuid.uuid4().hex
        if kind == "face":
            mime, ext = sniff_image(data)
            if not mime:
                return self._err(400, "Please upload a JPEG, PNG or WebP photo.")
            path = os.path.join(UPLOADS, f"{uid}.{ext}"); open(path, "wb").write(data)
            meta = dict(duration=None, width=None, height=None)
        else:
            path = os.path.join(UPLOADS, f"{uid}.mp4"); open(path, "wb").write(data); mime = "video/mp4"
            pr = probe(path)
            if not pr:
                os.remove(path); return self._err(400, "That file doesn't look like a video we can read.")
            dur, w, h = pr
            if dur < 4:
                os.remove(path); return self._err(400, "Video must be at least 4 seconds long.")
            meta = dict(duration=round(dur, 2), width=w, height=h)
        con = db()
        con.execute("INSERT INTO uploads VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (uid, v["sub"], kind, path, mime, name, meta["duration"], meta["width"], meta["height"], now()))
        con.commit(); con.close()
        resp = {"id": uid, "kind": kind, "mime": mime, **meta}
        if kind == "video":
            resp["billable_seconds"] = min(int(-(-meta["duration"] // 1)), 30)
            resp["est_usd"] = price(min(meta["duration"], 30))
            resp["trimmed"] = meta["duration"] > 30
        return self._json(201, resp)

    def do_POST(self):
        u = urlparse(self.path)
        if u.path != "/api/generate":
            return self._err(404, "not found", "not_found")
        v = visitor(self.headers)
        if not v:
            return self._err(401, "Open this app inside PromptQL to generate.", "anonymous")
        if not PLATFORM:
            return self._err(500, "Server is missing PROMPTQL_PLATFORM_API_URL.", "server")
        try:
            body = json.loads(self._body(50_000) or b"{}")
        except Exception:
            return self._err(400, "bad json")
        if not body.get("consent"):
            return self._err(400, "Please confirm both people agreed to have their likeness used.")
        con = db()
        def upload(uid, kind):
            r = con.execute("SELECT * FROM uploads WHERE id=? AND owner=? AND kind=?", (uid, v["sub"], kind)).fetchone()
            return r
        vid = body.get("video") or {}
        s = sample_by_id(vid.get("sample") or "hotel-lobby")   # the clip is fixed; only the bundled sample is accepted
        if not s: con.close(); return self._err(400, "unknown sample video")
        vpath = os.path.join(STATIC, s["file"].lstrip("/")); vlabel = s["title"]; vdur = float(s["duration"]); vmime = "video/mp4"
        f1 = upload(body.get("face1"), "face"); f2 = upload(body.get("face2"), "face")
        con.close()
        if not f1 or not f2:
            return self._err(400, "Both face photos are required.")
        try:
            video_url = stage_to_higgsfield(v["token"], vpath, vmime, "the source video")
            face1_url = stage_to_higgsfield(v["token"], f1["path"], f1["mime"], "face 1 (left)")
            face2_url = stage_to_higgsfield(v["token"], f2["path"], f2["mime"], "face 2 (right)")
            est = price(min(vdur, 30))
            st, j = hf_call(v["token"], "POST", MODEL_PATH,
                            {"video_url": video_url, "image_urls": [face1_url, face2_url], "prompt": PROMPT, "resolution": RESOLUTION},
                            desc=f"Hotel Lobby Remix: Genjutsu motion transfer, {RESOLUTION}, ~{int(min(vdur,30)+0.999)} s (≈${est:.2f}, billed to your Higgsfield key)")
            if st not in (200, 201, 202) or not isinstance(j, dict) or not j.get("request_id"):
                raise HFError(hf_problem(st, j))
        except HFError as e:
            return self._json(502 if e.p["kind"] != "consent" else 403, {"error": e.p})
        jid = uuid.uuid4().hex[:12]
        con = db()
        con.execute("INSERT INTO jobs(id,owner,owner_name,created_at,video_label,video_path,duration,est_usd,request_id,state,hf_status,last_poll) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (jid, v["sub"], v["name"], now(), vlabel, vpath, vdur, est, j["request_id"], "queued", j.get("status") or "queued", time.time()))
        con.commit(); job = con.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone(); con.close()
        return self._json(201, row(job))


if __name__ == "__main__":
    init_db()
    READY = os.path.exists(os.path.join(STATIC, "index.html"))
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()