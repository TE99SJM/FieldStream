"""FieldStream dashboard, same-origin WHEP signaling and OBS control."""
import asyncio
import hmac
import logging
import os
import re
import time
from contextlib import asynccontextmanager, suppress
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response

from obs_setup import call, client
from settings import API, OBS_NAME, ROOT, WHEP, cameras, path, scene

log = logging.getLogger("uvicorn.error")
CAMS = cameras()


class Monitor:
    def __init__(self):
        self.ws = None
        self.lock = asyncio.Lock()
        self.http = httpx.AsyncClient(trust_env=False, timeout=3)
        self.progress = {}

    async def media(self):
        try:
            r = await self.http.get(API + "/v3/paths/list", params={"itemsPerPage": 1000})
            r.raise_for_status()
            paths = {p["name"]: p for p in r.json()["items"] if p["online"]}
        except (httpx.HTTPError, KeyError, ValueError):
            self.progress.clear()
            return False, {}
        now = time.monotonic()
        result = {}
        for name, p in paths.items():
            n = p["inboundBytes"]
            prior, last = self.progress.get(name, (None, now))
            last = now if n != prior else last
            self.progress[name] = n, last
            result[name] = "stalled" if now-last > 5 else "ready" if n else "starting"
        self.progress = {k:v for k,v in self.progress.items() if k in paths}
        return True, result

    async def obs(self):
        idle = {"connected": False, "collection": None, "scene": None, "stream": None, "record": None}
        try:
            if self.ws is None or not self.ws.is_identified():
                if self.ws:
                    await self.ws.disconnect()
                self.ws = client()
                await asyncio.wait_for(self.ws.connect(), 2)
                if not await self.ws.wait_until_identified(2):
                    raise RuntimeError("OBS identification failed")
            collection, program, stream, record = await asyncio.gather(
                call(self.ws, "GetSceneCollectionList"), call(self.ws, "GetCurrentProgramScene"),
                call(self.ws, "GetStreamStatus"), call(self.ws, "GetRecordStatus"))
            return {"connected": True, "collection": collection["currentSceneCollectionName"],
                    "scene": program["currentProgramSceneName"], "stream": stream["outputActive"],
                    "record": record["outputActive"]}
        except Exception as exc:
            log.debug("OBS unavailable: %s", exc)
            if self.ws:
                with suppress(Exception):
                    await self.ws.disconnect()
                self.ws = None
            return idle

    async def state(self):
        async with self.lock:
            (ok, paths), obs = await asyncio.gather(self.media(), self.obs())
            return {"media": ok, "obs": obs, "collection": OBS_NAME, "cameras": [
                {"id": k, "name": c["name"], "link": c["link"], "scene": scene(k),
                 **{tag: paths.get(path(k)+suffix, "offline") for tag,suffix in
                    (("ingest",""),("hq","_hq"),("proxy","_proxy"))}}
                for k,c in CAMS.items()]}


@asynccontextmanager
async def lifespan(app):
    if not os.getenv("FIELDSTREAM_KEY"):
        raise RuntimeError("Start with launch.py or set FIELDSTREAM_KEY.")
    app.state.monitor = Monitor()
    yield
    m = app.state.monitor
    if m.ws:
        await m.ws.disconnect()
    await m.http.aclose()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/")
async def home():
    return FileResponse(ROOT / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/api/status")
async def status(request: Request):
    return await request.app.state.monitor.state()


def valid(key):
    if key not in CAMS:
        raise HTTPException(404, "Unknown camera")


@app.post("/api/take/{key}")
async def take(key: str, request: Request):
    token = request.headers.get("authorization", "").encode()
    if not hmac.compare_digest(token, ("Bearer "+os.environ["FIELDSTREAM_KEY"]).encode()):
        raise HTTPException(401, "Enter the operator key from the hub terminal.")
    valid(key)
    m = request.app.state.monitor
    async with m.lock:
        ok, paths = await m.media()
        if not ok or paths.get(path(key)+"_hq") != "ready":
            raise HTTPException(409, "The HQ feed is unavailable or stalled.")
        obs = await m.obs()
        if not obs["connected"] or obs["collection"] != OBS_NAME:
            raise HTTPException(503, "Connect OBS and select the FieldStream Dual Hub collection.")
        try:
            wanted = scene(key)
            await call(m.ws, "SetCurrentProgramScene", sceneName=wanted)
            actual = await call(m.ws, "GetCurrentProgramScene")
            return {"scene": actual["currentProgramSceneName"]}
        except Exception as exc:
            raise HTTPException(502, "OBS could not confirm the cut. Check the scene collection.") from exc


@app.post("/api/preview/{key}")
async def offer(key: str, request: Request):
    valid(key)
    if request.headers.get("content-type","").split(";")[0] != "application/sdp":
        raise HTTPException(415, "Expected application/sdp")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 65536:
            raise HTTPException(413, "Offer too large")
    target = "/" + path(key) + "_proxy/whep"
    try:
        r = await request.app.state.monitor.http.post(WHEP + target, content=bytes(data),
                headers={"Content-Type":"application/sdp"}, timeout=9)
    except httpx.HTTPError as exc:
        raise HTTPException(503, "Preview signaling is unavailable.") from exc
    if r.status_code != 201:
        raise HTTPException(503, "Preview is starting or unavailable.")
    location = urlsplit(r.headers.get("location","")).path
    match = re.fullmatch(re.escape(target) + r"/([0-9a-fA-F-]{36})", location)
    if not match:
        raise HTTPException(502, "Unexpected WHEP session address.")
    return Response(r.content, status_code=201, media_type="application/sdp",
                    headers={"Location": f"/api/preview/{key}/{match[1]}"})


@app.delete("/api/preview/{key}/{sid}", status_code=204)
async def close_preview(key: str, sid: UUID, request: Request):
    valid(key)
    with suppress(httpx.HTTPError):
        await request.app.state.monitor.http.delete(f"{WHEP}/{path(key)}_proxy/whep/{sid}")
    return Response(status_code=204)
