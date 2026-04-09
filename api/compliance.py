"""
Vercel serverless function: GET/POST /api/compliance

POST: Ontvangt ingevuld veldwerkformulier (JSON), slaat op in SharePoint-inbox.
      Omen-cron verwerkt de inbox elke 5 minuten en genereert de PDF.
GET:  Leest eerder verwerkt compliance JSON uit veldwerk_processed/ (heropenen-flow).

Inbox-pad:     General/HP Automatiseringen/veldwerk_inbox/{pid}_compliance.json
Processed-pad: General/HP Automatiseringen/veldwerk_processed/{pid}_compliance.json
"""
import json
import os
import time
import urllib.request
import urllib.parse
from http.server import BaseHTTPRequestHandler

_cache = {"token": None, "token_ts": 0}


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


def _lees_processed(pid: str) -> dict | None:
    drive_id = os.environ["DRIVE_ID"]
    token = _get_token()
    pad = f"General/HP Automatiseringen/veldwerk_processed/{pid}_compliance.json"
    url = (
        f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/"
        f"{urllib.parse.quote(pad, safe='/')}:/content"
    )
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def _upload_to_inbox(pid: str, payload: bytes) -> None:
    drive_id = os.environ["DRIVE_ID"]
    token = _get_token()
    sp_pad = f"General/HP Automatiseringen/veldwerk_inbox/{pid}_compliance.json"
    url = (
        f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/"
        f"{urllib.parse.quote(sp_pad, safe='/')}:/content"
    )
    req = urllib.request.Request(
        url, data=payload, method="PUT",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
    )
    urllib.request.urlopen(req, timeout=15)


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        pid = params.get("project", [""])[0].strip()
        if not pid:
            return self._json(400, {"ok": False, "error": "project ontbreekt"})
        try:
            data = _lees_processed(pid)
            if data is None:
                return self._json(404, {"ok": False, "error": "Geen opgeslagen formulier"})
            self._json(200, {"ok": True, "compliance": data})
        except Exception as e:
            self._json(500, {"ok": False, "error": str(e)})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        try:
            data = json.loads(body)
        except Exception:
            self._json(400, {"ok": False, "error": "Ongeldige JSON"})
            return

        proj = data.get("project", data)
        pid = proj.get("projectnummer") or proj.get("project_id")
        if not pid:
            self._json(400, {"ok": False, "error": "projectnummer ontbreekt"})
            return

        try:
            _upload_to_inbox(pid, body)
            self._json(200, {"ok": True, "project": pid})
        except Exception as e:
            self._json(500, {"ok": False, "error": str(e)})

    def _json(self, status: int, data: dict):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, *args):
        pass
