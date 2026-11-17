#!/usr/bin/env python3
"""Operate one USB-attached GoPro on Linux. Uses iproute2 and the standard library."""

import argparse
from dataclasses import dataclass
import http.client
import ipaddress
import json
from pathlib import Path
import re
import subprocess
import sys
import time


class CameraError(Exception):
    pass


@dataclass(frozen=True)
class Camera:
    interface: str
    product: str
    suffix: str

    @property
    def address(self):
        a, b, c = self.suffix
        return f"172.2{a}.1{b}{c}.51"


def usb_cameras(net_root=Path("/sys/class/net")):
    if not net_root.is_dir():
        raise CameraError("Linux network sysfs is unavailable. Run this on the camera host with /sys/class/net mounted.")
    cameras = []
    for interface in sorted(net_root.iterdir()):
        device = (interface / "device").resolve()
        for ancestor in (device, *device.parents):
            if not (ancestor / "idVendor").is_file():
                continue
            # Stop at the physical peripheral, even if it sits under a USB hub.
            if (ancestor / "idVendor").read_text().strip().lower() == "2672":
                serial_file = ancestor / "serial"
                if serial_file.is_file():
                    match = re.search(r"([0-9]{3})$", serial_file.read_text().strip())
                    if match:
                        product_file = ancestor / "product"
                        product = product_file.read_text().strip() if product_file.is_file() else "GoPro"
                        cameras.append(Camera(interface.name, product, match[1]))
            break
    return cameras


def ip_json(*args):
    result = subprocess.run(
        ["ip", "-j", "-4", *args], capture_output=True, text=True, timeout=5
    )
    if result.returncode:
        raise CameraError(result.stderr.strip() or "iproute2 did not return the requested network information.")
    return json.loads(result.stdout)


def usb_host_address(camera):
    """Use an on-link IPv4 address and verify the route without overriding it."""
    address = ipaddress.IPv4Address(camera.address)
    candidates = []
    for link in ip_json("address", "show", "dev", camera.interface):
        for addr in link.get("addr_info", []):
            if addr.get("family") != "inet":
                continue
            local = ipaddress.IPv4Interface(f"{addr['local']}/{addr['prefixlen']}")
            if local.ip != address and address in local.network:
                candidates.append(str(local.ip))
    if not candidates:
        raise CameraError(f"No on-link USB IPv4 address for {camera.address} on {camera.interface}. "
                          "Power on the GoPro, enable IPv4 DHCP for that USB interface, and retry.")
    for local in candidates:
        routes = ip_json("route", "get", camera.address, "from", local)
        if any(r.get("dev") == camera.interface and not r.get("gateway")
               and r.get("type", "unicast") == "unicast" for r in routes):
            return local
    raise CameraError(f"The kernel routes {camera.address} outside {camera.interface} or through a gateway. "
                      "Check for overlapping camera, LAN, VPN, or container subnets.")


def choose(cameras, interface=None, serial=None):
    selected = [c for c in cameras if (not interface or c.interface == interface)
                and (not serial or c.suffix == serial)]
    if len(selected) != 1:
        if not selected:
            raise CameraError("No matching GoPro USB network device. Check cable, power, network mode and filters. "
                              "Use 'list' and 'ip -br address' to inspect.")
        raise CameraError("Multiple GoPro USB interfaces found. Select one with --interface or --serial.")
    return selected[0]


STATES = {0: "off", 1: "idle", 2: "streaming", 3: "low-power preview"}


class Controller:
    def __init__(self, camera, host, *, http_port=8080):
        self.camera = camera
        self.host = host
        self.http_port = http_port

    def call(self, path):
        # Direct TCP, no DNS, redirects or proxy. Bind the source used for the camera's reply stream.
        conn = http.client.HTTPConnection(
            self.camera.address, self.http_port, timeout=4, source_address=(self.host, 0)
        )
        try:
            conn.request("GET", "/gopro/" + path)
            response = conn.getresponse()
            raw = response.read(65537)
            if response.status != 200:
                raise CameraError(f"GoPro {path} returned HTTP {response.status}. "
                                  "Check the camera's idle state and its firmware.")
            if len(raw) > 65536:
                raise CameraError("Unexpected GoPro response size.")
            result = json.loads(raw)
        finally:
            conn.close()
        if not isinstance(result, dict):
            raise CameraError(f"Unexpected GoPro reply to {path}.")
        if (path.startswith("webcam/") or "error" in result) and result.get("error") != 0:
            raise CameraError(f"GoPro rejected {path}: {result}.")
        return result

    def status(self):
        status = self.call("webcam/status").get("status")
        if type(status) is not int or status not in STATES:
            raise CameraError(f"Unknown GoPro webcam state: {status!r}.")
        return status

    def wait(self, expected):
        deadline = time.monotonic() + 7
        while True:
            current = self.status()
            if current in expected:
                return current
            if time.monotonic() >= deadline:
                raise CameraError(f"GoPro remained {STATES[current]}. Use 'status' before retrying.")
            time.sleep(0.25)

    def start(self):
        state = self.status()
        if state == 2:
            return "Already streaming. Existing settings were preserved. Check the live picture in OBS."
        if state == 3:
            raise CameraError("Another client has a preview session. Close it or run 'stop' before 'start'.")
        # GoPro's multi-webcam reference releases USB command-and-control before starting webcam.
        self.call("camera/control/wired_usb?p=0")
        self.call("webcam/start?res=12&port=8554&protocol=TS")
        self.wait({2})
        return "GoPro reports 1080p webcam mode started. Confirm moving video in OBS."

    def stop(self):
        state = self.status()
        if state in (2, 3):
            self.call("webcam/stop")
            state = self.wait({0, 1})
        if state == 1:
            self.call("webcam/exit")
        # Some firmware reports idle rather than off on a USB connection even after exit.
        self.wait({0, 1})
        return "GoPro webcam stream is stopped."


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["list", "address", "status", "start", "stop"])
    parser.add_argument("--interface", help="Linux GoPro USB network interface, for example enx...")
    parser.add_argument("--serial", metavar="LAST3", help="Last three decimal digits of the GoPro serial")
    args = parser.parse_args(argv)
    if args.serial and not re.fullmatch(r"[0-9]{3}", args.serial):
        parser.error("--serial requires the last three decimal digits, including leading zeroes")
    if not sys.platform.startswith("linux"):
        parser.error("This helper requires Linux sysfs and iproute2.")
    try:
        cameras = usb_cameras()
        if args.command == "list":
            if not cameras:
                print("No GoPro USB network interfaces detected.")
            for camera in cameras:
                try:
                    local = usb_host_address(camera)
                except CameraError as exc:
                    local = str(exc)
                print(f"{camera.interface}  {camera.product}  serial …{camera.suffix}  "
                      f"camera {camera.address}  host {local}")
            return 0
        camera = choose(cameras, args.interface, args.serial)
        local = usb_host_address(camera)
        if args.command == "address":
            print(local)
            return 0
        control = Controller(camera, local)
        if args.command == "status":
            print(STATES[control.status()])
        elif args.command == "start":
            print(control.start())
            print(f"OBS Media Source: udp://{local}:8554?localaddr={local}&localport=8554")
        else:
            print(control.stop())
        return 0
    except (CameraError, OSError, ValueError, subprocess.TimeoutExpired, http.client.HTTPException) as exc:
        print(f"GoPro USB: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
