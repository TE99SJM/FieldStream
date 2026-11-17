import sys
import time

import av


def monitor(url):
    options = {"analyzeduration": "500000", "probesize": "262144", "rtmp_live": "live"}
    with av.open(url, options=options, timeout=(6, 6)) as container:
        video = container.streams.video[0]
        frames = 0
        last_report = 0
        for packet in container.demux(video):
            if packet.size and (packet.pts is not None or packet.dts is not None):
                frames += 1
                now = time.monotonic()
                if now - last_report >= 0.5:
                    print(f"frame={frames}", flush=True)
                    last_report = now


if __name__ == "__main__":
    try:
        monitor(sys.argv[1])
    except (av.FFmpegError, IndexError, OSError) as exc:
        sys.exit(str(exc))
