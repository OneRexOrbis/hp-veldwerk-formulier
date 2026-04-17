"""
Vercel serverless function: POST /api/foto

Maakt een SharePoint Upload Session aan. De browser uploadt de foto
daarna DIRECT naar SharePoint (geen bytes via Vercel — geen 4.5 MB body
limiet, geen cold-start timeout).

Flow:
  Browser POST /api/foto met {project, bestandsnaam, categorie, ...}
  → Server: token + index lookup + ensure Foto's folder + createUploadSession
  → Server returns {ok, uploadUrl, sp_naam, sp_folder}
  → Browser PUT de file bytes naar uploadUrl (direct naar SharePoint)

Body (JSON):
  {
    "project":      "P2600125",
    "bestandsnaam": "IMG_001.jpg",
    "mimetype":     "image/jpeg",           // optioneel
    "categorie":    "rapport"|"verificatie",
    "opnamedatum":  "YYYYMMDD_HHMMSS"       // optioneel (EXIF)
  }

Response:
  {
    "ok":        true,
    "uploadUrl": "https://...sharepoint.com/...&token=...",
    "sp_naam":   "20260417_152334_IMG_001.jpg",
    "sp_folder": "General/Lopende projecten/.../Foto's"
  }
"""
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler

INDEX_SP_PATH   = "General/HP Automatiseringen/veldwerk_projects_index.json"
FOTOS_MAP       = "Foto's"
FOTOS_VERIF_MAP = "Foto's verificatie"

_cache = {"token": None, "token_ts": 0.0, "index": None, "index_ts": 0.0}
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
    projecten = json.loads(resp.read()).get("projecten", [])
    _cache["index"] = projecten
    _cache["index_ts"] = now
    return projecten


def _sp_folder(pid: str):
    for p in _get_index():
        if p.get("projectnummer") == pid:
            return p.get("sp_folder")
    return None


def _ensure_folder(folder_path: str) -> None:
    """Maak folder aan als die nog niet bestaat (409 = al aanwezig, dat is OK)."""
    drive_id = os.environ["DRIVE_ID"]
    token = _get_token()
    parent, child = folder_path.rsplit("/", 1)
    url = (
        f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/"
        f"{urllib.parse.quote(parent, safe='/')}:/children"
    )
    body = json.dumps({
        "name": child,
        "folder": {},
        "@microsoft.graph.conflictBehavior": "fail",
    }).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as e:
        if e.code != 409:
            raise


def _create_upload_session(sp_path: str) -> str:
    """Microsoft Graph createUploadSession — returns short-lived uploadUrl (~15 min)."""
    drive_id = os.environ["DRIVE_ID"]
    token = _get_token()
    url = (
        f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/"
        f"{urllib.parse.quote(sp_path, safe='/')}:/createUploadSession"
    )
    body = json.dumps({
        "item": {"@microsoft.graph.conflictBehavior": "replace"}
    }).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    )
    resp = urllib.request.urlopen(req, timeout=10)
    return json.loads(resp.read())["uploadUrl"]


class handler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
        except Exception:
            return self._json(400, {"ok": False, "error": "Ongeldige JSON"})

        pid          = (body.get("project") or "").strip()
        bestandsnaam = (body.get("bestandsnaam") or "foto.jpg").strip()
        categorie    = (body.get("categorie") or "rapport").strip().lower()
        opnamedatum  = (body.get("opnamedatum") or "").strip()

        if not pid:
            return self._json(400, {"ok": False, "error": "project is verplicht"})

        try:
            folder = _sp_folder(pid)
            if not folder:
                return self._json(404, {"ok": False, "error": f"Project {pid} niet in index"})

            submap = FOTOS_VERIF_MAP if categorie == "verificatie" else FOTOS_MAP
            foto_folder = f"{folder}/{submap}"
            _ensure_folder(foto_folder)

            if opnamedatum and re.fullmatch(r'\d{8}_\d{6}', opnamedatum):
                ts = opnamedatum
            else:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            # Bestandsnaam veilig maken voor SharePoint (geen / \ : * ? " < > |)
            safe_naam = re.sub(r'[\\/:*?"<>|]', '_', bestandsnaam)
            sp_naam = f"{ts}_{safe_naam}"
            sp_path = f"{foto_folder}/{sp_naam}"

            upload_url = _create_upload_session(sp_path)

            self._json(200, {
                "ok":        True,
                "uploadUrl": upload_url,
                "sp_naam":   sp_naam,
                "sp_folder": foto_folder,
            })

        except urllib.error.HTTPError as e:
            try:
                err_body = e.read().decode("utf-8", errors="replace")
            except Exception:
                err_body = ""
            self._json(500, {"ok": False,
                             "error": f"Graph HTTP {e.code}: {err_body[:200]}"})
        except Exception as e:
            self._json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})

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
