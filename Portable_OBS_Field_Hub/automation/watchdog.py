"""Keep Field scene tiles in step with decoded video arriving at MediaMTX."""

import argparse
import asyncio
from collections import deque
from dataclasses import dataclass, field
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from urllib.request import build_opener, ProxyHandler

COLLECTION = 'Field Hub'
SOURCES = {
    'Field / DJI': 'live/dji',
    'Field / GoPro 1': 'live/gopro1',
    'Field / GoPro 2': 'live/gopro2',
    'Field / GoPro 3': 'live/gopro3',
}
RTSP_BASE = 'rtsp://127.0.0.1:8554/'
MIN_FPS = 5.0
WARMUP = 2.0
DROP_GRACE = 4.0
START_GRACE = 8.0
log = logging.getLogger('fieldhub.watchdog')


@dataclass
class FrameHealth:
    started: float = field(default_factory=time.monotonic)
    samples: deque = field(default_factory=deque)
    last_frame: int = 0
    last_good: float | None = None
    visible: bool | None = None
    fps: float = 0.0

    def begin_decode(self):
        self.samples.clear()
        self.last_frame = 0
        self.fps = 0.0

    def observe(self, frames, now):
        if frames <= self.last_frame:
            return
        self.last_frame = frames
        if self.samples and now - self.samples[-1][0] > 1.5:
            self.samples.clear()
        self.samples.append((now, frames))
        # Keep about two seconds of advancing-frame samples. A new connection
        # must supply fresh samples; its counter always starts from zero.
        while len(self.samples) > 2 and now - self.samples[1][0] >= WARMUP:
            self.samples.popleft()
        start, first = self.samples[0]
        span = now - start
        if span >= WARMUP:
            self.fps = (frames - first) / span
            if self.fps >= MIN_FPS:
                self.last_good = now
                self.visible = True

    def decision(self, now):
        if self.last_good is None:
            if now - self.started >= START_GRACE:
                self.visible = False
        elif now - self.last_good >= DROP_GRACE:
            self.visible = False
        return self.visible


def receiver_paths():
    # The local control API should never be sent through Windows proxy settings.
    opener = build_opener(ProxyHandler({}))
    with opener.open('http://127.0.0.1:9997/v3/paths/list', timeout=2) as response:
        payload = json.load(response)
    # v1.21 exposes 'online'; older v1 builds exposed 'ready'. A path with an
    # unknown schema is probed directly rather than declared offline.
    return {row['name'] for row in payload['items'] if row.get('online', row.get('ready', True))}


async def update_paths(shared):
    while True:
        try:
            shared['ready'] = await asyncio.to_thread(receiver_paths)
        except (OSError, ValueError, KeyError, TypeError):
            shared['ready'] = None
        await asyncio.sleep(2)


def probe_command(ffmpeg, path):
    # No frame-rate conversion: output frames only advance as video decodes.
    # Never use -c copy or count socket bytes here; audio can outlive video.
    return [ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error', '-nostats',
            '-stats_period', '0.5', '-progress', 'pipe:1', '-rtsp_transport', 'tcp',
            '-timeout', '5000000', '-threads', '2', '-analyzeduration', '1000000',
            '-i', RTSP_BASE + path, '-map', '0:v:0', '-an', '-sn', '-dn',
            '-fps_mode', 'passthrough', '-f', 'null', '-']


async def decode(path, ffmpeg, health, shared):
    while True:
        ready = shared['ready']
        if ready is not None and path not in ready:
            await asyncio.sleep(1)
            continue
        health.begin_decode()
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        proc = await asyncio.create_subprocess_exec(
            *probe_command(ffmpeg, path), stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, creationflags=flags)
        advanced = time.monotonic()
        try:
            while True:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=12)
                now = time.monotonic()
                if not line or now - advanced > 12:
                    break
                key, sep, value = line.strip().partition(b'=')
                if key == b'frame' and sep and value.strip().isdigit():
                    count = int(value)
                    if count > health.last_frame:
                        health.observe(count, now)
                        advanced = now
        except asyncio.TimeoutError:
            pass
        finally:
            if proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
            await proc.wait()
        await asyncio.sleep(2)


