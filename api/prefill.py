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
PREFILL_DIR = "General/HP Automatiseringen/prefills"
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


def _fetch_sp_file(path: str) -> dict | None:
    """Haal een JSON-bestand op van SharePoint. Retourneert None bij 404/fout."""
    drive_id = os.environ["DRIVE_ID"]
    token = _get_token()
    url = (
        f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/"
        f"{urllib.parse.quote(path, safe='/')}:/content"
    )
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        return json.loads(resp.read())
    except urllib.error.HTTPError:
        return None


def _compute_protocol_suggestie(pf: dict) -> tuple[str, list[str]]:
    """Bereken protocol_suggestie en protocollen op basis van discipline en velden.

    Returns: (suggestie, protocollen)
    """
    discipline = (pf.get("discipline") or pf.get("project_type") or "").upper()
    peilbuizen = int(pf.get("peilbuizen") or 0)
    analysepakket = (pf.get("analysepakket") or "").lower()

    # EQ disciplines — ook als de index al protocol_suggestie='eq' had ingesteld
    if discipline in ("EQ", "ECOLOGIE", "ECOLOGISCHE QUICKSCAN", "ECOLOGY"):
        return "eq", ["eq"]
    if str(pf.get("protocol_suggestie") or "").lower() == "eq":
        return "eq", ["eq"]
    if "eq" in [str(p).lower() for p in (pf.get("protocollen") or [])]:
        return "eq", ["eq"]

    # BRL 1000 disciplines
    if discipline in ("AP", "AP04", "BRL1000", "BRL1001", "1001"):
        return "1001", ["1001"]
    if discipline in ("BRL1002", "1002"):
        return "1002", ["1002"]

    # BRL 2000 disciplines (VE=verkennend, VO=vooronderzoek, default)
    protocollen = ["2001"]
    if peilbuizen > 0:
        protocollen.append("2002")
    if "asbest" in analysepakket:
        protocollen.append("2018")

    return "2001", protocollen


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
                else:
                    pf = dict(project.get("prefill") or {})

                    # Fallback: als index geen prefill heeft, probeer individueel bestand
                    if not pf:
                        sp_prefill = _fetch_sp_file(f"{PREFILL_DIR}/{pid}_prefill.json")
                        if sp_prefill:
                            pf = sp_prefill
                            pf["_bron"] = f"prefills/{pid}_prefill.json"

                    # Merge index-velden als fallback voor ontbrekende prefill-velden
                    for key in ("projectnummer", "adres", "opdrachtgever", "discipline"):
                        pf.setdefault(key, project.get(key, ""))
                    pf.setdefault("projectnummer", pid)

                    # protocol_suggestie: index-waarde heeft voorrang (berekend via sp_folder),
                    # daarna _compute als fallback. Lege string uit index = geen suggestie.
                    idx_ps = project.get("protocol_suggestie") or ""
                    if idx_ps:
                        pf.setdefault("protocol_suggestie", idx_ps)

                    # Bereken protocol-suggestie op basis van discipline/velden
                    suggestie, protocollen = _compute_protocol_suggestie(pf)
                    if "protocol_suggestie" not in pf:
                        pf["protocol_suggestie"] = suggestie
                    if "protocollen" not in pf:
                        pf["protocollen"] = protocollen

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
