"""
Vercel serverless function: GET /api/projects?q=TERM

Leest veldwerk_projects_index.json van SharePoint en filtert op query.
Credentials via Vercel environment variables (TENANT_ID, CLIENT_ID, CLIENT_SECRET, DRIVE_ID).
"""
import json
import os
import time
import urllib.request
import urllib.parse
from http.server import BaseHTTPRequestHandler

# Module-level cache (warm invocations hergebruiken dit)
_cache = {"token": None, "token_ts": 0, "index": None, "index_ts": 0}
INDEX_SP_PATH = "General/HP Automatiseringen/veldwerk_projects_index.json"
INDEX_TTL = 300  # 5 minuten cache


def _get_token() -> str:
    now = time.time()
    if _cache["token"] and now - _cache["token_ts"] < 3300:
        return _cache["token"]
    tenant = os.environ["TENANT_ID"]
    data = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": os.environ["CLIENT_ID"],
        "client_secret": os.environ["CLIENT_SECRET"],
        "scope": "https://graph.microsoft.com/.default",
    }).encode()
    req = urllib.request.Request(
        f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        data=data, method="POST"
    )
    resp = urllib.request.urlopen(req, timeout=10)
    token = json.loads(resp.read())["access_token"]
    _cache["token"] = token
    _cache["token_ts"] = now
    return token


def _get_index() -> list:
    now = time.time()
    if _cache["index"] and now - _cache["index_ts"] < INDEX_TTL:
        return _cache["index"]
    drive_id = os.environ["DRIVE_ID"]
    token = _get_token()
    url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/{urllib.parse.quote(INDEX_SP_PATH, safe="/")}:/content"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    resp = urllib.request.urlopen(req, timeout=10)
    data = json.loads(resp.read())
    projecten = data.get("projecten", [])
    _cache["index"] = projecten
    _cache["index_ts"] = now
    return projecten


def _score(p: dict, q: str) -> int:
    pid = p.get("projectnummer", "").lower()
    adres = p.get("adres", "").lower()
    og = p.get("opdrachtgever", "").lower()
    if q == pid: return 100
    if pid.startswith(q): return 90
    if q in pid: return 80
    if adres.startswith(q): return 70
    if q in adres: return 60
    if q in og: return 40
    return 0


def _zoek(q: str) -> list:
    projecten = _get_index()
    # Verwijder prefill uit zoekresultaten (te groot)
    def slim(p):
        return {k: v for k, v in p.items() if k != "prefill"}

    if not q:
        return [slim(p) for p in projecten[:20]]

    resultaten = [(p, _score(p, q.lower())) for p in projecten]
    resultaten = [(p, s) for p, s in resultaten if s > 0]
    resultaten.sort(key=lambda x: (-x[1], x[0].get("projectnummer", "")))
    return [slim(p) for p, _ in resultaten[:20]]


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        q = params.get("q", [""])[0].strip()

        try:
            resultaten = _zoek(q)
            body = json.dumps({"ok": True, "count": len(resultaten), "resultaten": resultaten})
            status = 200
        except Exception as e:
            body = json.dumps({"ok": False, "error": str(e)})
            status = 500

        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *args):
        pass  # Suppress access logs in Vercel
