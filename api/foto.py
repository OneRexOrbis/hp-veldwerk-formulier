"""
Vercel serverless function: POST /api/foto

Upload een foto naar {projectmap}/Foto's/ op SharePoint.

Body (JSON):
  {
    "project":     "P2600125",
    "bestandsnaam": "IMG_001.jpg",
    "data_base64": "<base64-encoded bytes>",
    "mimetype":    "image/jpeg"
  }

De sp_folder wordt opgezocht in veldwerk_projects_index.json.
Bestandsnaam op SharePoint: {YYYYMMDD_HHMMSS}_{bestandsnaam}
"""
import base64
import json
import os
import time
import urllib.request
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler

INDEX_SP_PATH   = "General/HP Automatiseringen/veldwerk_projects_index.json"
FOTOS_MAP       = "Foto's"
FOTOS_VERIF_MAP = "Foto's verificatie"
MAX_MB          = 20

_cache = {"token": None, "token_ts": 0, "index": None, "index_ts": 0}
INDEX_TTL = 300


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


def _ensure_folder(folder_path: str) -> None:
    """Maak folder aan op SharePoint als die nog niet bestaat (no-op als al aanwezig)."""
    drive_id = os.environ["DRIVE_ID"]
    token = _get_token()
    # Splits in parent + child
    parent, child = folder_path.rsplit("/", 1)
    url = (
        f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/"
        f"{urllib.parse.quote(parent, safe='/')}:/children"
    )
    body = json.dumps({
        "name": child,
        "folder": {},
        "@microsoft.graph.conflictBehavior": "fail"
    }).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json",
        }
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as e:
        if e.code == 409:
            pass  # Map bestaat al — prima
        else:
            raise


def _upload(sp_pad: str, data: bytes, mimetype: str) -> None:
    drive_id = os.environ["DRIVE_ID"]
    token = _get_token()
    url = (
        f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/"
        f"{urllib.parse.quote(sp_pad, safe='/')}:/content"
    )
    req = urllib.request.Request(
        url, data=data, method="PUT",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type":  mimetype,
        }
    )
    urllib.request.urlopen(req, timeout=45)


class handler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        if length > MAX_MB * 1024 * 1024:
            return self._json(413, {"ok": False, "error": f"Bestand te groot (max {MAX_MB} MB)"})

        try:
            body = json.loads(self.rfile.read(length))
        except Exception:
            return self._json(400, {"ok": False, "error": "Ongeldige JSON"})

        pid          = (body.get("project") or "").strip()
        bestandsnaam = (body.get("bestandsnaam") or "foto.jpg").strip()
        data_b64     = body.get("data_base64", "")
        mimetype     = body.get("mimetype", "image/jpeg")
        categorie    = (body.get("categorie") or "rapport").strip().lower()
        opnamedatum  = (body.get("opnamedatum") or "").strip()
        lat          = body.get("lat")
        lon          = body.get("lon")

        if not pid or not data_b64:
            return self._json(400, {"ok": False, "error": "project en data_base64 zijn verplicht"})

        try:
            foto_bytes = base64.b64decode(data_b64)
        except Exception:
            return self._json(400, {"ok": False, "error": "Ongeldige base64 data"})

        try:
            folder = _sp_folder(pid)
            if not folder:
                return self._json(404, {"ok": False, "error": f"Project {pid} niet gevonden"})

            submap = FOTOS_VERIF_MAP if categorie == "verificatie" else FOTOS_MAP

            # Zorg dat de Foto's submap bestaat op SharePoint
            _ensure_folder(f"{folder}/{submap}")

            # Gebruik EXIF opnamedatum als die is meegegeven en geldig is (YYYYMMDD_HHMMSS),
            # anders server-tijdstip. Zo sorteert de bijlage op opnametijd, niet uploadtijd.
            import re as _re
            if opnamedatum and _re.fullmatch(r'\d{8}_\d{6}', opnamedatum):
                ts = opnamedatum
            else:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            sp_naam = f"{ts}_{bestandsnaam}"
            sp_pad  = f"{folder}/{submap}/{sp_naam}"

            _upload(sp_pad, foto_bytes, mimetype)
            resp_data: dict = {"ok": True, "bestandsnaam": sp_naam, "map": submap}
            if lat is not None: resp_data["lat"] = lat
            if lon is not None: resp_data["lon"] = lon
            self._json(200, resp_data)

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
