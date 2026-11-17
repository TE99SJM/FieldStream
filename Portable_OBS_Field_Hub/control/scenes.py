"""Provision the Field Hub layouts and apply measured camera offsets in OBS."""
import argparse
import math
import os
import re
import sys
from fractions import Fraction
from pathlib import Path
from statistics import median

HERE = Path(__file__).resolve().parent
CAMERA_NAMES = {
    "sony": "Sony", "gopro1": "GoPro 1", "dji": "DJI",
    "gopro2": "GoPro 2", "gopro3": "GoPro 3",
}
GRID = "Field - Grid"
PIP = "Field - Sony + PiP"
FULL_SCENES = {key: f"Field - {name} Full" for key, name in CAMERA_NAMES.items()}
SCENES = [GRID, PIP, *FULL_SCENES.values()]
GRID_RECTS = {
    "sony": (24, 44, 930, 523), "gopro1": (966, 44, 930, 523),
    "dji": (24, 646, 616, 347), "gopro2": (652, 646, 616, 347),
    "gopro3": (1280, 646, 616, 347),
}
FULL = (0, 0, 1920, 1080)
SOURCE = {key: f"Field / {name}" for key, name in CAMERA_NAMES.items()}
DELAY_NAME = re.compile(r"^Field sync (\d+)$")


def connect():
    import obsws_python
    password = os.getenv("FIELDHUB_OBS_PASSWORD") or os.getenv("OBS_PASSWORD")
    if not password:
        raise ValueError("Set FIELDHUB_OBS_PASSWORD to the password from OBS Tools > WebSocket Server Settings.")
    return obsws_python.ReqClient(host="127.0.0.1", port=int(os.getenv("OBS_PORT", "4455")),
                                  password=password, timeout=6)


def request(client, method, **fields):
    return client.send(method, fields, raw=True) or {}


def require_field_collection(client):
    name = request(client, "GetSceneCollectionList")["currentSceneCollectionName"]
    if name != "Field Hub":
        raise ValueError("Select the Field Hub scene collection in OBS.")


def inactive(client):
    require_field_collection(client)
    # A disabled optional output may reject an individual status request.
    # Enumerating instantiated outputs covers record, stream, replay and virtual camera.
    statuses = request(client, "GetOutputList")["outputs"]
    active = [output["outputName"] for output in statuses if output["outputActive"]]
    if active:
        raise ValueError("Stop the active OBS outputs before editing the rig: " + ", ".join(active))


def input_spec(kind, settings):
    return {"inputKind": kind, "inputSettings": settings}


def specs(existing):
    wanted = os.getenv("FIELDHUB_GOPRO1", "").lower()
    if wanted not in ("", "usb", "wifi"):
        raise ValueError("FIELDHUB_GOPRO1 must be usb or wifi.")
    remembered = existing.get(SOURCE["gopro1"]) == "ffmpeg_source"
    wireless_gopro1 = (wanted == "wifi") if wanted else remembered
    specs_by_name = {}
    for key, name in SOURCE.items():
        if key == "sony" or (key == "gopro1" and not wireless_gopro1):
            specs_by_name[name] = input_spec("dshow_input", {"active": True, "deactivate_when_not_showing": False})
        else:
            specs_by_name[name] = input_spec("ffmpeg_source", {
                "is_local_file": False, "input": "rtsp://127.0.0.1:8554/live/" + key,
                "ffmpeg_options": "rtsp_transport=tcp", "restart_on_activate": False,
                "close_when_inactive": False, "clear_on_media_end": True, "reconnect_delay_sec": 2,
            })
        specs_by_name[slate_name(key)] = image_spec(key)
    specs_by_name["Field / Grid plate"] = image_spec("grid")
    for name, spec in specs_by_name.items():
        if name in existing and existing[name] != spec["inputKind"]:
            raise ValueError(f"{name} already uses {existing[name]}. Use a fresh OBS collection for a different transport.")
    return specs_by_name


def image_spec(asset):
    path = HERE / "slates" / f"{asset}.png"
    if not path.is_file():
        raise ValueError(f"Missing bundled image {path}. Extract the whole kit.")
    return input_spec("image_source", {"file": str(path), "unload": False})


def slate_name(key):
    return f"Field / {CAMERA_NAMES[key]} slate"


def tile(key, rect):
    return [(slate_name(key), rect), (SOURCE[key], rect)]


def layout_items():
    layouts = {GRID: [("Field / Grid plate", FULL)],
               PIP: tile("sony", FULL) + tile("dji", (1392, 768, 480, 270))}
    for key, rect in GRID_RECTS.items():
        layouts[GRID].extend(tile(key, rect))
        layouts[FULL_SCENES[key]] = tile(key, FULL)
    return layouts


