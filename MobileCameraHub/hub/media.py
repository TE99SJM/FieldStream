import asyncio
from contextlib import suppress
import os
from pathlib import Path
from uuid import UUID

import httpx
import yaml

from .config import ROOT, STATE
from .process import spawn, stop


def receiver_config(cfg):
    return {
        "logLevel": "warn", "api": True, "apiAddress": "127.0.0.1:9997",
        "rtmp": True, "rtmpAddress": "0.0.0.0:1935", "rtsp": False, "hls": False,
        "webrtc": False, "srt": False, "moq": False, "playback": False,
        "authInternalUsers": [
            {"user": "any", "ips": ["192.168.77.0/24", "127.0.0.1"], "permissions": [{"action": "publish", "path": c.path} for c in cfg.wireless]},
            {"user": "any", "ips": ["127.0.0.1", "::1"],
             "permissions": [{"action": "read"}, {"action": "api"}]}],
        "pathDefaults": {"overridePublisher": False},
        "paths": {c.path: {} for c in cfg.wireless}
    }


class Receiver:
    def __init__(self, cfg, feeds):
        self.cfg = cfg
        self.feeds = feeds
        self.http = httpx.AsyncClient(base_url="http://127.0.0.1:9997/v3/", timeout=2, trust_env=False)
        self.process = None
        self.online = False
        self.paths = {}
        self.task = None
        self.detail = "Starting receiver"
        self.generation = {}
        self.probes = {}

    async def start(self):
        binary = Path(os.getenv("HUB_MEDIAMTX", str(ROOT / "bin" / "mediamtx.exe")))
        config_file = STATE / "mediamtx.yml"
        config_file.write_text(yaml.safe_dump(receiver_config(self.cfg), sort_keys=False), encoding="utf-8")
        if not binary.is_file():
            raise RuntimeError(f"MediaMTX executable missing: {binary}")
        log = (STATE / "mediamtx.log").open("ab")
        try:
            self.process = await spawn(binary, config_file, stdout=log, stderr=log)
        finally:
            log.close()
        for _ in range(40):
            await asyncio.sleep(0.1)
            if self.process.returncode is not None:
                raise RuntimeError("Receiver stopped during startup. See runtime/mediamtx.log and check ports 1935/9997.")
            try:
                response = await self.http.get("paths/list")
                response.raise_for_status()
                self.task = asyncio.create_task(self.poll())
                return
            except httpx.HTTPError:
                continue
        raise RuntimeError("MediaMTX control API did not become ready. See runtime/mediamtx.log.")

    async def poll(self):
        from .capture import worker, read_counts, probe_args
        while True:
            try:
                if self.process.returncode is not None:
                    raise RuntimeError("Receiver exited. Restart the hub and check runtime/mediamtx.log.")
                response = await self.http.get("paths/list", params={"itemsPerPage": 100})
                response.raise_for_status()
                self.paths = {item["name"]: item for item in response.json()["items"]}
                self.online, self.detail = True, ""
            except (httpx.HTTPError, RuntimeError, ValueError, KeyError) as exc:
                self.online, self.detail, self.paths = False, str(exc), {}
            for cam in self.cfg.wireless:
                path = self.paths.get(cam.path, {})
                source = path.get("source") or {}
                session = source.get("id") if path.get("online") and source.get("type") == "rtmpConn" else None
                if session != self.generation.get(cam.id):
                    await self.stop_probe(cam.id)
                    self.generation[cam.id] = session
                    if session:
                        self.probes[cam.id] = asyncio.create_task(
                            worker(self.feeds[cam.id], probe_args(cam.url(self.cfg.dashboard_port)), read_counts))
            await asyncio.sleep(1)

    async def stop_probe(self, camera_id):
        task = self.probes.pop(camera_id, None)
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        self.feeds[camera_id].clear("Waiting for publisher")

    async def reset(self, cam):
        if self.process is None or self.process.returncode is not None:
            raise RuntimeError("Receiver is offline.")
        response = await self.http.get("paths/get/" + cam.path)
        if response.status_code == 404:
            return "Endpoint is ready. Start the camera's custom RTMP stream."
        response.raise_for_status()
        src = response.json().get("source") or {}
        if src.get("type") != "rtmpConn" or not src.get("id"):
            return "Endpoint is ready. Start the camera's custom RTMP stream."
        conn = str(UUID(src["id"]))
        response = await self.http.get("rtmp/conns/get/" + conn)
        if response.status_code == 404:
            return "Publisher already disconnected."
        response.raise_for_status()
        item = response.json()
        if item["state"] != "publish" or item["path"] != cam.path:
            raise RuntimeError("Publisher changed. Refresh and try again.")
        response = await self.http.post("rtmp/conns/kick/" + conn)
        if response.status_code != 404:
            response.raise_for_status()
        return "Publisher disconnected. If the camera does not reconnect, restart it in its app."

    async def close(self):
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
        for camera_id in list(self.probes):
            await self.stop_probe(camera_id)
        if self.process:
            await stop(self.process)
        await self.http.aclose()
