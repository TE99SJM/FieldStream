"""Build the native OBS scenes for the Field Stream camera kit."""
import argparse
import getpass
from ipaddress import IPv4Address
import os
from pathlib import Path
import platform
import re
import socket
import sys
from urllib.parse import parse_qs, urlsplit

FIELD = "Field Stream"
GRID = "Combined Multiview"
PIP = "Field PiP"
CAMERAS = {
    "Sony": None,
    "GoPro USB": None,
    "GoPro 2": "gopro2",
    "GoPro 3": "gopro3",
    "DJI": "dji",
}
PRIMARY_RTMP = "rtmp://127.0.0.1:1935/live/gopro1"
FRAME = (0, 0, 1920, 1080)
GRID_POSITIONS = {
    "Sony": (0, 90, 960, 540),
    "GoPro USB": (960, 90, 960, 540),
    "GoPro 2": (0, 630, 640, 360),
    "GoPro 3": (640, 630, 640, 360),
    "DJI": (1280, 630, 640, 360),
}
PIP_POSITIONS = {
    "Sony": FRAME,
    "GoPro USB": (24, 813, 432, 243),
    "GoPro 2": (504, 813, 432, 243),
    "GoPro 3": (984, 813, 432, 243),
    "DJI": (1464, 813, 432, 243),
}
VIDEO = dict(baseWidth=1920, baseHeight=1080, outputWidth=1920, outputHeight=1080,
             fpsNumerator=30, fpsDenominator=1)


def slate_path(camera):
    filename = "standby-" + camera.lower().replace(" ", "-") + ".png"
    return Path(__file__).resolve().parent / "assets" / filename


def media_settings(url, input_format=""):
    return {
        "input": url, "is_local_file": False, "input_format": input_format, "ffmpeg_options": "",
        "buffering_mb": 1, "reconnect_delay_sec": 2, "hw_decode": False,
        "restart_on_activate": False, "close_when_inactive": False,
        "clear_on_media_end": True, "looping": False,
    }


def usb_url(address):
    return (f"udp://{address}:8554?localaddr={address}&localport=8554"
            "&overrun_nonfatal=1&timeout=5000000")


def usb_address(value, *, verify_local):
    address = IPv4Address(value)
    if address.is_unspecified or address.is_loopback or address.is_multicast or address.is_reserved:
        raise ValueError("Use the unicast IPv4 address assigned to the PC's GoPro USB interface.")
    if verify_local:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.bind((str(address), 0))
        except OSError as exc:
            raise ValueError(f"{address} is not available on this host. Connect the GoPro "
                             "and use the PC-side USB address displayed by the helper.") from exc
    return str(address)


def saved_usb_address(settings):
    url = urlsplit(settings.get("input", ""))
    query = parse_qs(url.query)
    if (url.scheme == "udp" and url.hostname and url.port == 8554 and
            query.get("localaddr") == [url.hostname] and query.get("localport") == ["8554"]):
        return usb_address(url.hostname, verify_local=False)
    raise ValueError("Configure the Linux GoPro with --gopro-usb-bind HOST_USB_IP or --gopro-wireless.")


def transform(box):
    x, y, width, height = box
    return {
        "positionX": x, "positionY": y, "alignment": 5,  # top + left
        "boundsType": "OBS_BOUNDS_SCALE_INNER", "boundsAlignment": 0,
        "boundsWidth": width, "boundsHeight": height,
        "rotation": 0, "scaleX": 1, "scaleY": 1,
        "cropLeft": 0, "cropRight": 0, "cropTop": 0, "cropBottom": 0,
    }


