import argparse
import getpass
import os
import secrets
import socket
import sys

import uvicorn

from hub.capture import devices, modes
from hub.config import load_config
from hub.web import make_app


def main():
    cli = argparse.ArgumentParser(description="Multi-camera mobile streaming hub for Windows")
    sub = cli.add_subparsers(dest="command", required=True)
    sub.add_parser("devices", help="List Windows DirectShow devices")
    profiles = sub.add_parser("modes", help="List capture modes for one Windows device")
    profiles.add_argument("device")
    profiles.add_argument("--number", type=int, default=0)
    sub.add_parser("serve", help="Start the receiver, cameras and touch controls")
    args = cli.parse_args()
    if args.command == "devices":
        devices()
        return
    if args.command == "modes":
        modes(args.device, args.number)
        return
    cfg = load_config()
    if "OBS_PASSWORD" not in os.environ and sys.stdin.isatty():
        os.environ["OBS_PASSWORD"] = getpass.getpass("OBS WebSocket password: ")
    token = os.getenv("HUB_CONTROL_KEY") or secrets.token_hex(8)
    if len(token) < 12 or not token.isascii() or not token.isprintable():
        raise ValueError("Use a control key of at least 12 printable ASCII characters.")
    print(f"Control key: {token}", flush=True)
    print(f"Hub dashboard: http://127.0.0.1:{cfg.dashboard_port}")
    ips = sorted({v[4][0] for v in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
                  if not v[4][0].startswith("127.")})
    for ip in ips:
        print(f"LAN dashboard: http://{ip}:{cfg.dashboard_port}")
        for cam in cfg.wireless:
            print(f"  {cam.label}: rtmp://{ip}:1935/{cam.path}")
    print("Use the IPv4 address of the field Wi-Fi adapter.", flush=True)
    uvicorn.run(make_app(cfg, token), host="0.0.0.0", port=cfg.dashboard_port,
                proxy_headers=False, access_log=False, timeout_graceful_shutdown=4)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, RuntimeError) as exc:
        sys.exit(str(exc))
