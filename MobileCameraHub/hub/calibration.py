import asyncio
import json
from datetime import datetime, timezone
from math import floor
from pathlib import Path

import av
import numpy as np

from .config import GRID, STATE

PULSE_GAPS = np.array([5.0, 6.5])


def edges(times, values, minimum, channel):
    t = np.asarray(times)
    x = np.asarray(values)
    if len(t) < 20 or np.any(np.diff(t) < -0.001):
        raise ValueError(f"{channel}: missing or discontinuous timestamps.")
    low, high = np.percentile(x, [30, 97])
    if high - low < minimum:
        raise ValueError(f"{channel}: insufficient signal. Fill the center of each view with the slate and check mic level.")
    on, starts = False, []
    for when, value in zip(t, x):
        if not on and value >= low + 0.55 * (high - low):
            if not starts or when - starts[-1] > 0.8:
                starts.append(when)
            on = True
        elif on and value < low + 0.25 * (high - low):
            on = False
    matches = [starts[i:i + 3] for i in range(len(starts) - 2)
               if np.max(np.abs(np.diff(starts[i:i + 3]) - PULSE_GAPS)) < 0.22]
    if len(matches) != 1:
        raise ValueError(f"{channel}: could not identify one complete three-pulse sequence.")
    return np.array(matches[0])


