"""Provision the dedicated FieldStream profile and collection through OBS v5."""
import asyncio
import os
import time

import simpleobsws

from settings import OBS_NAME, OBS_URL, cameras, path, recording_dir, scene


def client():
    return simpleobsws.WebSocketClient(
        url=OBS_URL, password=os.getenv("OBS_PASSWORD", ""),
        identification_parameters=simpleobsws.IdentificationParameters(eventSubscriptions=0),
    )


async def call(ws, name, **params):
    response = await ws.call(simpleobsws.Request(name, params or None), timeout=4)
    if not response.ok():
        raise RuntimeError(f"{name}: {response.requestStatus.comment or response.requestStatus.code}")
    return response.responseData or {}


async def select_group(ws, kind):
    field = "profile" if kind == "Profile" else "sceneCollection"
    current = "current" + kind + "Name"
    listing = "Get" + kind + "List"
    status = await call(ws, listing)
    if status[current] == OBS_NAME:
        return
    verb = "SetCurrent" if OBS_NAME in status[field + "s"] else "Create"
    await call(ws, verb + kind, **{field + "Name": OBS_NAME})
    for _ in range(50):
        await asyncio.sleep(.2)
        if (await call(ws, listing))[current] == OBS_NAME:
            return
    raise RuntimeError(f"Select the {OBS_NAME} {kind} in OBS and retry.")


async def install_camera(ws, key, cfg, scenes, inputs):
    name = scene(key)
    source = f"FS HQ / {key}"
    if name not in scenes:
        await call(ws, "CreateScene", sceneName=name)
    opts = {"is_local_file": False, "input": f"rtsp://127.0.0.1:8554/{path(key)}_hq",
            "ffmpeg_options": "rtsp_transport=tcp", "restart_on_activate": False,
            "close_when_inactive": False, "reconnect_delay_sec": 1,
            "clear_on_media_end": True}
    if source not in inputs:
        await call(ws, "CreateInput", sceneName=name, inputName=source,
                   inputKind="ffmpeg_source", inputSettings=opts, sceneItemEnabled=True)
    else:
        await call(ws, "SetInputSettings", inputName=source, inputSettings=opts, overlay=True)
    items = (await call(ws, "GetSceneItemList", sceneName=name))["sceneItems"]
    item = next((i["sceneItemId"] for i in items if i["sourceName"] == source), None)
    if item is None:
        item = (await call(ws, "CreateSceneItem", sceneName=name, sourceName=source))["sceneItemId"]
    await call(ws, "SetSceneItemTransform", sceneName=name, sceneItemId=item,
               sceneItemTransform={"positionX": 0.0, "positionY": 0.0, "alignment": 5,
                                   "boundsType": "OBS_BOUNDS_SCALE_INNER", "boundsWidth": 1920.0,
                                   "boundsHeight": 1080.0, "boundsAlignment": 0})
    # Render Delay accepts up to 500 ms per filter. Stack only what was measured.
    prefix = "FieldStream delay "
    filters = (await call(ws, "GetSourceFilterList", sourceName=source))["filters"]
    for f in filters:
        if f["filterName"].startswith(prefix):
            await call(ws, "RemoveSourceFilter", sourceName=source, filterName=f["filterName"])
    delay = cfg["delay_ms"]
    for i, start in enumerate(range(0, delay, 500), 1):
        await call(ws, "CreateSourceFilter", sourceName=source, filterName=f"{prefix}{i}",
                   filterKind="gpu_delay", filterSettings={"delay_ms": min(500, delay-start)})
    await call(ws, "SetInputAudioSyncOffset", inputName=source, inputAudioSyncOffset=delay)


async def configure(record_format="hybrid_mp4"):
    ws = client()
    limit = time.monotonic() + 30
    while True:
        try:
            await asyncio.wait_for(ws.connect(), 3)
            break
        except (OSError, TimeoutError):
            if time.monotonic() >= limit:
                raise RuntimeError("Enable OBS WebSocket on port 4455 and run setup again.")
            await asyncio.sleep(.5)
    try:
        if not await ws.wait_until_identified(4):
            raise RuntimeError("Check OBS_PASSWORD against OBS WebSocket settings.")
        for req in ("GetRecordStatus", "GetStreamStatus", "GetVirtualCamStatus", "GetReplayBufferStatus"):
            reply = await ws.call(simpleobsws.Request(req), timeout=4)
            if req in ("GetVirtualCamStatus", "GetReplayBufferStatus") and reply.requestStatus.code == 604:
                continue
            if not reply.ok():
                raise RuntimeError(f"Cannot check {req}: {reply.requestStatus.comment}")
            if reply.responseData["outputActive"]:
                raise RuntimeError("Stop active OBS outputs before configuring the dedicated profile.")
        await select_group(ws, "Profile")
        await select_group(ws, "SceneCollection")
        await call(ws, "SetVideoSettings", baseWidth=1920, baseHeight=1080,
                   outputWidth=1920, outputHeight=1080, fpsNumerator=60, fpsDenominator=1)
        where = recording_dir() / "program"
        where.mkdir(parents=True, exist_ok=True)
        params = {"Output": {"Mode": "Simple"}, "SimpleOutput": {
            "FilePath": str(where), "RecFormat2": record_format, "RecQuality": "Small",
            "RecEncoder": "x264", "StreamEncoder": "x264", "VBitrate": "6000", "ABitrate": "160"}}
        for group, values in params.items():
            for name, value in values.items():
                await call(ws, "SetProfileParameter", parameterCategory=group,
                           parameterName=name, parameterValue=value)
        # OBS constructs output handlers when a profile is loaded. A parameter
        # write alone can leave the previous muxer active for the first recording.
        profiles = (await call(ws, "GetProfileList"))["profiles"]
        other = next((p for p in profiles if p != OBS_NAME), None)
        if other:
            await call(ws, "SetCurrentProfile", profileName=other)
            for _ in range(50):
                if (await call(ws, "GetProfileList"))["currentProfileName"] == other:
                    break
                await asyncio.sleep(.1)
            await select_group(ws, "Profile")
        scenes = {s["sceneName"] for s in (await call(ws, "GetSceneList"))["scenes"]}
        inputs = {s["inputName"] for s in (await call(ws, "GetInputList"))["inputs"]}
        cams = cameras()
        for key, cfg in cams.items():
            await install_camera(ws, key, cfg, scenes, inputs)
        transitions = (await call(ws, "GetSceneTransitionList"))["transitions"]
        cut = next((t["transitionName"] for t in transitions if t["transitionKind"] == "cut_transition"), None)
        if cut:
            await call(ws, "SetCurrentSceneTransition", transitionName=cut)
        await call(ws, "SetCurrentProgramScene", sceneName=scene(next(iter(cams))))
    finally:
        await ws.disconnect()
