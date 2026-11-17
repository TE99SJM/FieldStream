from dataclasses import dataclass
from math import ceil, sqrt
from pathlib import Path
import re

import yaml

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "runtime"
GRID = "Hub • Grid"
FILTER = "Hub • Camera delay"


@dataclass(frozen=True)
class Camera:
    id: str
    label: str
    transport: str
    device: str = ""
    width: int = 1920
    height: int = 1080
    fps: int | float | str = 30
    format: str = "auto"
    device_number: int = 0

    @property
    def source(self):
        return "Hub • Input • " + self.id

    @property
    def solo(self):
        return "Hub • Solo • " + self.id

    @property
    def path(self):
        return "live/" + self.id

    def url(self, port):
        if self.transport == "usb":
            return f"http://127.0.0.1:{port}/video/{self.id}.mjpg"
        return "rtmp://127.0.0.1:1935/" + self.path


@dataclass(frozen=True)
class Config:
    dashboard_port: int
    obs_collection: str
    master_mic: str
    delay_filter: str
    cameras: tuple

    @property
    def grid(self):
        cols = ceil(sqrt(len(self.cameras)))
        return cols, ceil(len(self.cameras) / cols)

    @property
    def wireless(self):
        return [c for c in self.cameras if c.transport == "rtmp"]


def load_config(path=ROOT / "server_config.yml"):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    cameras = tuple(Camera(**row) for row in data.pop("cameras"))
    cfg = Config(cameras=cameras, **data)
    if not 1 <= len(cameras) <= 16 or len({c.id for c in cameras}) != len(cameras):
        raise ValueError("Use 1–16 cameras with unique IDs.")
    for c in cameras:
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,30}", c.id) or not c.label:
            raise ValueError(f"Invalid camera ID or label: {c.id}")
        if c.transport not in ("usb", "rtmp"):
            raise ValueError(f"Unknown transport: {c.transport}")
        if c.transport == "usb" and (not c.device or c.format not in ("mjpeg", "yuy2", "auto")
                or str(c.fps) not in ("24", "25", "30", "50", "60", "29.97", "59.94", "30000/1001", "60000/1001") or not 16 <= c.width <= 4096
                or not 16 <= c.height <= 2160 or c.device_number < 0):
            raise ValueError(f"Invalid capture profile for {c.id}")
    wired = [(c.device.casefold(), c.device_number) for c in cameras if c.transport == "usb"]
    if len(wired) != len(set(wired)):
        raise ValueError("Two inputs select the same DirectShow device.")
    if not 1024 <= cfg.dashboard_port <= 65535 or cfg.dashboard_port in (1935, 4455, 9997):
        raise ValueError("Choose an unused dashboard port from 1024–65535.")
    if cfg.delay_filter not in ("async", "render") or not cfg.master_mic or not cfg.obs_collection:
        raise ValueError("Check delay_filter, master_mic and obs_collection.")
    return cfg