def connect(timeout=3):
    import obsws_python as obs
    password = os.environ.get('FIELDHUB_OBS_PASSWORD') or os.environ.get('OBS_PASSWORD')
    if not password:
        raise ValueError('Set FIELDHUB_OBS_PASSWORD or use Start-FieldHub.cmd')
    return obs.ReqClient(host='127.0.0.1', port=4455, password=password, timeout=timeout)


def outputs_active(client):
    # GetOutputList includes the replay buffer, virtual camera, and plugin
    # outputs; explicit stream/record requests also cover frontend state.
    return (client.get_stream_status().output_active or client.get_record_status().output_active or
            any(row['outputActive'] for row in client.get_output_list().outputs))


def prepare_collection(client):
    selected = client.get_scene_collection_list()
    if outputs_active(client):
        return 'ACTIVE' if selected.current_scene_collection_name == COLLECTION else 'BUSY'
    if selected.current_scene_collection_name != COLLECTION:
        if COLLECTION in selected.scene_collections:
            client.set_current_scene_collection(COLLECTION)
        else:
            client.create_scene_collection(COLLECTION)
    deadline = time.monotonic() + 20
    while client.get_scene_collection_list().current_scene_collection_name != COLLECTION:
        if time.monotonic() > deadline:
            raise RuntimeError('OBS did not finish opening the Field Hub collection')
        time.sleep(0.5)
    return 'ACTIVE' if outputs_active(client) else 'IDLE'


class FieldScenes:
    def __init__(self, client):
        self.client = client
        self.items = {}
        self.next_refresh = 0
        self.last_restart = {}
        self.native_down_since = {}

    def refresh(self):
        # Only manage kit-owned scene items that point at the expected relay.
        # A wired GoPro or an independently rebound DJI is left to the operator.
        names = {s['inputName'] for s in self.client.get_input_list('ffmpeg_source').inputs}
        self.items = {}
        for name in SOURCES.keys() & names:
            settings = self.client.get_input_settings(name).input_settings
            if settings.get('input') == RTSP_BASE + SOURCES[name]:
                self.items[name] = []
        for scene in self.client.get_scene_list().scenes:
            title = scene['sceneName']
            if not title.startswith('Field - '):
                continue
            for row in self.client.get_scene_item_list(title).scene_items:
                name = row['sourceName']
                if name in self.items:
                    self.items[name].append([title, row['sceneItemId'], row['sceneItemEnabled']])

    def step(self, frame_states, now):
        if self.client.get_scene_collection_list().current_scene_collection_name != COLLECTION:
            self.items.clear()
            self.next_refresh = 0
            return set()
        if now >= self.next_refresh:
            self.refresh()
            self.next_refresh = now + 10
        for name, rows in self.items.items():
            want = frame_states.get(name)
            if want is None or not rows:
                continue
            if want:
                media_state = self.client.get_media_input_status(name).media_state
                if media_state in ('OBS_MEDIA_STATE_ERROR', 'OBS_MEDIA_STATE_ENDED', 'OBS_MEDIA_STATE_STOPPED'):
                    down_since = self.native_down_since.setdefault(name, now)
                    if now >= self.last_restart.get(name, -100) + 12:
                        self.client.trigger_media_input_action(name, 'OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART')
                        self.last_restart[name] = now
                        log.info('Restarted OBS decoder for %s (%s)', name, media_state)
                    if now - down_since >= DROP_GRACE or not any(enabled for _, _, enabled in rows):
                        want = False
                else:
                    # NONE can mean a new source has never been shown. Enable it
                    # once the independent decoder proves frames are available.
                    self.native_down_since.pop(name, None)
            else:
                self.native_down_since.pop(name, None)
            changed = False
            for row in rows:
                title, item_id, shown = row
                if shown != want:
                    self.client.set_scene_item_enabled(title, item_id, want)
                    row[2] = want
                    changed = True
            if changed:
                log.info('%s %s', 'Show' if want else 'Hide', name)
        return set(self.items)