def analyze(path, cfg, current):
    video_times, audio_times, audio_energy = [], [], []
    levels = [[] for _ in cfg.cameras]
    cols, rows = cfg.grid
    tone = np.exp(-2j * np.pi * 1000 * np.arange(480) / 48000)
    resampler = av.AudioResampler(format="flt", layout="mono", rate=48000)
    pending = np.empty(0, dtype=np.float32)
    next_time = None
    with av.open(str(path)) as container:
        if not container.streams.video or not container.streams.audio:
            raise ValueError("Record the OBS grid with one audible master microphone on the first recording track.")
        video, audio = container.streams.video[0], container.streams.audio[0]
        for packet in container.demux(video, audio):
            for frame in packet.decode():
                if frame.pts is None:
                    continue
                if isinstance(frame, av.VideoFrame):
                    video_times.append(float(frame.pts * frame.time_base))
                    patch = frame.to_ndarray(format="gray", width=32 * cols, height=32 * rows)
                    for i, values in enumerate(levels):
                        x, y = 32 * (i % cols), 32 * (i // cols)
                        values.append(float(patch[y+10:y+22, x+10:x+22].mean()))
                else:
                    for sample in resampler.resample(frame):
                        if sample.pts is None:
                            continue
                        ts = float(sample.pts * sample.time_base)
                        if next_time is None or abs(ts - (next_time + len(pending) / 48000)) > 0.025:
                            pending = np.empty(0, dtype=np.float32)
                            next_time = ts
                        chunk = np.concatenate((pending, sample.to_ndarray().reshape(-1)))
                        n = len(chunk) // 480
                        if n:
                            blocks = chunk[:n*480].reshape(n, 480)
                            audio_energy.extend(np.abs(blocks @ tone) / 240)
                            audio_times.extend(next_time + np.arange(n) * 0.01)
                            next_time += n * 0.01
                        pending = chunk[n*480:]
    points = {c.id: edges(video_times, v, 32, c.label) for c, v in zip(cfg.cameras, levels)}
    points["@audio"] = edges(audio_times, audio_energy, 0.006, "Master microphone")
    if len(video_times) < 2:
        raise ValueError("No usable video.")
    frame_ms = float(np.median(np.diff(video_times)) * 1000)
    if not 8 <= frame_ms <= 100:
        raise ValueError("Recording frame timestamps are unsuitable for calibration.")
    matrix = np.stack(list(points.values())) * 1000
    rel = matrix - matrix[0]
    center = np.median(rel, axis=1)
    residual = rel - center[:, None]
    variability = float(np.ptp(residual, axis=0).max())
    skew = float(np.ptp(matrix, axis=0).max())
    raw = center - np.array([current[k] for k in points])
    values = {}
    for channel, delay in zip(points, raw.max() - raw):
        values[channel] = round(delay) if channel == "@audio" else floor(delay + 1e-6)
    early = np.median(matrix.max(axis=0) - matrix, axis=1)
    measured = {channel: round(float(ms), 1) for channel, ms in zip(points, early)}
    return dict(offsets_ms=values, median_early_ms=measured, observed_skew_ms=round(skew, 1), variation_ms=round(variability, 1),
                frame_ms=round(frame_ms, 1), within_frame=skew <= frame_ms + 1,
                stable=variability <= frame_ms + 1, pulses=3)


class Calibration:
    def __init__(self, cfg, obs, feeds):
        self.cfg, self.obs, self.feeds = cfg, obs, feeds
        self.task = None
        self.status = {"phase": "idle"}
        self.report_file = STATE / "calibration.json"
        if self.report_file.exists():
            try:
                self.status["last"] = json.loads(self.report_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass

    @property
    def active(self):
        return self.task is not None and not self.task.done()

    async def start(self, mode):
        if self.active:
            raise RuntimeError("Calibration is already running.")
        await self.obs.guard(idle=True)
        missing = [f.cam.label for f in self.feeds.values() if f.status()["state"] != "receiving"]
        if missing:
            raise RuntimeError("Waiting for live feeds: " + ", ".join(missing))
        current = await self.obs.delays()
        scene = (await self.obs.call("GetCurrentProgramScene"))["currentProgramSceneName"]
        await self.obs.call("SetCurrentProgramScene", sceneName=GRID)
        try:
            await self.obs.call("StartRecord")
            for _ in range(12):
                await asyncio.sleep(0.2)
                if (await self.obs.call("GetRecordStatus"))["outputActive"]:
                    break
            else:
                raise RuntimeError("OBS could not start recording. Check its recording folder and encoder.")
        except Exception:
            await self.obs.call("SetCurrentProgramScene", sceneName=scene)
            raise
        self.status.update(phase="recording", message="Recording the three pulses.")
        self.task = asyncio.create_task(self.finish(scene, current, mode))

    async def finish(self, scene, current, mode):
        try:
            try:
                await asyncio.sleep(25)
            finally:
                recording = await self.obs.call("GetRecordStatus")
                if not recording["outputActive"]:
                    raise RuntimeError("OBS stopped recording before the slate finished. Check disk space and encoder settings.")
                result = await self.obs.call("StopRecord")
                for _ in range(80):
                    if not (await self.obs.call("GetRecordStatus"))["outputActive"]:
                        break
                    await asyncio.sleep(0.2)
                else:
                    raise RuntimeError("OBS is still finalizing the recording. Check the recording output in OBS.")
            await self.obs.guard(idle=True)
            if (await self.obs.call("GetCurrentProgramScene"))["currentProgramSceneName"] != GRID:
                raise RuntimeError("Program scene changed during calibration.")
            self.status.update(phase="analyzing", message="Reading recorded frame and microphone timestamps.")
            path = Path(result["outputPath"])
            data = await asyncio.to_thread(analyze, path, self.cfg, current)
            data.update(mode=mode, recorded=str(path), measured_at=datetime.now(timezone.utc).isoformat(),
                        previous_ms=current, applied=False)
            if mode == "apply":
                if not data["stable"]:
                    message = "Feed timing varied by more than a frame. Repeat with a stable network."
                elif max(data["offsets_ms"].values()) > 4000:
                    message = "Measured offset exceeds the 4 s mobile buffer limit. Reduce buffering and retry."
                else:
                    await self.obs.guard(idle=True)
                    await self.obs.apply(data["offsets_ms"], current)
                    data["applied"] = True
                    message = "Offsets applied. Run Verify to measure the resulting alignment."
            else:
                message = "Verified within one recorded frame." if data["within_frame"] else "Residual exceeds one recorded frame. Use Measure & apply to refine, then verify again."
            data["message"] = message
            tmp = self.report_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(self.report_file)
            self.status.update(phase="done", message=message, last=data)
        except Exception as exc:
            self.status.update(phase="error", message=str(exc))
        finally:
            try:
                await self.obs.guard(idle=True)
                if (await self.obs.call("GetCurrentProgramScene"))["currentProgramSceneName"] == GRID:
                    await self.obs.call("SetCurrentProgramScene", sceneName=scene)
            except Exception as exc:
                self.status.update(phase="error", message=f"{self.status.get('message', '')} Scene restore: {exc}")

    async def close(self):
        if self.active:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
