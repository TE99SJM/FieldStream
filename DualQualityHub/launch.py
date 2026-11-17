"""Local cross-platform supervisor. Run python launch.py --help."""
import argparse
import asyncio
import getpass
import ipaddress
import os
import platform
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from settings import API, OBS_NAME, ROOT, cameras, path, recording_dir


def binary(name, candidates=()):
    for candidate in (ROOT / "bin" / name, *candidates, name):
        found = shutil.which(str(candidate))
        if found:
            return found
    raise SystemExit(f"Install {name} and put it on PATH or in the kit's bin folder.")


def guess_ip():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.0.2.1", 9))  # route selection only; sends no data
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def capture_args(ff, key, camera, index, demo):
    target = f"rtmp://127.0.0.1:1935/{path(key)}"
    opts = [ff, "-nostdin", "-hide_banner", "-loglevel", "warning"]
    if demo:
        hue = (index * 67) % 360
        inputs = ["-re", "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=30,hue=h={hue}",
                  "-f", "lavfi", "-i", f"sine=frequency={330+index*110}:sample_rate=48000"]
    else:
        cap = camera.get("capture", {})
        if not cap.get("enabled"):
            return None
        inputs = cap.get(platform.system())
        if not inputs or any("REPLACE" in i for i in inputs):
            raise SystemExit(f"Set the actual device(s) for {key} in cameras.json.")
        if not isinstance(inputs, list) or not all(isinstance(i, str) for i in inputs):
            raise SystemExit(f"{key}: input arguments must be an array of strings")
    audio = "1:a:0?" if inputs.count("-i") > 1 else "0:a:0?"
    encode = ["-c:v", "libx264", "-threads", "2", "-preset", "veryfast", "-crf", "18",
              "-tune", "zerolatency", "-pix_fmt", "yuv420p", "-force_key_frames", "expr:gte(t,n_forced*1)"]
    if not demo and camera["capture"].get("h264_copy", False):
        encode = ["-c:v", "copy"]
    return [*opts, *inputs, "-map", "0:v:0", "-map", audio, *encode,
            "-c:a","aac","-b:a","160k","-ar","48000","-f","flv",target]


def devices(ff):
    system = platform.system()
    if system == "Linux":
        print("Video devices (also see v4l2-ctl --list-devices):", flush=True)
        print(*(list(Path("/dev/v4l/by-id").glob("*")) or Path("/dev").glob("video*")), sep="\n")
        if shutil.which("arecord"):
            subprocess.run(["arecord", "-l"])
        return
    args = ["-list_devices","true","-f","dshow","-i","dummy"] if system=="Windows" else [
        "-f","avfoundation","-list_devices","true","-i",""]
    subprocess.run([ff, "-hide_banner", *args])


