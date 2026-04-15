"""
Vercel serverless function: POST /api/eq_opslaan

Slaat het ingevulde EQ-formulier (JSON) op in de AI Sjablonen-map
van het project op SharePoint.

Bestandsnaam: eq_veldwerk_fase1.json  of  eq_veldwerk_fase2.json
Map:          {sp_folder}/AI Sjablonen/

Body: volledig buildExportEQ() JSON object.
Fase wordt gelezen uit _meta.fase (int, default 1).
"""
import json
import os
import time
import urllib.request
import urllib.parse
from http.server import BaseHTTPRequestHandler

INDEX_SP_PATH = "General/HP Automatiseringen/veldwerk_projects_index.json"
AI_SJABLONEN  = "AI Sjablonen"
INDEX_TTL     = 300

_cache = {"token": None, "token_ts": 0, "index": None, "index_ts": 0}


def _get_token() -> str:
    now = time.time()
    if _cache["token"] and now - _cache["token_ts"] < 3300:
        return _cache["token"]
    tenant = os.environ["TENANT_ID"]
    data = urllib.parse.urlencode({
        "grant_type":    "client_credentials",
        "client_id":     os.environ["CLIENT_ID"],
        "client_secret": os.environ["CLIENT_SECRET"],
        "scope":         "https://graph.microsoft.com/.default",
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
    url = (
        f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/"
        f"{urllib.parse.quote(INDEX_SP_PATH, safe='/')}:/content"
    )
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    resp = urllib.request.urlopen(req, timeout=10)
    data = json.loads(resp.read())
    projecten = data.get("projecten", [])
    _cache["index"] = projecten
    _cache["index_ts"] = now
    return projecten


def _sp_folder(pid: str) -> str | None:
    for p in _get_index():
        if p.get("projectnummer") == pid:
            return p.get("sp_folder")
    return None


def _upload(sp_pad: str, payload: bytes) -> None:
    drive_id = os.environ["DRIVE_ID"]
    token = _get_token()
    url = (
        f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/"
        f"{urllib.parse.quote(sp_pad, safe='/')}:/content"
    )
    req = urllib.request.Request(
        url, data=payload, method="PUT",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json",
        }
    )
    urllib.request.urlopen(req, timeout=15)


class handler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            body_bytes = self.rfile.read(length)
            data = json.loads(body_bytes)
        except Exception:
            return self._json(400, {"ok": False, "error": "Ongeldige JSON"})

        proj = data.get("project", {})
        pid  = (proj.get("projectnummer") or "").strip()

        # Fase uit _meta (int 1 of 2, default 1)
        try:
            fase = int(data.get("_meta", {}).get("fase") or 1)
        except (TypeError, ValueError):
            fase = 1
        if fase not in (1, 2):
            fase = 1

        if not pid:
            return self._json(400, {"ok": False, "error": "projectnummer ontbreekt in JSON"})

        try:
            folder = _sp_folder(pid)
            if not folder:
                return self._json(404, {"ok": False, "error": f"Project {pid} niet gevonden in index"})

            bestandsnaam = f"eq_veldwerk_fase{fase}.json"
            sp_pad = f"{folder}/{AI_SJABLONEN}/{bestandsnaam}"

            _upload(sp_pad, body_bytes)
            self._json(200, {
                "ok":           True,
                "bestandsnaam": bestandsnaam,
                "map":          f"{folder}/{AI_SJABLONEN}/",
            })

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
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, *args):
        pass