async def monitor(ffmpeg, status_file):
    shared = {'ready': None}
    api_task = asyncio.create_task(update_paths(shared))
    probes = {}
    health = {}
    scenes = None
    last_status = 0.0
    try:
        while True:
            try:
                if scenes is None:
                    scenes = FieldScenes(await asyncio.to_thread(connect))
                    log.info('Connected to OBS')
                now = time.monotonic()
                desired = {name: h.decision(now) for name, h in health.items()}
                active = await asyncio.to_thread(scenes.step, desired, now)
                if now - last_status >= 3:
                    snapshot = {'pid': os.getpid(), 'updated': time.time(), 'inputs': {
                        name: {'estimated_decode_fps': round(h.fps, 1) if h.samples and now - h.samples[-1][0] < 2 else 0,
                               'frames_healthy': h.visible} for name, h in health.items()}}
                    temporary = status_file.with_suffix('.tmp')
                    try:
                        temporary.write_text(json.dumps(snapshot), encoding='utf-8')
                        temporary.replace(status_file)
                    except OSError as error:
                        log.warning('Status file unavailable: %s', type(error).__name__)
                    last_status = now
                for name in set(probes) - active:
                    probes[name].cancel()
                    await asyncio.gather(probes.pop(name), return_exceptions=True)
                    del health[name]
                for name in active:
                    if name not in probes or probes[name].done():
                        if name in probes and probes[name].exception():
                            log.warning('Decoder for %s failed: %s', name, type(probes[name].exception()).__name__)
                        health.setdefault(name, FrameHealth())
                        probes[name] = asyncio.create_task(decode(SOURCES[name], ffmpeg, health[name], shared))
            except Exception as error:
                # OBS may restart or the operator may replace the scene collection.
                log.warning('OBS unavailable or scene changed (%s), retrying', type(error).__name__)
                if scenes:
                    try:
                        await asyncio.to_thread(scenes.client.disconnect)
                    except Exception:
                        pass
                scenes = None
                await asyncio.sleep(2)
            await asyncio.sleep(1)
    finally:
        api_task.cancel()
        for task in probes.values():
            task.cancel()
        await asyncio.gather(api_task, *probes.values(), return_exceptions=True)
        if scenes:
            scenes.client.disconnect()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-obs', action='store_true', help='authenticate to OBS without changing its state')
    parser.add_argument('--prepare-collection', action='store_true', help='open the kit collection when OBS outputs are idle')
    args = parser.parse_args()
    if args.check_obs or args.prepare_collection:
        client = connect(timeout=25)
        try:
            client.get_version()
            print(prepare_collection(client) if args.prepare_collection else 'OK')
        finally:
            client.disconnect()
        return
    ffmpeg = os.environ.get('FIELDHUB_FFMPEG') or shutil.which('ffmpeg')
    if not ffmpeg:
        parser.error('FFmpeg missing. Install it or set FIELDHUB_FFMPEG.')
    logdir = Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'ForgeFieldHub'
    logdir.mkdir(parents=True, exist_ok=True)
    # Kernel releases this lock on exit, including after a crash or power loss.
    with (logdir / 'watchdog.lock').open('a+b') as lock:
        if os.name == 'nt':
            import msvcrt
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                return
        handler = RotatingFileHandler(logdir / 'watchdog.log', maxBytes=500_000, backupCount=2)
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        log.addHandler(handler)
        log.setLevel(logging.INFO)
        asyncio.run(monitor(ffmpeg, logdir / 'watchdog-status.json'))


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
