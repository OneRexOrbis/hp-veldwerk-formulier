"""
Vercel serverless function: GET /api/prefill?project=P2600125

Leest veldwerk_projects_index.json van SharePoint en geeft prefill terug.
"""
import json
import os
import time
import urllib.request
import urllib.parse
from http.server import BaseHTTPRequestHandler

_cache = {"token": None, "token_ts": 0, "index": None, "index_ts": 0}
INDEX_SP_PATH = "General/HP Automatiseringen/veldwerk_projects_index.json"
INDEX_TTL = 300


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


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        pid = params.get("project", [""])[0].strip()

        if not pid:
            body = json.dumps({"ok": False, "error": "project parameter ontbreekt"})
            status = 400
        else:
            try:
                projecten = _get_index()
                project = next((p for p in projecten if p.get("projectnummer") == pid), None)
                if project is None:
                    body = json.dumps({"ok": False, "error": f"Project {pid} niet gevonden"})
                    status = 404
                elif not project.get("prefill"):
                    # Geen prefill beschikbaar — stuur minimale data terug
                    body = json.dumps({"ok": True, "prefill": {
                        "projectnummer": project["projectnummer"],
                        "adres": project.get("adres", ""),
                        "opdrachtgever": project.get("opdrachtgever", ""),
                        "discipline": project.get("discipline", "BRL2000"),
                        "protocollen": ["2001"],
                        "protocol_suggestie": project.get("protocol_suggestie", "1001"),
                        "_bron": "index (geen prefill beschikbaar)",
                    }})
                    status = 200
                else:
                    pf = dict(project["prefill"] or {})
                    pf.setdefault("discipline", project.get("discipline", ""))
                    pf.setdefault("protocol_suggestie", project.get("protocol_suggestie", "1001"))
                    body = json.dumps({"ok": True, "prefill": pf})
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
        pass