def transform(rect, width, height):
    x, y, w, h = rect
    return {"positionX": x * width / 1920, "positionY": y * height / 1080,
            "alignment": 5, "boundsType": "OBS_BOUNDS_SCALE_INNER", "boundsAlignment": 0,
            "boundsWidth": w * width / 1920, "boundsHeight": h * height / 1080,
            "scaleX": 1.0, "scaleY": 1.0, "rotation": 0.0,
            "cropLeft": 0, "cropRight": 0, "cropTop": 0, "cropBottom": 0}


def build(client):
    inactive(client)
    video = request(client, "GetVideoSettings")
    width, height = video["baseWidth"], video["baseHeight"]
    if abs(width / height - 16 / 9) > .01:
        raise ValueError("Choose a 16:9 base canvas in OBS Settings > Video, then run build again.")
    scene_list = request(client, "GetSceneList")
    scene_names = {s["sceneName"] for s in scene_list["scenes"]}
    existing = {i["inputName"]: i["inputKind"] for i in request(client, "GetInputList")["inputs"]}
    source_specs = specs(existing)
    kinds = request(client, "GetInputKindList", unversioned=False)["inputKinds"]
    missing = {s["inputKind"] for s in source_specs.values()} - set(kinds)
    if missing:
        raise ValueError("Windows OBS is missing required inputs: " + ", ".join(sorted(missing)))
    for scene in SCENES:
        if scene not in scene_names:
            request(client, "CreateScene", sceneName=scene)

    checked = set()
    for scene, layers in layout_items().items():
        by_name = {}
        for item in request(client, "GetSceneItemList", sceneName=scene)["sceneItems"]:
            name = item["sourceName"]
            if name in by_name and name in source_specs:
                raise ValueError(f"Duplicate managed item {name} in {scene}. Remove the duplicate in OBS.")
            by_name[name] = item
        for index, (name, rect) in enumerate(layers):
            spec = source_specs[name]
            current = by_name.get(name)
            if name not in existing:
                created = request(client, "CreateInput", sceneName=scene, inputName=name,
                                  sceneItemEnabled=True, **spec)
                item_id = created["sceneItemId"]
                existing[name] = spec["inputKind"]
                if name in SOURCE.values():
                    request(client, "SetInputMute", inputName=name, inputMuted=True)
            else:
                if name not in checked:
                    actual = request(client, "GetInputSettings", inputName=name)["inputSettings"]
                    updates = {k: v for k, v in spec["inputSettings"].items() if actual.get(k) != v}
                    if updates:
                        request(client, "SetInputSettings", inputName=name, inputSettings=updates, overlay=True)
                if current:
                    item_id = current["sceneItemId"]
                else:
                    item_id = request(client, "CreateSceneItem", sceneName=scene, sourceName=name,
                                      sceneItemEnabled=True)["sceneItemId"]
            checked.add(name)
            fields = {"sceneName": scene, "sceneItemId": item_id}
            request(client, "SetSceneItemTransform", **fields, sceneItemTransform=transform(rect, width, height))
            request(client, "SetSceneItemIndex", **fields, sceneItemIndex=index)
            if name not in SOURCE.values() and current and not current["sceneItemEnabled"]:
                request(client, "SetSceneItemEnabled", **fields, sceneItemEnabled=True)
    if GRID not in scene_names or scene_list["currentProgramSceneName"] not in SCENES:
        request(client, "SetCurrentProgramScene", sceneName=GRID)
    print(f"Ready: {len(SCENES)} layouts on the {width} x {height} base canvas. "
          "Select the device in each USB source's Properties. Route one master mic in OBS.")


def devices(client):
    available = {i["inputName"]: i["inputKind"] for i in request(client, "GetInputList")["inputs"]}
    for key in ("sony", "gopro1"):
        name = SOURCE[key]
        if available.get(name) != "dshow_input":
            print(f"{name}: no USB source in this collection")
            continue
        current = request(client, "GetInputSettings", inputName=name)["inputSettings"].get("video_device_id", "")
        choices = request(client, "GetInputPropertiesListPropertyItems", inputName=name,
                          propertyName="video_device_id")["propertyItems"]
        print(name)
        for option in choices:
            if option.get("itemEnabled", True):
                selected = " [selected]" if option.get("itemValue") == current else ""
                print(f"  {option['itemName']}{selected}")
    print("Choose devices in the source Properties in OBS. Verify the two USB previews differ.")


def parse_frames(value):
    sample = {}
    try:
        for entry in value.split(","):
            key, raw = entry.strip().split("=")
            if key not in CAMERA_NAMES or key in sample:
                raise ValueError
            sample[key] = int(raw)
            if sample[key] < 0:
                raise ValueError
    except ValueError:
        raise argparse.ArgumentTypeError("Use distinct camera IDs and nonnegative first-flash frame numbers.") from None
    if len(sample) < 2:
        raise argparse.ArgumentTypeError("Supply at least two camera frame numbers for the same flash.")
    return sample


