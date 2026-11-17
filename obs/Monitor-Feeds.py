#!/usr/bin/env python3
"""Keep the wireless items in the Field Stream collection on live video or a slate."""
import argparse
import getpass
import json
import logging
import os
import shutil
import subprocess
import threading
import time
from contextlib import suppress
from urllib.request import ProxyHandler, build_opener

DEFAULT_CAMERAS = ("gopro2", "gopro3", "dji")
CAMERAS = {"gopro1": "GoPro USB", "gopro2": "GoPro 2", "gopro3": "GoPro 3", "dji": "DJI"}
WORKSPACE = "Field Stream"
API_URL = "http://127.0.0.1:9997/v3/paths/list?itemsPerPage=100"
LOOPBACK_HTTP = build_opener(ProxyHandler({}))
FRAME_BYTES = 96 * 54
POLL = 1.0


def log(message):
    print(f"{time.strftime('%H:%M:%S')}  {message}", flush=True)


def online_paths():
    """A missing API is unknown. The independent video probe still works."""
    try:
        with LOOPBACK_HTTP.open(API_URL, timeout=0.8) as response:
            rows = json.load(response)["items"]
        return {row["name"] for row in rows if row["online"]}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def probe_command(ffmpeg, url, stale):
    # Intra frames only. The select filter emits only when source timestamps
    # advance, and passthrough prevents output frame-rate synthesis on a stall.
    return [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
            "-rw_timeout", str(int(1_000_000 * stale)), "-analyzeduration", "1000000",
            "-probesize", "1000000", "-threads", "1", "-skip_frame", "nokey",
            "-i", url, "-map", "0:v:0", "-an", "-sn", "-dn",
            "-vf", "select='isnan(prev_selected_t)+gte(t-prev_selected_t,0.5)',"
                   "scale=96:54:flags=area,format=gray",
            "-fps_mode", "passthrough", "-threads:v", "1", "-c:v", "rawvideo",
            "-flush_packets", "1", "-f", "rawvideo", "pipe:1"]