def provision(client, gopro_usb_bind=None, gopro_wireless=False):
    def call(request, **fields):
        return client.send(request, fields, raw=True) or {}

    profile = call("GetProfileList")["currentProfileName"]
    collection = call("GetSceneCollectionList")["currentSceneCollectionName"]
    if profile != FIELD or collection != FIELD:
        raise ValueError(f"Select '{FIELD}' in both Profile and Scene Collection in OBS first. "
                         f"Current selections: {profile!r} / {collection!r}.")
    for request in ("GetStreamStatus", "GetRecordStatus"):
        if call(request)["outputActive"]:
            raise ValueError("Run setup while OBS is offline, after stopping recording and streaming.")

    kinds = set(call("GetInputKindList")["inputKinds"])
    os_name = platform.system()
    native_kinds = {"Windows": ("dshow_input",), "Darwin": ("macos-avcapture", "av_capture_input"),
                    "Linux": ("v4l2_input",)}
    native = next((kind for kind in native_kinds.get(os_name, ()) if kind in kinds), None)
    if native is None or not {"image_source", "ffmpeg_source"}.issubset(kinds):
        raise ValueError(f"{os_name} OBS must provide its native camera input, Image, and Media Source. "
                         f"Expected camera input: {native_kinds.get(os_name)}.")
    if gopro_usb_bind is not None and gopro_wireless:
        raise ValueError("Choose one Linux GoPro input transport.")
    if os_name != "Linux" and (gopro_usb_bind is not None or gopro_wireless):
        raise ValueError("The GoPro transport options apply only to a native Linux OBS host.")

    inputs = {item["inputName"]: item for item in call("GetInputList")["inputs"]}
    scenes = {item["sceneName"] for item in call("GetSceneList")["scenes"]}
    camera_scenes = [f"Cam - {camera}" for camera in CAMERAS]
    scene_names = camera_scenes + [GRID, PIP]
    feeds = {}
    assets = {}
    corrections = {}
    for camera, key in CAMERAS.items():
        asset = slate_path(camera)
        if not asset.is_file():
            raise ValueError(f"Missing {asset.name}. Extract the complete kit with its assets folder.")
        assets[camera] = asset
        slate = f"Slate - {camera}"
        feed = f"Feed - {camera}"
        for name in (slate, feed):
            if name in scenes:
                raise ValueError(f"An existing scene uses the input name {name!r}.")
        if slate in inputs:
            current_kind = re.sub(r"_v\d+$", "", inputs[slate]["inputKind"])
            if current_kind == "image_source":
                current = call("GetInputSettings", inputName=slate)["inputSettings"]
                old_file = current.get("file")
                if not old_file or not Path(old_file).is_file():
                    corrections[slate] = {"file": str(asset)}
            elif os_name != "Windows" or current_kind != "text_gdiplus":
                raise ValueError(f"{slate!r} must be an Image source or an existing Windows Text (GDI+) slate.")
        if key:
            kind = "ffmpeg_source"
            settings = media_settings(f"rtmp://127.0.0.1:1935/live/{key}")
        elif camera == "GoPro USB" and os_name == "Linux":
            kind = "ffmpeg_source"
            settings = {}
        else:
            kind = native
            settings = {}
            if native == "dshow_input":
                settings = {"video_device_id": "", "deactivate_when_not_showing": False}
        if feed in inputs:
            existing = inputs[feed]["inputKind"]
            if os_name == "Darwin" and not key and existing in native_kinds["Darwin"] and existing in kinds:
                kind = existing
            elif existing != kind:
                raise ValueError(f"{feed!r} uses {existing!r}. Expected {kind!r}.")
        if camera == "GoPro USB" and os_name == "Linux":
            current = call("GetInputSettings", inputName=feed)["inputSettings"] if feed in inputs else {}
            wireless = gopro_wireless or (gopro_usb_bind is None and current.get("input") == PRIMARY_RTMP)
            if wireless:
                settings = media_settings(PRIMARY_RTMP)
            elif gopro_usb_bind is not None:
                settings = media_settings(usb_url(usb_address(gopro_usb_bind, verify_local=True)), "mpegts")
            else:
                settings = media_settings(usb_url(saved_usb_address(current)), "mpegts")
            if feed in inputs and (gopro_wireless or gopro_usb_bind is not None):
                # The source remains an ffmpeg_source in either mode, keeping its filters and scene references.
                changes = {k: settings[k] for k in ("input", "input_format", "is_local_file",
                                                    "restart_on_activate", "close_when_inactive",
                                                    "clear_on_media_end") if current.get(k) != settings[k]}
                if changes:
                    corrections[feed] = changes
        feeds[camera] = (kind, settings)
    for name in scene_names:
        if name in inputs:
            raise ValueError(f"An existing input uses the scene name {name!r}.")

    for name, changes in corrections.items():
        call("SetInputSettings", inputName=name, inputSettings=changes, overlay=True)
    current_video = call("GetVideoSettings")
    if any(current_video[key] != value for key, value in VIDEO.items()):
        call("SetVideoSettings", **VIDEO)

    new_grid = GRID not in scenes
    for name in scene_names:
        if name not in scenes:
            call("CreateScene", sceneName=name)

    def place(scene, source, box, kind=None, settings=None):
        items = call("GetSceneItemList", sceneName=scene)["sceneItems"]
        matches = [item for item in items if item["sourceName"] == source]
        if len(matches) > 1:
            raise ValueError(f"Multiple copies of {source!r} in {scene!r}. Keep one, then rerun.")
        if matches:
            item_id = matches[0]["sceneItemId"]
        elif kind is not None and source not in inputs:
            result = call("CreateInput", sceneName=scene, inputName=source, inputKind=kind,
                          inputSettings=settings, sceneItemEnabled=True)
            item_id = result["sceneItemId"]
            inputs[source] = {"inputKind": kind}
        else:
            item_id = call("CreateSceneItem", sceneName=scene, sourceName=source,
                           sceneItemEnabled=True)["sceneItemId"]
        item = {"sceneName": scene, "sceneItemId": item_id}
        call("SetSceneItemTransform", **item, sceneItemTransform=transform(box))
        return item

    for camera, (kind, settings) in feeds.items():
        scene = f"Cam - {camera}"
        slate = place(scene, f"Slate - {camera}", FRAME, "image_source",
                      {"file": str(assets[camera]), "unload": False})
        call("SetSceneItemEnabled", **slate, sceneItemEnabled=True)
        call("SetSceneItemIndex", **slate, sceneItemIndex=0)
        feed = f"Feed - {camera}"
        place(scene, feed, FRAME, kind, settings)
        # V4L2 provides video only. Its separate USB audio device belongs in the master mixer.
        if kind != "v4l2_input":
            call("SetInputMute", inputName=feed, inputMuted=True)

    for scene, layout in ((GRID, GRID_POSITIONS), (PIP, PIP_POSITIONS)):
        for camera, box in layout.items():
            item = place(scene, f"Cam - {camera}", box)
            call("SetSceneItemEnabled", **item, sceneItemEnabled=True)
            if scene == PIP and camera == "Sony":
                call("SetSceneItemIndex", **item, sceneItemIndex=0)
    if new_grid:
        call("SetCurrentProgramScene", sceneName=GRID)
    return feeds["GoPro USB"][1].get("input")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
        epilog="Open the dedicated 'Field Stream' profile and collection first. "
               "Stop the monitor before provisioning. Rerunning reapplies geometry and mutes camera audio. "
               "Hardware bindings and filters are preserved. Use --gopro-usb-bind to rebind the Linux UDP input.")
    parser.add_argument("--port", type=int, default=os.environ.get("OBS_WS_PORT", 4455),
                        help="local OBS WebSocket v5 port (default: 4455 or OBS_WS_PORT)")
    transport = parser.add_mutually_exclusive_group()
    transport.add_argument("--gopro-usb-bind", metavar="HOST_USB_IP",
                           help="Linux wired mode: PC-side IPv4 on the GoPro USB interface")
    transport.add_argument("--gopro-wireless", action="store_true",
                           help="Linux wireless mode: use local RTMP live/gopro1 for the first GoPro")
    args = parser.parse_args()
    import obsws_python as obs
    password = os.environ.get("OBS_WS_PASSWORD") or getpass.getpass("OBS WebSocket password: ")
    client = obs.ReqClient(host="127.0.0.1", port=args.port, password=password, timeout=10)
    try:
        primary_url = provision(client, args.gopro_usb_bind, args.gopro_wireless)
    finally:
        client.disconnect()
    print("Five camera scenes, Combined Multiview and Field PiP are ready.")
    if platform.system() == "Linux":
        print("Select the Sony V4L2 device in Feed - Sony.")
        if primary_url == PRIMARY_RTMP:
            print("First GoPro: use Quik to publish to live/gopro1; its OBS source keeps the name Feed - GoPro USB.")
        else:
            print("Start the wired GoPro USB webcam sender.")
    else:
        print("Select the Sony and the GoPro Webcam devices in their respective Feed properties.")
    print("Select one global Mic/Aux in OBS for program audio.")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        sys.exit("Setup cancelled.")
    except Exception as exc:
        sys.exit(f"Could not complete OBS setup: {exc}")
