import asyncio
from collections import deque
import logging
import os
from pathlib import Path
import subprocess
import sys
import time

import av
import imageio_ffmpeg

from .process import spawn, stop


def ffmpeg():
    return os.environ.get("HUB_FFMPEG") or imageio_ffmpeg.get_ffmpeg_exe()


def devices():
    if os.name != "nt":
        raise RuntimeError("DirectShow device discovery runs on the Windows hub.")
    try:
        listed = av.enumerate_input_devices("dshow")
    except (ValueError, av.FFmpegError):
        listed = []
    if listed:
        for item in listed:
            if not item.media_types or "video" in item.media_types:
                print(f"{item.description}\n  ID: {item.name}")
    else:
        subprocess.run([ffmpeg(), "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"])


def modes(name, number):
    subprocess.run([ffmpeg(), "-hide_banner", "-f", "dshow", "-video_device_number", str(number),
                    "-list_options", "true", "-i", "video=" + name])


def capture_args(cam):
    input_args = ["-f", "dshow", "-rtbufsize", "32M", "-video_device_number", str(cam.device_number),
                  "-video_size", f"{cam.width}x{cam.height}", "-framerate", str(cam.fps)]
    if cam.format == "mjpeg":
        input_args += ["-vcodec", "mjpeg"]
    if cam.format == "yuy2":
        input_args += ["-pixel_format", "yuyv422"]
    output = ["-c:v", "copy"] if cam.format == "mjpeg" else ["-c:v", "mjpeg", "-threads", "2", "-q:v", "4"]
    return [ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error", *input_args,
            "-i", "video=" + cam.device, "-map", "0:v:0", "-an", "-fps_mode", "passthrough",
            *output, "-f", "mpjpeg", "-flush_packets", "1", "pipe:1"]


class Feed:
    def __init__(self, cam):
        self.cam = cam
        self.samples = deque()
        self.last = None
        self.count = 0
        self.frame = None
        self.changed = asyncio.Event()
        self.detail = "Waiting for input"

    def advance(self, count, jpeg=None):
        now = time.monotonic()
        if count <= self.count:
            return
        self.count = count
        self.last = now
        self.samples.append((now, count))
        while len(self.samples) > 2 and self.samples[1][0] < now - 4:
            self.samples.popleft()
        if jpeg is not None:
            self.frame = jpeg
            self.changed.set()
            self.changed = asyncio.Event()
        self.detail = ""

    def clear(self, detail):
        self.samples.clear()
        self.last = self.frame = None
        self.count = 0
        self.detail = detail
        self.changed.set()
        self.changed = asyncio.Event()

    def status(self):
        age = time.monotonic() - self.last if self.last is not None else None
        state = "offline" if age is None else ("stalled" if age > 3 else "receiving")
        fps = None
        if state == "receiving" and len(self.samples) > 1:
            (t0, n0), (t1, n1) = self.samples[0], self.samples[-1]
            if t1 - t0 >= 0.5:
                fps = round((n1 - n0) / (t1 - t0), 1)
        return dict(id=self.cam.id, label=self.cam.label, transport=self.cam.transport,
                    state=state, fps=fps, age=None if age is None else round(age, 1), detail=self.detail)

    async def multipart(self):
        while self.frame is not None:
            frame, changed = self.frame, self.changed
            yield (b"--hub\r\nContent-Type: image/jpeg\r\nContent-Length: " +
                   str(len(frame)).encode() + b"\r\n\r\n" + frame + b"\r\n")
            try:
                await asyncio.wait_for(changed.wait(), 5)
            except TimeoutError:
                return


async def read_jpegs(reader, feed):
    while True:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 12)
        fields = [v.partition(b":")[2] for v in head.lower().splitlines() if v.startswith(b"content-length:")]
        size = int(fields[0]) if len(fields) == 1 else 0
        if not 0 < size <= 32_000_000:
            raise ValueError("Invalid MJPEG frame length")
        jpeg = await asyncio.wait_for(reader.readexactly(size), 5)
        feed.advance(feed.count + 1, jpeg)


async def read_counts(reader, feed):
    deadline = time.monotonic() + 12
    while True:
        line = await asyncio.wait_for(reader.readline(), max(0.01, deadline - time.monotonic()))
        if not line:
            return
        if line.startswith(b"frame="):
            count = int(line.partition(b"=")[2])
            if count > feed.count:
                feed.advance(count)
                deadline = time.monotonic() + 8


def probe_args(url):
    return [sys.executable, str(Path(__file__).with_name("probe.py")), url]


async def worker(feed, args, parser):
    while True:
        process = None
        message = "Input closed"
        try:
            process = await spawn(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            errors = deque(maxlen=3)

            async def drain():
                async for line in process.stderr:
                    errors.append(line.decode(errors="replace").strip())

            drain_task = asyncio.create_task(drain())
            try:
                await parser(process.stdout, feed)
            finally:
                async def discard():
                    while await process.stdout.read(65536):
                        pass

                discard_task = asyncio.create_task(discard())
                await stop(process)
                await asyncio.gather(discard_task, drain_task)
                if errors:
                    message = errors[-1][:220]
        except asyncio.CancelledError:
            raise
        except (OSError, EOFError, ValueError, TimeoutError, asyncio.LimitOverrunError) as exc:
            if message == "Input closed":
                message = str(exc) or "No new video frames"
        finally:
            feed.clear(message)
        logging.debug("%s: %s", feed.cam.id, message)
        await asyncio.sleep(2)
