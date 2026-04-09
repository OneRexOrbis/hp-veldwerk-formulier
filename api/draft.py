"""
Vercel serverless function: /api/draft

GET    /api/draft?project=P2600125       → haal draft op (of 404)
POST   /api/draft                        → sla draft op (body: {project, naam, data})
DELETE /api/draft?project=P2600125&naam=X → verwijder draft (bij definitief melden)

Draft-pad: General/HP Automatiseringen/veldwerk_drafts/{pid}_draft.json
Lock-logica: draft bevat {naam, timestamp}. Als timestamp < 30 min geleden en
naam ≠ huidige gebruiker → 423 Locked met {"locked_by": naam, "since": ts}.
"""
import json
import os
import time
import urllib.request
import urllib.parse
from http.server import BaseHTTPRequestHandler

DRAFT_DIR = "General/HP Automatiseringen/veldwerk_drafts"
LOCK_TTL  = 30 * 60  # seconden — lock vervalt na 30 min inactiviteit

_cache = {"token": None, "token_ts": 0}


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


def _sp_url(pid: str) -> str:
    drive_id = os.environ["DRIVE_ID"]
    pad = f"{DRAFT_DIR}/{pid}_draft.json"
    return (
        f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/"
        f"{urllib.parse.quote(pad, safe='/')}:/content"
    )


def _read_draft(pid: str) -> dict | None:
    try:
        req = urllib.request.Request(
            _sp_url(pid),
            headers={"Authorization": f"Bearer {_get_token()}"}
        )
        resp = urllib.request.urlopen(req, timeout=10)
        return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def _write_draft(pid: str, payload: bytes) -> None:
    req = urllib.request.Request(
        _sp_url(pid), data=payload, method="PUT",
        headers={
            "Authorization": f"Bearer {_get_token()}",
            "Content-Type":  "application/json",
        }
    )
    urllib.request.urlopen(req, timeout=15)


def _delete_draft(pid: str) -> None:
    drive_id = os.environ["DRIVE_ID"]
    pad = f"{DRAFT_DIR}/{pid}_draft.json"
    url = (
        f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/"
        f"{urllib.parse.quote(pad, safe='/')}:"
    )
    req = urllib.request.Request(
        url, method="DELETE",
        headers={"Authorization": f"Bearer {_get_token()}"}
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise


class handler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        pid = self._pid_from_qs()
        if not pid:
            return self._json(400, {"ok": False, "error": "project ontbreekt"})
        try:
            draft = _read_draft(pid)
            if draft is None:
                return self._json(404, {"ok": False, "error": "Geen draft"})
            self._json(200, {"ok": True, "draft": draft})
        except Exception as e:
            self._json(500, {"ok": False, "error": str(e)})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
        except Exception:
            return self._json(400, {"ok": False, "error": "Ongeldige JSON"})

        pid  = body.get("project")
        naam = (body.get("naam") or "").strip()
        data = body.get("data")

        if not pid or not naam or data is None:
            return self._json(400, {"ok": False, "error": "project, naam en data zijn verplicht"})

        try:
            # Controleer bestaande draft op lock
            existing = _read_draft(pid)
            now = time.time()
            if existing:
                lock_naam = existing.get("naam", "")
                lock_ts   = existing.get("timestamp", 0)
                if lock_naam and lock_naam != naam and (now - lock_ts) < LOCK_TTL:
                    return self._json(423, {
                        "ok":        False,
                        "error":     "Formulier is in gebruik",
                        "locked_by": lock_naam,
                        "since":     lock_ts,
                    })

            draft = {
                "project":   pid,
                "naam":      naam,
                "timestamp": now,
                "data":      data,
            }
            _write_draft(pid, json.dumps(draft, ensure_ascii=False).encode())
            self._json(200, {"ok": True, "project": pid})
        except Exception as e:
            self._json(500, {"ok": False, "error": str(e)})

    def do_DELETE(self):
        pid = self._pid_from_qs()
        if not pid:
            return self._json(400, {"ok": False, "error": "project ontbreekt"})
        try:
            _delete_draft(pid)
            self._json(200, {"ok": True})
        except Exception as e:
            self._json(500, {"ok": False, "error": str(e)})

    def _pid_from_qs(self) -> str:
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        return params.get("project", [""])[0].strip()

    def _json(self, status: int, data: dict):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, *args):
        pass