class VideoProbe:
    def __init__(self, ffmpeg, key, stale, pixel_freeze):
        self.url = f"rtmp://127.0.0.1:1935/live/{key}"
        self.command = probe_command(ffmpeg, self.url, stale)
        self.stale, self.pixel_freeze = stale, pixel_freeze
        self.process = None
        self.lock = threading.Lock()
        self.next_start = 0.0
        self.started = 0.0
        self.frames = 0
        self.changes = 0
        self.last_frame = self.changed = 0.0
        self.previous = None

    def start(self, now):
        proc = subprocess.Popen(
            self.command, stdout=subprocess.PIPE, stdin=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
        with self.lock:
            self.process = proc
            self.started = now
            self.frames = 0
            self.last_frame = self.changed = 0.0
            self.previous = None
        threading.Thread(target=self.read_frames, args=(proc,), daemon=True).start()

    def read_frames(self, proc):
        with proc.stdout as pipe:
            while len(frame := pipe.read(FRAME_BYTES)) == FRAME_BYTES:
                now = time.monotonic()
                with self.lock:
                    if self.process is not proc:
                        break
                    self.frames += 1
                    self.last_frame = now
                    if frame != self.previous:
                        self.changes += 1
                        self.changed = now
                    self.previous = frame

    def stop(self):
        with self.lock:
            proc, self.process = self.process, None
        if proc is not None:
            if proc.poll() is None:
                with suppress(ProcessLookupError):
                    proc.kill()
            proc.wait()

    def check(self, now, publisher):
        if publisher is False:
            self.stop()
            self.next_start = 0.0
            return False, "publisher offline"
        if self.process is not None and self.process.poll() is not None:
            self.stop()
            self.next_start = now + 2
        if self.process is None and now >= self.next_start:
            self.start(now)
        with self.lock:
            frames, last, changed = self.frames, self.last_frame, self.changed
        if self.process is None:
            return False, "retrying decoder"
        if now - (last or self.started) > self.stale:
            self.stop()
            self.next_start = now + 1
            return False, "video timestamps stalled"
        if frames < 2:
            return False, "waiting for two video keyframes"
        if self.pixel_freeze and now - changed > self.pixel_freeze:
            # Keep this decoder open: a later changed frame must clear the fault.
            return False, "repeated image"
        return True, "decoded video"


class RenderCheck:
    """Detect a frozen OBS picture if the camera independently shows movement."""
    def __init__(self):
        self.shot = None
        self.sample_at = 0.0
        self.last_change = 0.0
        self.change_count = 0
        self.stuck = False
        self.recovery_changes = 0
        self.unavailable = False

    def ready(self, obs, name, now, changes):
        if now < self.sample_at:
            return not (self.stuck or self.unavailable)
        self.sample_at = now + 3
        active = obs.get_source_active(name)
        if not (active.video_active or active.video_showing):
            self.shot, self.stuck, self.unavailable = None, False, False
            self.recovery_changes = 0
            return True
        from obsws_python.error import OBSSDKRequestError
        try:
            shot = obs.get_source_screenshot(name, "png", 96, 54, -1).image_data
        except OBSSDKRequestError:
            # An active source can report Playing while lacking a drawable image.
            self.shot, self.unavailable = None, True
            return False
        self.unavailable = False
        if shot != self.shot:
            self.shot = shot
            self.last_change, self.change_count = now, changes
            if self.stuck:
                self.recovery_changes += 1
                if self.recovery_changes >= 2:
                    self.stuck = False
                    self.recovery_changes = 0
        elif now - self.last_change >= 15 and changes - self.change_count >= 3:
            self.stuck = True
            self.recovery_changes = 0
        return not self.stuck


class Switch:
    def __init__(self, title):
        self.title = title
        self.scene = "Cam - " + title
        self.feed = "Feed - " + title
        self.slate = "Slate - " + title
        self.candidate = None
        self.since = 0.0
        self.notice = None
        self.next_restart = 0.0
        self.render = RenderCheck()

    def report(self, text):
        if text != self.notice:
            log(f"{self.title}: {text}")
            self.notice = text

    def update(self, obs, signal_good, reason, now, changes):
        items = obs.get_scene_item_list(self.scene).scene_items
        feeds = [x for x in items if x["sourceName"] == self.feed]
        slates = [x for x in items if x["sourceName"] == self.slate]
        if len(feeds) != 1 or len(slates) != 1:
            self.report("check unique Feed and Slate items in the camera scene")
            self.candidate = None
            return
        feed, slate = feeds[0], slates[0]
        if self.title == CAMERAS["gopro1"]:
            # The primary slot may alternatively contain a direct USB decoder.
            settings = obs.get_input_settings(self.feed)
            if (settings.input_kind != "ffmpeg_source" or
                    settings.input_settings.get("input") != "rtmp://127.0.0.1:1935/live/gopro1"):
                self.report("primary slot uses USB; omit gopro1 from wireless monitoring")
                self.candidate = None
                return
        if (feed.get("inputKind") != "ffmpeg_source" or not slate["sceneItemEnabled"]
                or not feed["sceneItemEnabled"]):
            self.report("enable both the Media Source and its opaque slate; monitoring paused")
            self.candidate = None
            return
        good = signal_good
        if signal_good:
            state = obs.get_media_input_status(self.feed).media_state
            if state != "OBS_MEDIA_STATE_PLAYING":
                good, reason = False, state
            elif not self.render.ready(obs, self.feed, now, changes):
                good, reason = False, "OBS picture unavailable or stalled"
        else:
            self.render = RenderCheck()
        if good != self.candidate:
            self.candidate, self.since = good, now
        if now - self.since < (2.0 if good else 3.0):
            return
        # Change only z-order in this two-layer camera view. The decoder remains
        # active behind the opaque slate and every parent layout sees the change.
        visible = feed["sceneItemIndex"] > slate["sceneItemIndex"]
        if visible != good:
            if not right_workspace(obs):
                return
            obs.set_scene_item_index(self.scene, slate["sceneItemId"], feed["sceneItemIndex"])
        self.report("live" if good else f"slate ({reason})")
        if signal_good and not good and now >= self.next_restart and now - self.since >= 6:
            active = obs.get_source_active(self.feed)
            if (active.video_showing or active.video_active) and right_workspace(obs):
                obs.trigger_media_input_action(self.feed, "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART")
                self.next_restart = now + 25
                log(f"{self.title}: restarted OBS Media Source behind its slate.")


def right_workspace(client):
    return (client.get_scene_collection_list().current_scene_collection_name == WORKSPACE
            and client.get_profile_list().current_profile_name == WORKSPACE)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cameras", nargs="+", choices=CAMERAS, default=list(DEFAULT_CAMERAS))
    p.add_argument("--ffmpeg", default="ffmpeg")
    p.add_argument("--stale", type=int, default=12, metavar="SECONDS",
                   help="restart probe after this gap in video keyframes (default 12)")
    p.add_argument("--pixel-freeze", type=int, default=0, metavar="SECONDS",
                   help="also slate repeated 96x54 images; 0 disables this optional heuristic")
    p.add_argument("--port", default=int(os.getenv("OBS_WS_PORT", "4455")), type=int)
    args = p.parse_args()
    if args.stale < 4 or args.pixel_freeze < 0 or not 1 <= args.port <= 65535:
        p.error("--stale must be >=4; --pixel-freeze >=0; --port 1..65535")
    ffmpeg = shutil.which(args.ffmpeg)
    if not ffmpeg:
        p.error("FFmpeg is missing. Install FFmpeg or supply --ffmpeg.")
    try:
        import obsws_python
        from obsws_python.error import OBSSDKRequestError
    except ImportError:
        p.error("Install obsws-python in this Python environment.")
    logging.getLogger("obsws_python").setLevel(logging.CRITICAL)
    password = os.environ.get("OBS_WS_PASSWORD") or getpass.getpass("OBS WebSocket password: ")
    if not password:
        p.error("Enter the password shown by OBS under Tools > WebSocket Server Settings.")
    probes = {key: VideoProbe(ffmpeg, key, args.stale, args.pixel_freeze)
              for key in dict.fromkeys(args.cameras)}
    switches = {key: Switch(CAMERAS[key]) for key in probes}
    client, notice, api_available = None, None, None
    try:
        while True:
            try:
                if client is None:
                    client = obsws_python.ReqClient(host="127.0.0.1", port=args.port,
                                                    password=password, timeout=3)
                if not right_workspace(client):
                    if notice != "workspace":
                        log("Paused. Select Field Stream under both Profile and Scene Collection.")
                        notice = "workspace"
                    for probe in probes.values():
                        probe.stop()
                    for switch in switches.values():
                        switch.candidate = None
                    time.sleep(3)
                    continue
                if notice != "online":
                    log("Watching " + ", ".join(CAMERAS[key] for key in probes) + ". Ctrl+C stops the monitor.")
                    notice = "online"
                paths = online_paths()
                if (paths is not None) != api_available:
                    api_available = paths is not None
                    log("MediaMTX API online." if api_available else
                        "MediaMTX API unavailable; using decoded frames for publisher health.")
                for key, probe in probes.items():
                    now = time.monotonic()
                    good, reason = probe.check(now, None if paths is None else f"live/{key}" in paths)
                    try:
                        switches[key].update(client, good, reason, now, probe.changes)
                    except OBSSDKRequestError as exc:
                        switches[key].candidate = None
                        switches[key].report(f"OBS request failed ({exc}); verify the scene and source names")
                time.sleep(POLL)
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as exc:
                # Reconnect to OBS after a restart instead of replacing program scenes.
                if notice != "error":
                    log(f"Connection/monitor error ({type(exc).__name__}: {exc}). Retrying in 3 s.")
                    notice = "error"
                if client is not None:
                    with suppress(Exception):
                        client.disconnect()
                client = None
                time.sleep(3)
    except KeyboardInterrupt:
        log("Monitor stopped.")
    finally:
        for probe in probes.values():
            probe.stop()
        if client is not None:
            with suppress(Exception):
                client.disconnect()


if __name__ == "__main__":
    main()
