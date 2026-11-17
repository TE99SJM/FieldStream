import asyncio
import os
from contextlib import suppress

import simpleobsws

from .config import FILTER, GRID


class Obs:
    def __init__(self, cfg):
        self.cfg = cfg
        self.ws = None
        self.lock = asyncio.Lock()

    async def call(self, name, **data):
        async with self.lock:
            try:
                if not self.ws or not self.ws.is_identified():
                    if self.ws:
                        await self.ws.disconnect()
                    self.ws = simpleobsws.WebSocketClient(
                        url="ws://127.0.0.1:4455", password=os.getenv("OBS_PASSWORD", ""),
                        identification_parameters=simpleobsws.IdentificationParameters(eventSubscriptions=0))
                    await asyncio.wait_for(self.ws.connect(), 3)
                    if not await self.ws.wait_until_identified(timeout=3):
                        raise RuntimeError("Check OBS WebSocket password and authentication.")
                result = await self.ws.call(simpleobsws.Request(name, data), timeout=5)
            except Exception as exc:
                if self.ws:
                    with suppress(Exception):
                        await self.ws.disconnect()
                self.ws = None
                raise RuntimeError(f"OBS connection failed: {exc}") from exc
        if not result.ok():
            raise RuntimeError(f"{name}: {result.requestStatus.comment or result.requestStatus.code}")
        return result.responseData or {}

    async def close(self):
        if self.ws:
            await self.ws.disconnect()

    async def guard(self, idle=False):
        name = (await self.call("GetSceneCollectionList"))["currentSceneCollectionName"]
        if name != self.cfg.obs_collection:
            raise RuntimeError(f"Select the OBS scene collection named '{self.cfg.obs_collection}'.")
        if idle:
            for action in ("GetStreamStatus", "GetRecordStatus"):
                if (await self.call(action))["outputActive"]:
                    raise RuntimeError("Stop streaming and recording in OBS before this operation.")

    async def scene(self, name):
        await self.guard()
        await self.call("SetCurrentProgramScene", sceneName=name)

    def media_settings(self, cam):
        settings = dict(is_local_file=False, input=cam.url(self.cfg.dashboard_port),
                        restart_on_activate=False, close_when_inactive=False,
                        reconnect_delay_sec=1, buffering_mb=0, clear_on_media_end=True)
        if cam.transport == "usb":
            settings.update(input_format="mpjpeg", ffmpeg_options="use_wallclock_as_timestamps=1")
        return settings

    async def layout(self):
        await self.guard(idle=True)
        scenes = {x["sceneName"] for x in (await self.call("GetSceneList"))["scenes"]}
        inputs = {x["inputName"]: x["inputKind"] for x in (await self.call("GetInputList"))["inputs"]}
        video = await self.call("GetVideoSettings")
        width, height = video["baseWidth"], video["baseHeight"]
        cols, rows = self.cfg.grid
        desired = {GRID: [(c, i % cols, i // cols, cols, rows) for i, c in enumerate(self.cfg.cameras)]}
        desired.update({c.solo: [(c, 0, 0, 1, 1)] for c in self.cfg.cameras})
        for name, cells in desired.items():
            if name not in scenes:
                await self.call("CreateScene", sceneName=name)
            items = (await self.call("GetSceneItemList", sceneName=name))["sceneItems"]
            if name == GRID:
                active_inputs = {cam.source for cam in self.cfg.cameras}
                for entry in items:
                    source = entry["sourceName"]
                    if source.startswith("Hub • Input • ") and source not in active_inputs:
                        await self.call("RemoveSceneItem", sceneName=name, sceneItemId=entry["sceneItemId"])
            for cam, col, row, nx, ny in cells:
                matches = [i["sceneItemId"] for i in items if i["sourceName"] == cam.source]
                if len(matches) > 1:
                    raise RuntimeError(f"Duplicate items for {cam.label} in {name}. Keep one and retry.")
                settings = self.media_settings(cam)
                if cam.source not in inputs:
                    result = await self.call("CreateInput", sceneName=name, inputName=cam.source,
                                             inputKind="ffmpeg_source", inputSettings=settings, sceneItemEnabled=True)
                    inputs[cam.source] = "ffmpeg_source"
                    item = result["sceneItemId"]
                else:
                    if inputs[cam.source] != "ffmpeg_source":
                        raise RuntimeError(f"Name conflict: {cam.source}")
                    if name == GRID:
                        current = (await self.call("GetInputSettings", inputName=cam.source))["inputSettings"]
                        if any(current.get(k) != v for k, v in settings.items()):
                            await self.call("SetInputSettings", inputName=cam.source, inputSettings=settings)
                    item = matches[0] if matches else (await self.call(
                        "CreateSceneItem", sceneName=name, sourceName=cam.source, sceneItemEnabled=True))["sceneItemId"]
                await self.call("SetSceneItemTransform", sceneName=name, sceneItemId=item, sceneItemTransform={
                    "positionX": width * col / nx, "positionY": height * row / ny, "alignment": 5,
                    "scaleX": 1.0, "scaleY": 1.0, "rotation": 0.0, "boundsType": "OBS_BOUNDS_SCALE_INNER",
                    "boundsAlignment": 0, "boundsWidth": width / nx, "boundsHeight": height / ny,
                    "cropLeft": 0, "cropRight": 0, "cropTop": 0, "cropBottom": 0})
                await self.call("SetSceneItemEnabled", sceneName=name, sceneItemId=item, sceneItemEnabled=True)
                if name == GRID:
                    await self.call("SetInputMute", inputName=cam.source, inputMuted=True)
        return "Grid and solo scenes are ready."

    async def delays(self):
        values = {}
        for cam in self.cfg.cameras:
            filters = (await self.call("GetSourceFilterList", sourceName=cam.source))["filters"]
            own = self.owned(filters)
            values[cam.id] = sum(int(f["filterSettings"].get("delay_ms", 0)) for f in own if f["filterEnabled"])
        if (await self.call("GetInputMute", inputName=self.cfg.master_mic))["inputMuted"]:
            raise RuntimeError(f"Unmute the master input '{self.cfg.master_mic}' in OBS.")
        values["@audio"] = (await self.call("GetInputAudioSyncOffset", inputName=self.cfg.master_mic))["inputAudioSyncOffset"]
        return values

    def owned(self, filters):
        own = [f for f in filters if f["filterName"].startswith(FILTER + " ")]
        kind = "async_delay_filter" if self.cfg.delay_filter == "async" else "gpu_delay"
        for f in own:
            suffix = f["filterName"][len(FILTER) + 1:]
            if not suffix.isdigit() or int(suffix) < 1 or f["filterKind"] != kind:
                raise RuntimeError("Managed delay filter conflicts with the configured filter type.")
        return own

    async def video_delay(self, cam, value):
        if not 0 <= value <= 4000:
            raise ValueError("Video delay must be 0–4000 ms.")
        render = self.cfg.delay_filter == "render"
        size = 500 if render else 4000
        parts = [min(size, value - i) for i in range(0, value, size)]
        proposed = {f"{FILTER} {i}": ms for i, ms in enumerate(parts, 1)}
        current = self.owned((await self.call("GetSourceFilterList", sourceName=cam.source))["filters"])
        have = {f["filterName"]: f for f in current}
        for name, ms in proposed.items():
            args = dict(sourceName=cam.source, filterName=name)
            if name in have:
                if have[name]["filterSettings"].get("delay_ms") != ms:
                    await self.call("SetSourceFilterSettings", **args, filterSettings={"delay_ms": ms})
                if not have[name]["filterEnabled"]:
                    await self.call("SetSourceFilterEnabled", **args, filterEnabled=True)
            else:
                await self.call("CreateSourceFilter", **args, filterKind="gpu_delay" if render else "async_delay_filter",
                                filterSettings={"delay_ms": ms})
        for name in have.keys() - proposed.keys():
            await self.call("RemoveSourceFilter", sourceName=cam.source, filterName=name)

    async def audio_delay(self, value, previous):
        if previous == 0 and value > 1:
            await self.call("SetInputAudioSyncOffset", inputName=self.cfg.master_mic, inputAudioSyncOffset=1)
            await asyncio.sleep(0.25)
        await self.call("SetInputAudioSyncOffset", inputName=self.cfg.master_mic, inputAudioSyncOffset=value)

    async def apply(self, values, previous):
        if await self.delays() != previous:
            raise RuntimeError("OBS offsets changed during measurement. Run calibration again.")
        touched = []
        try:
            for cam in self.cfg.cameras:
                if previous[cam.id] != values[cam.id]:
                    touched.append(cam)
                    await self.video_delay(cam, values[cam.id])
            if previous["@audio"] != values["@audio"]:
                touched.append(None)
                await self.audio_delay(values["@audio"], previous["@audio"])
            if await self.delays() != values:
                raise RuntimeError("OBS did not retain the requested offsets.")
        except Exception as exc:
            failures = []
            for cam in reversed(touched):
                try:
                    if cam:
                        await self.video_delay(cam, previous[cam.id])
                    else:
                        await self.call("SetInputAudioSyncOffset", inputName=self.cfg.master_mic,
                                        inputAudioSyncOffset=previous["@audio"])
                except Exception as error:
                    failures.append(str(error))
            if failures:
                raise RuntimeError("Partial rollback. Check OBS filters and audio offsets: " + ", ".join(failures)) from exc
            raise
