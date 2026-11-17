"""Local, scene-only touch control for the OBS field hub."""
import argparse
import base64
import hmac
import json
import os
import re
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock

from scenes import SCENES, connect, request, require_field_collection

ADDRESS = ("192.168.77.10", 8088)
URL = f"http://{ADDRESS[0]}:{ADDRESS[1]}"
SECRET_FORMAT = re.compile(r"[A-Za-z0-9_~-]{20,128}\Z")


def pairing_key():
    external = os.getenv("FIELDHUB_PANEL_TOKEN")
    folder = Path(os.getenv("LOCALAPPDATA") or (Path.home() / ".config")) / "ForgeFieldHub"
    if external is not None:
        key = external
    else:
        folder.mkdir(parents=True, exist_ok=True)
        file = folder / "panel-token.txt"
        if not file.exists():
            try:
                with open(file, "x", encoding="ascii", opener=lambda p, f: os.open(p, f, 0o600)) as output:
                    output.write(secrets.token_urlsafe(22))
            except FileExistsError:
                pass
        key = file.read_text(encoding="ascii").strip()
    if not SECRET_FORMAT.fullmatch(key):
        raise ValueError("The touch panel key must be 20–128 URL-safe characters (letters, digits, _, ~ or -).")
    return key


class Controller:
    def __init__(self):
        self.client = None
        self.lock = Lock()

    def run(self, action):
        with self.lock:
            try:
                if self.client is None:
                    self.client = connect()
                return action(self.client)
            except Exception:
                if self.client:
                    try:
                        self.client.disconnect()
                    except Exception:
                        pass
                self.client = None
                raise


def state(client):
    require_field_collection(client)
    return {"scene": request(client, "GetCurrentProgramScene")["currentProgramSceneName"],
            "streaming": request(client, "GetStreamStatus")["outputActive"],
            "scenes": SCENES}


def snapshot(client):
    require_field_collection(client)
    scene = request(client, "GetCurrentProgramScene")["currentProgramSceneName"]
    if scene not in SCENES:
        raise ValueError("Select a Field scene first.")
    result = request(client, "GetSourceScreenshot", sourceName=scene, imageFormat="jpg",
                     imageWidth=640, imageHeight=360, imageCompressionQuality=72)
    _, data = result["imageData"].split(",", 1)
    return base64.b64decode(data, validate=True)


class Handler(BaseHTTPRequestHandler):
    server_version = "FieldPanel"

    def log_message(self, fmt, *args):
        # Pairing values and operator actions do not belong in access logs.
        pass

    def reply(self, status, body, media="application/json"):
        if isinstance(body, dict):
            body = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", media)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def authorized(self):
        header = self.headers.get("Authorization", "")
        expected = ("Bearer " + self.server.key).encode()
        if not hmac.compare_digest(header.encode(), expected):
            self.reply(401, {"error": "Pair this device with the hub."})
            return False
        return True

    def allowed_origin(self):
        origin = self.headers.get("Origin")
        if origin and origin != self.server.origin:
            self.reply(403, {"error": "Open the touch panel directly on the camera network."})
            return False
        return True

    def in_obs(self, fn, media="application/json"):
        try:
            result = self.server.controller.run(fn)
        except ValueError as error:
            self.reply(409, {"error": str(error)})
        except Exception:
            self.reply(503, {"error": "OBS is unavailable. Check OBS and its WebSocket connection on the hub."})
        else:
            self.reply(200, result, media)

    def do_GET(self):
        static = {"/": ("panel.html", "text/html; charset=utf-8"),
                  "/panel.js": ("panel.js", "application/javascript; charset=utf-8"),
                  "/panel.css": ("panel.css", "text/css; charset=utf-8"),
                  "/calibration/Sync-Target.html": ("../calibration/Sync-Target.html", "text/html; charset=utf-8"),
                  "/calibration/target.js": ("../calibration/target.js", "application/javascript; charset=utf-8"),
                  "/calibration/target.css": ("../calibration/target.css", "text/css; charset=utf-8")}
        if self.path in static:
            filename, media = static[self.path]
            self.reply(200, (Path(__file__).parent / filename).read_bytes(), media)
        elif self.path in ("/state", "/snapshot"):
            if self.allowed_origin() and self.authorized():
                if self.path == "/snapshot":
                    self.in_obs(snapshot, "image/jpeg")
                else:
                    self.in_obs(state)
        else:
            self.reply(404, {"error": "Unknown control."})

    def do_POST(self):
        if self.path != "/scene":
            self.reply(404, {"error": "Unknown control."})
            return
        if not self.allowed_origin() or not self.authorized():
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not (1 <= length <= 512) or self.headers.get_content_type() != "application/json":
                raise ValueError
            name = json.loads(self.rfile.read(length))["scene"]
            if name not in SCENES:
                raise ValueError
        except (ValueError, KeyError, TypeError, UnicodeError):
            self.reply(400, {"error": "Choose a Field scene."})
            return

        def switch(client):
            require_field_collection(client)
            request(client, "SetCurrentProgramScene", sceneName=name)
            return state(client)
        self.in_obs(switch)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--show-url", action="store_true", help="Print the pairing link and exit")
    args = parser.parse_args()
    key = pairing_key()
    if args.show_url:
        print(f"Touch panel pairing link: {URL}/#{key}", flush=True)
        return
    print(f"Touch panel listening on {URL}", flush=True)
    with ThreadingHTTPServer(ADDRESS, Handler) as server:
        server.controller = Controller()
        server.origin = URL
        server.key = key
        server.serve_forever()


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        sys.exit(str(error) or "Stopped")
