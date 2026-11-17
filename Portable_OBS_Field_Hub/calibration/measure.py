"""Measure when five flashes reach each camera tile in an OBS Field - Grid recording."""
import argparse
import os
from itertools import pairwise
from pathlib import Path
import shutil
from statistics import median
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "control"))
from scenes import GRID_RECTS, frame_lags

FPS = 30
PULSE_GAPS = (7, 9, 6, 8)


def read_centers(movie, cameras, ffmpeg):
    # Normalize a 16:9 program recording and average a small patch at each tile center.
    n = len(cameras)
    tags = "".join(f"[c{i}]" for i in range(n))
    graph = [f"[0:v:0]fps={FPS},scale=480:270,format=gray,split={n}{tags}"]
    for i, name in enumerate(cameras):
        x, y, w, h = GRID_RECTS[name]
        x0 = round((x + w / 2) / 4) - 8
        y0 = round((y + h / 2) / 4) - 6
        graph.append(f"[c{i}]crop=16:12:{x0}:{y0},scale=1:1:flags=area[s{i}]")
    graph.append("".join(f"[s{i}]" for i in range(n)) + f"hstack=inputs={n}[out]")
    cmd = [ffmpeg, "-v", "error", "-nostdin", "-i", str(movie), "-filter_complex_threads",
           "1", "-filter_complex", ";".join(graph), "-map", "[out]", "-t", "75",
           "-an", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=240)
    if result.returncode:
        raise ValueError(result.stderr.decode(errors="replace").strip() or "FFmpeg could not decode this file.")
    return {name: result.stdout[i::n] for i, name in enumerate(cameras)}


def pulse_starts(samples):
    if len(samples) < FPS * (sum(PULSE_GAPS) + 1):
        raise ValueError("recording is too short for the five-flash sequence")
    sorted_samples = sorted(samples)
    low = median(sorted_samples[:len(samples) // 2])
    high = sorted_samples[int(len(samples) * 0.98)]
    if high - low < 38:
        raise ValueError("too little brightness change at the center of the tile")
    rising, falling = low + 0.65 * (high - low), low + 0.4 * (high - low)
    events, start = [], None
    for i, lum in enumerate(samples):
        if start is None:
            if lum >= rising:
                start = i
        elif lum <= falling:
            if 9 <= i - start <= 42:
                events.append(start)
            start = None
    windows = []
    for i in range(len(events) - len(PULSE_GAPS)):
        five = events[i:i + 5]
        if all(abs(b - a - gap * FPS) <= 9 for (a, b), gap in zip(pairwise(five), PULSE_GAPS)):
            windows.append(five)
    if len(windows) != 1:
        raise ValueError("could not uniquely identify all five flashes in order")
    return windows[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recording", type=Path)
    parser.add_argument("--cameras", help="Require exactly these comma-separated IDs, e.g. sony,dji")
    parser.add_argument("--ffmpeg", help="Path to the FFmpeg executable")
    args = parser.parse_args()
    cameras = list(GRID_RECTS) if not args.cameras else args.cameras.split(",")
    if len(cameras) < 2 or len(set(cameras)) != len(cameras) or set(cameras) - GRID_RECTS.keys():
        parser.error("Choose at least two different IDs from " + ", ".join(GRID_RECTS))
    if not args.recording.is_file():
        parser.error("Recording file does not exist.")
    bundled = ROOT / "tools/ffmpeg.exe"
    ffmpeg = args.ffmpeg or os.getenv("FIELDHUB_FFMPEG") or (str(bundled) if bundled.is_file() else shutil.which("ffmpeg"))
    if not ffmpeg:
        parser.error("Put ffmpeg.exe in tools or add FFmpeg to PATH.")
    tracks = read_centers(args.recording, cameras, ffmpeg)
    arrivals = {}
    for camera, data in tracks.items():
        try:
            arrivals[camera] = pulse_starts(data)
            print(f"{camera:8} event frames  " + "  ".join(str(i) for i in arrivals[camera]))
        except ValueError as exc:
            print(f"{camera:8} skipped: {exc}", file=sys.stderr)
    if len(arrivals) < 2 or (args.cameras and len(cameras) != len(arrivals)):
        raise ValueError("Match the target in every required tile and repeat the recording.")
    cols = list(zip(*arrivals.values()))
    measured = [dict(zip(arrivals, col)) for col in cols]
    lags, _ = frame_lags(measured)
    offsets = {cam: [max(col) - frame for col, frame in zip(cols, values)]
               for cam, values in arrivals.items()}
    print("\nCamera     delay (frames / ms)     observed range (frames)")
    unstable = False
    for cam, values in offsets.items():
        mid = lags[cam]
        print(f"{cam:9}  {mid:4} / {round(mid * 1000 / FPS):4} ms          {min(values)} .. {max(values)}")
        unstable |= max(abs(x - mid) for x in values) > 1
    if unstable:
        raise ValueError("Offset varied by more than one frame around its median. Check Wi-Fi and repeat before applying.")
    print("\nFor a baseline captured with kit delays cleared, apply in PowerShell from the kit folder.")
    print("For a post-calibration check, use the table above to read residual error.")
    frame_args = ["--frames " + ",".join(f"{cam}={arrivals[cam][i]}" for cam in arrivals)
                  for i in range(5)]
    print(r".\Field-OBS.cmd calibrate --measured-fps 30 " + " ".join(frame_args))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"Measurement stopped: {exc}", file=sys.stderr)
        sys.exit(1)
