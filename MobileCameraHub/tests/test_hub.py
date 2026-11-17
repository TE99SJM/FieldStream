import asyncio
from dataclasses import replace
from pathlib import Path
import subprocess
import tempfile
import unittest
import wave

import numpy as np

from hub.calibration import analyze, edges
from hub.capture import Feed, capture_args, ffmpeg, read_jpegs
from hub.config import load_config
from hub.media import receiver_config
from hub.obs import Obs


class HubTests(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config()
        self.cam = self.cfg.cameras[0]

    def test_native_mjpeg(self):
        args = capture_args(self.cam)
        self.assertIn("1280x720", args)
        self.assertEqual(args[args.index("-vcodec")+1], "mjpeg")
        self.assertEqual(args[args.index("-c:v")+1], "copy")

    def test_yuy2(self):
        args = capture_args(replace(self.cam, format="yuy2", width=1920, height=1080, fps=60))
        self.assertIn("yuyv422", args)
        self.assertIn("60", args)
        self.assertIn("1920x1080", args)

    def test_receiver_exposure(self):
        config = receiver_config(self.cfg)
        self.assertEqual(config["apiAddress"], "127.0.0.1:9997")
        self.assertEqual(set(config["paths"]), {"live/dji", "live/gopro2"})
        self.assertEqual(self.cam.url(8088), "http://127.0.0.1:8088/video/sony.mjpg")

    def test_bad_reference(self):
        with self.assertRaisesRegex(ValueError, "insufficient signal"):
            edges(range(100), [0]*100, 32, "Camera")

    def test_no_fabricated_fps(self):
        feed = Feed(self.cam)
        self.assertIsNone(feed.status()["fps"])
        feed.advance(1)
        self.assertIsNone(feed.status()["fps"])
        feed.last -= 10
        self.assertEqual(feed.status()["state"], "stalled")
        self.assertIsNone(feed.status()["fps"])

    def test_recorded_calibration(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            rate, duration = 30, 22
            pulses = np.array([2.5, 7.5, 14.0])
            offsets = np.array([0.1, 0.3, 2.2, 1.8])
            with (tmp / "grid.raw").open("wb") as raw:
                for frame in range(rate*duration):
                    times = frame/rate - offsets[:, None] - pulses[None, :]
                    levels = np.any((times >= -1e-7) & (times < 0.48), axis=1)
                    tile = (20 + 200*levels).astype("uint8").reshape(2, 2)
                    raw.write(tile.repeat(20, 0).repeat(32, 1).tobytes())
            sr = 48000
            t = np.arange(sr*duration)/sr
            tone = np.sin(2*np.pi*1000*t) * 13000
            gate = np.any([(t >= p+0.2) & (t < p+0.68) for p in pulses], axis=0)
            with wave.open(str(tmp/"mic.wav"), "wb") as w:
                w.setparams((1, 2, sr, 0, "NONE", ""))
                w.writeframes((tone*gate).astype("<i2").tobytes())
            path = tmp/"grid.mkv"
            subprocess.run([ffmpeg(), "-loglevel","error","-f","rawvideo","-pix_fmt","gray","-s","64x40","-r","30",
                            "-i",str(tmp/"grid.raw"),"-i",str(tmp/"mic.wav"),"-c:v","ffv1","-c:a","pcm_s16le",str(path)],check=True)
            previous = {c.id: 0 for c in self.cfg.cameras}
            if len(previous) != 4:
                self.skipTest("This fixture uses the supplied four-camera layout.")
            previous["@audio"] = 0
            result = analyze(path, self.cfg, previous)
            self.assertTrue(result["stable"])
            self.assertFalse(result["within_frame"])
            for value, target in zip(result["offsets_ms"].values(), [2100,1900,0,400,2000]):
                self.assertLessEqual(abs(value-target), 3)


class AsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_jpeg_reader(self):
        feed = Feed(load_config().cameras[0])
        reader = asyncio.StreamReader()
        reader.feed_data(b"--line\r\nContent-type: image/jpeg\r\nContent-length: 4\r\n\r\nabcd")
        reader.feed_eof()
        with self.assertRaises(asyncio.IncompleteReadError):
            await read_jpegs(reader, feed)
        self.assertEqual(feed.frame, b"abcd")

    async def test_render_stack(self):
        cfg = replace(load_config(), delay_filter="render")
        obs = Obs(cfg)
        filters = {}
        async def call(name, **data):
            if name == "GetSourceFilterList":
                return {"filters": list(filters.values())}
            key = data["filterName"]
            if name == "RemoveSourceFilter":
                del filters[key]
            else:
                filters[key] = dict(filterName=key, filterSettings=data["filterSettings"],
                                    filterKind="gpu_delay", filterEnabled=True)
            return {}
        obs.call = call
        await obs.video_delay(cfg.cameras[0], 2125)
        self.assertEqual([x["filterSettings"]["delay_ms"] for x in filters.values()], [500,500,500,500,125])
        await obs.video_delay(cfg.cameras[0], 325)
        self.assertEqual(len(filters), 1)
        await obs.video_delay(cfg.cameras[0], 0)
        self.assertFalse(filters)


if __name__ == "__main__":
    unittest.main()