def frame_lags(samples):
    reference = next(iter(samples[0]))
    if any(set(sample) != set(samples[0]) for sample in samples):
        raise ValueError("Use the same cameras for each measured flash.")
    observations = {k: [s[k] - s[reference] for s in samples] for k in samples[0]}
    centers = {k: round(median(vals)) for k, vals in observations.items()}
    latest = max(centers.values())
    return {k: latest - value for k, value in centers.items()}, observations


def segments(frames, fps):
    # OBS GPU Delay allocates floor(ms / frame period) textures. It draws the first
    # texture AFTER inserting the current frame, giving N-1 elapsed frame intervals.
    period_ns = (1_000_000_000 * fps.denominator) // fps.numerator
    capacity = (500_000_000 // period_ns) - 1
    if capacity < 1:
        raise ValueError("The OBS frame rate is too low for stacked Render Delay.")
    values = []
    while frames:
        count = min(frames, capacity)
        values.append(math.ceil((count + 1) * period_ns / 1_000_000))
        frames -= count
    return values


def apply_filters(client, source, delays):
    existing = {f["filterName"]: f for f in
                request(client, "GetSourceFilterList", sourceName=source)["filters"]
                if DELAY_NAME.fullmatch(f["filterName"])}
    if any(f["filterKind"] != "gpu_delay" for f in existing.values()):
        raise ValueError(f"A user filter in {source} already uses a Field sync name.")
    for idx, value in enumerate(delays, start=1):
        name = f"Field sync {idx:02d}"
        fields = {"sourceName": source, "filterName": name}
        old = existing.pop(name, None)
        if old is None:
            request(client, "CreateSourceFilter", **fields, filterKind="gpu_delay",
                    filterSettings={"delay_ms": value})
        else:
            if old["filterSettings"].get("delay_ms") != value:
                request(client, "SetSourceFilterSettings", **fields, filterSettings={"delay_ms": value}, overlay=True)
            if not old["filterEnabled"]:
                request(client, "SetSourceFilterEnabled", **fields, filterEnabled=True)
    for name in existing:
        request(client, "RemoveSourceFilter", sourceName=source, filterName=name)


def calibrate(client, samples, dry_run=False, measured_fps=None):
    if not dry_run:
        inactive(client)
    video = request(client, "GetVideoSettings")
    fps = Fraction(video["fpsNumerator"], video["fpsDenominator"])
    if measured_fps is not None and fps != measured_fps:
        raise ValueError(f"The recording rate {measured_fps} differs from OBS ({fps}). "
                         "Record the grid again with matched project and recording rates.")
    lags, observations = frame_lags(samples)
    names = {i["inputName"] for i in request(client, "GetInputList")["inputs"]}
    if not set(SOURCE[k] for k in lags) <= names:
        raise ValueError("Build the Field layouts before calibrating.")
    if max(lags.values()) > 15 * fps:
        raise ValueError("Flash spread exceeds 15 seconds. Check that each frame number refers to the same flash.")
    for key, lag in lags.items():
        delays = segments(lag, fps)
        spread = max(observations[key]) - min(observations[key])
        values = " + ".join(str(d) for d in delays) or "none"
        print(f"{key}: target +{lag} frames ({float(1000 * lag / fps):.1f} ms); "
              f"Render Delay settings in ms: {values}; sampled relative range {spread} frame(s).")
        if not dry_run:
            apply_filters(client, SOURCE[key], delays)
    if not dry_run:
        print("Record the grid again and verify the residual on independent flashes.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("build")
    commands.add_parser("devices")
    commands.add_parser("clear-sync")
    sync = commands.add_parser("calibrate")
    sync.add_argument("--frames", required=True, type=parse_frames, action="append")
    sync.add_argument("--dry-run", action="store_true")
    sync.add_argument("--measured-fps", type=Fraction,
                      help="Check the recording frame rate against OBS, e.g. 30 or 30000/1001")
    args = parser.parse_args()
    client = connect()
    try:
        if args.command in (None, "build"):
            build(client)
        elif args.command == "devices":
            devices(client)
        elif args.command == "clear-sync":
            inactive(client)
            existing = {i["inputName"] for i in request(client, "GetInputList")["inputs"]}
            for name in SOURCE.values():
                if name in existing:
                    apply_filters(client, name, [])
            print("Cleared Field sync filters.")
        else:
            calibrate(client, args.frames, args.dry_run, args.measured_fps)
    finally:
        client.disconnect()


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        sys.exit(str(error) or "Interrupted")