def stop(proc):
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            proc.send_signal(signal.SIGINT)
        proc.wait(timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        proc.kill()
        proc.wait()


def listening(host, port):
    try:
        with socket.create_connection((host,port), .3):
            return True
    except OSError:
        return False


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ip", help="Hub IPv4 address on the camera LAN")
    ap.add_argument("--network", help="Field LAN subnet (defaults to /24 around --ip)")
    ap.add_argument("--record-format", choices=("hybrid_mp4","mkv"), default="hybrid_mp4",
                    help="Program recording container applied with --configure-obs")
    ap.add_argument("--devices", action="store_true", help="List capture devices and exit")
    ap.add_argument("--demo", action="store_true", help="Publish synthetic color/motion and tone for each camera")
    ap.add_argument("--configure-obs", action="store_true", help="Create/update and select the dedicated OBS profile and scenes")
    ap.add_argument("--no-obs", action="store_true", help="Run the media hub without launching the OBS desktop")
    ap.add_argument("--cert", help="Trusted TLS certificate for the dashboard")
    ap.add_argument("--key-file", help="Private key for --cert")
    args = ap.parse_args()
    os.chdir(ROOT)
    ff = binary("ffmpeg")
    if args.devices:
        return devices(ff)
    if sys.version_info < (3, 10):
        raise SystemExit("Python 3.10 or newer required.")
    try:
        import yaml
        import uvicorn  # validate desktop installation before launching processes
        from obs_setup import configure
        import hub_controller
    except ImportError as exc:
        raise SystemExit(f"Missing dependency ({exc.name}). Run this Python with -m pip install -r requirements.txt.")
    if bool(args.cert) != bool(args.key_file):
        ap.error("--cert and --key-file must be provided together")
    ip = str(ipaddress.IPv4Address(args.ip or guess_ip()))
    network = ipaddress.IPv4Network(args.network or ip+"/24", strict=False)
    if ipaddress.IPv4Address(ip) not in network:
        ap.error("--ip must belong to --network")
    system = platform.system().lower()
    arch = {"amd64":"amd64", "x86_64":"amd64", "arm64":"arm64", "aarch64":"arm64"}.get(platform.machine().lower())
    receiver = ROOT.parent / "obs" / "server"
    repo_binary = receiver / "mediamtx.exe" if os.name=="nt" else receiver / "bin" / f"{system}-{arch}" / "mediamtx"
    mtx = binary("mediamtx", [repo_binary])
    ver = subprocess.check_output([mtx, "--version"], text=True).strip()
    if "v1.21.4" != ver:
        raise SystemExit(f"Install MediaMTX v1.21.4 (found {ver}).")
    # The hook resolves ffmpeg by executable name on every supported OS.
    os.environ["PATH"] = str(Path(ff).parent) + os.pathsep + os.environ["PATH"]
    for port, host in [(9997,"127.0.0.1"), (1935,"127.0.0.1"), (8554,"127.0.0.1"),
                       (8889,"127.0.0.1"), (8189,ip), (8000,ip)]:
        if listening(host,port):
            raise SystemExit(f"Port {port} is already in use. Stop the previous hub before starting this one.")
    cam_list = cameras()
    captures = {key: cmd for i,(key,c) in enumerate(cam_list.items())
                if (cmd := capture_args(ff, key, c, i, args.demo))}
    state = ROOT / ".runtime"
    state.mkdir(exist_ok=True, mode=0o700)
    scheme = "https" if args.cert else "http"
    os.environ.setdefault("FIELDSTREAM_KEY", secrets.token_urlsafe(18))
    if not os.getenv("OBS_PASSWORD") and not args.no_obs:
        os.environ["OBS_PASSWORD"] = getpass.getpass("OBS WebSocket password (from Tools > WebSocket Server Settings): ")
    cfg = yaml.safe_load((ROOT / "mediamtx.yml").read_text(encoding="utf-8"))
    cfg["authInternalUsers"][1]["ips"] = [str(network)]
    # Retain canonical cam_x paths and the shared keys already set on field cameras.
    cfg["paths"] = {k:v for k,v in cfg["paths"].items() if k.startswith("~")}
    keys = ["cam_" + k for k in cam_list]
    for k, c in cam_list.items():
        if alias := c.get("legacy_key"):
            keys.append(alias)
            cfg["paths"]["live/"+alias] = {"forward":[{"dest":"rtsp://127.0.0.1:8554/"+path(k)}]}
    cfg["authInternalUsers"][1]["permissions"][0]["path"] = "~^live/(" + "|".join(keys) + ")$"
    cfg["webrtcAdditionalHosts"] = [ip]
    cfg["webrtcIPsFromInterfaces"] = False
    cfg["webrtcLocalUDPAddress"] = ip + ":8189"
    cfg["webrtcLocalTCPAddress"] = ip + ":8189"
    base = recording_dir()
    base.mkdir(parents=True, exist_ok=True)
    cfg["pathDefaults"]["recordPath"] = base.as_posix() + "/%path/%Y-%m-%d_%H-%M-%S-%f_%z"
    effective = state / "mediamtx.yml"
    effective.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    procs = {}
    logs = {}
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name=="nt" else 0

    def start(label, cmd):
        if label not in logs:
            logs[label] = (state / (label + ".log")).open("ab", buffering=0)
        p = subprocess.Popen(cmd, stdout=logs[label], stderr=subprocess.STDOUT,
                             creationflags=flags, start_new_session=(os.name!="nt"))
        procs[label] = p
        return p

    try:
        media = start("media", [mtx, str(effective)])
        http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(50):
            if media.poll() is not None:
                raise RuntimeError("MediaMTX exited. See .runtime/media.log.")
            try:
                with http.open(API + "/v3/paths/list", timeout=.4):
                    break
            except OSError:
                time.sleep(.12)
        else:
            raise RuntimeError("MediaMTX API startup timed out.")
        if not args.no_obs and not listening("127.0.0.1",4455):
            obs = binary("obs", [Path(os.environ.get("ProgramFiles", "C:/Program Files")) /
                                 "obs-studio/bin/64bit/obs64.exe",
                                 "/Applications/OBS.app/Contents/MacOS/OBS", "obs64"])
            # Leave OBS under operator control even when this terminal closes.
            subprocess.Popen([obs, "--profile", OBS_NAME, "--collection", OBS_NAME],
                             cwd=Path(obs).parent, start_new_session=(os.name!="nt"), creationflags=flags)
        if args.configure_obs:
            asyncio.run(configure(args.record_format))
        for key, cmd in captures.items():
            start("capture-"+key, cmd)
        webargs = [sys.executable, "-m", "uvicorn", "hub_controller:app", "--host", ip,
                   "--port","8000","--no-access-log"]
        if args.cert:
            webargs.extend(["--ssl-certfile",args.cert,"--ssl-keyfile",args.key_file])
        web = start("web", webargs)
        for _ in range(80):
            if web.poll() is not None:
                raise RuntimeError("Dashboard exited. See .runtime/web.log.")
            if listening(ip,8000):
                break
            time.sleep(.12)
        else:
            raise RuntimeError("Dashboard startup timed out. See .runtime/web.log.")
        print(f"Dashboard   {scheme}://{ip}:8000\nOperator key   {os.environ['FIELDSTREAM_KEY']}",flush=True)
        print(f"Recordings  {base}\nCtrl+C stops the media hub. OBS stays open.",flush=True)
        for key,c in cam_list.items():
            if not args.demo and not captures.get(key) and c.get("capture"):
                print(f"{c['name']}: USB capture disabled; set its devices in cameras.json.",flush=True)
        retry_at = {}
        while True:
            if media.poll() is not None or web.poll() is not None:
                raise RuntimeError("A hub service exited. Check .runtime/media.log and web.log.")
            for key, cmd in captures.items():
                name = "capture-" + key
                if procs[name].poll() is not None and time.monotonic() >= retry_at.get(key,0):
                    print(f"Retrying {key}. Details in .runtime/{name}.log",flush=True)
                    start(name,cmd)
                    retry_at[key] = time.monotonic()+5
            time.sleep(.5)
    except KeyboardInterrupt:
        pass
    finally:
        for p in reversed(list(procs.values())):
            stop(p)
        for f in logs.values():
            f.close()


if __name__ == "__main__":
    try:
        def terminate(signum, frame):
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, terminate)
        main()
    except (RuntimeError,OSError) as exc:
        sys.exit(str(exc))
