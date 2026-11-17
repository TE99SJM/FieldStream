import asyncio
from contextlib import asynccontextmanager
import hmac

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import httpx

from .calibration import Calibration
from .capture import Feed, capture_args, read_jpegs, worker
from .config import GRID, ROOT, STATE
from .media import Receiver
from .obs import Obs


def make_app(cfg, token):
    feeds = {cam.id: Feed(cam) for cam in cfg.cameras}
    receiver = Receiver(cfg, feeds)
    obs = Obs(cfg)
    calibration = Calibration(cfg, obs, feeds)
    actions = asyncio.Lock()
    status = {"connected": False, "scene": None, "on_air": None, "fps": None, "collection_ok": False}
    scenes = [{"id": "grid", "label": "All cameras", "name": GRID}]
    scenes += [{"id": "solo-" + c.id, "label": c.label, "name": c.solo} for c in cfg.cameras]
    scene_map = {s["id"]: s["name"] for s in scenes}

    async def poll_obs():
        while True:
            try:
                scene_list = await obs.call("GetSceneList")
                collection = await obs.call("GetSceneCollectionList")
                stats = await obs.call("GetStats")
                stream = await obs.call("GetStreamStatus")
                status.update(connected=True, scene=scene_list["currentProgramSceneName"],
                              on_air=stream["outputActive"], fps=round(stats["activeFps"], 1),
                              collection_ok=collection["currentSceneCollectionName"] == cfg.obs_collection,
                              existing=[s["sceneName"] for s in scene_list["scenes"]], detail="")
            except RuntimeError as exc:
                status.update(connected=False, scene=None, on_air=None, fps=None,
                              collection_ok=False, existing=[], detail=str(exc))
            await asyncio.sleep(2)

    @asynccontextmanager
    async def lifespan(app):
        tasks = []
        try:
            STATE.mkdir(exist_ok=True)
            await receiver.start()
            tasks.append(asyncio.create_task(poll_obs()))
            for feed in feeds.values():
                if feed.cam.transport == "usb":
                    tasks.append(asyncio.create_task(worker(feed, capture_args(feed.cam), read_jpegs)))
            yield
        finally:
            await calibration.close()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await receiver.close()
            await obs.close()

    app = FastAPI(title="Mobile Camera Hub", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    def auth(authorization: str = Header(default="")):
        if not hmac.compare_digest(authorization.encode(), ("Bearer " + token).encode()):
            raise HTTPException(401, "Enter the control key shown on the hub.")

    guarded = [Depends(auth)]

    @app.exception_handler(RuntimeError)
    @app.exception_handler(httpx.HTTPError)
    async def dependency_error(request, error):
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @app.get("/")
    async def home():
        return FileResponse(ROOT / "dashboard" / "index.html")

    @app.get("/slate")
    async def slate():
        return FileResponse(ROOT / "dashboard" / "slate.html")

    @app.get("/api/status", dependencies=guarded)
    async def health():
        cameras = []
        for feed in feeds.values():
            item = feed.status()
            if feed.cam.transport == "rtmp":
                path = receiver.paths.get(feed.cam.path, {})
                item["publisher"] = bool(path.get("online")) if receiver.online else None
            cameras.append(item)
        return {"obs": status.copy(), "receiver": receiver.online, "receiver_detail": receiver.detail,
                "cameras": cameras, "scenes": scenes, "calibration": calibration.status.copy()}

    def idle():
        if calibration.active:
            raise RuntimeError("Calibration is recording or analyzing. Wait for its result.")

    @app.post("/api/layout", dependencies=guarded)
    async def layout():
        async with actions:
            idle()
            return {"message": await obs.layout()}

    @app.post("/api/scenes/{scene_id}", dependencies=guarded)
    async def switch(scene_id: str):
        if scene_id not in scene_map:
            raise HTTPException(404, "Unknown scene.")
        async with actions:
            idle()
            await obs.scene(scene_map[scene_id])
            status["scene"] = scene_map[scene_id]
            return {"message": "Program scene switched."}

    @app.post("/api/reset/{camera_id}", dependencies=guarded)
    async def reset(camera_id: str):
        feed = feeds.get(camera_id)
        if not feed or feed.cam.transport != "rtmp":
            raise HTTPException(404, "Unknown wireless camera.")
        async with actions:
            idle()
            return {"message": await receiver.reset(feed.cam)}

    @app.post("/api/calibration/{mode}", dependencies=guarded)
    async def calibrate(mode: str):
        if mode not in ("apply", "verify"):
            raise HTTPException(404, "Unknown calibration mode.")
        async with actions:
            await calibration.start(mode)
            return {"message": "Recording started."}

    @app.get("/api/preview", dependencies=guarded)
    async def preview():
        scene = (await obs.call("GetCurrentProgramScene"))["currentProgramSceneName"]
        data = await obs.call("GetSourceScreenshot", sourceName=scene, imageFormat="jpg",
                              imageWidth=480, imageCompressionQuality=48)
        return {"image": data["imageData"]}

    @app.get("/video/{camera_id}.mjpg")
    async def video(camera_id: str, request: Request):
        if not request.client or request.client.host != "127.0.0.1":
            raise HTTPException(403, "This camera bridge is local to OBS.")
        feed = feeds.get(camera_id)
        if not feed or feed.cam.transport != "usb":
            raise HTTPException(404, "Unknown wired camera.")
        if feed.frame is None or feed.status()["state"] != "receiving":
            raise HTTPException(503, "Waiting for camera.")
        return StreamingResponse(feed.multipart(), media_type="multipart/x-mixed-replace; boundary=hub",
                                 headers={"Cache-Control": "no-store"})

    app.mount("/assets", StaticFiles(directory=ROOT / "dashboard"), name="assets")
    return app
