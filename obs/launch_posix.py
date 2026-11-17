#!/usr/bin/env python3
"""Launch the local field camera receiver, OBS, and wireless monitor on macOS/Linux."""
import argparse
import fcntl
import importlib.util
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from contextlib import contextmanager, nullcontext, suppress
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parent
SERVER = ROOT / "server"
RUN = ROOT / "run"
CONFIG = SERVER / "mediamtx.yml"
STATE = RUN / "server.pid"
RTMP = 1935
API = 9997
HTTP = build_opener(ProxyHandler({}))


@contextmanager
def exclusive(name, wait=False):
    with (RUN / name).open("a") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError:
            raise RuntimeError("Another launcher is already monitoring this kit.") from None
        yield  # Closing the fd also releases the lock after a crash.


def executable(name):
    found = shutil.which(str(name))
    if not found:
        raise RuntimeError(f"Cannot run {name}. Verify installation, path and executable permission.")
    return str(Path(found).resolve())


def obs_command(system, override):
    if override:
        return [executable(override)]
    if system == "darwin":
        for apps in (Path("/Applications"), Path.home() / "Applications"):
            obs = apps / "OBS.app/Contents/MacOS/OBS"
            if obs.is_file():
                return [executable(obs)]
    if shutil.which("obs"):
        return [executable("obs")]
    if system == "linux" and shutil.which("flatpak"):
        probe = subprocess.run(["flatpak", "info", "com.obsproject.Studio"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if probe.returncode == 0:
            return [executable("flatpak"), "run", "com.obsproject.Studio"]
    raise RuntimeError("Install OBS Studio or supply --obs /full/path/to/the/OBS/executable.")


def obs_is_open():
    # ps comm is the executable name/path, independent of its arguments.
    entries = subprocess.check_output(["ps", "-U", str(os.getuid()), "-o", "stat=", "-o", "comm="],
                                      text=True)
    for line in entries.splitlines():
        state, name = line.strip().split(None, 1)
        if not state.startswith("Z") and Path(name).name in {"obs", "OBS"}:
            return True
    return False


def port_open(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def api_ready():
    try:
        with HTTP.open(f"http://127.0.0.1:{API}/v3/paths/list?itemsPerPage=1", timeout=0.6) as r:
            return isinstance(json.load(r)["items"], list)
    except (OSError, ValueError, KeyError, TypeError):
        return False


def is_kit_server(pid, binary, system):
    """A stale PID or unrelated RTMP server must never be adopted as this kit."""
    if pid <= 1:
        return False
    if system == "linux":
        try:
            cmd = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
            return cmd[:2] == [os.fsencode(binary), os.fsencode(CONFIG)]
        except OSError:
            return False
    result = subprocess.run(["ps", "-ww", "-p", str(pid), "-o", "command="],
                            capture_output=True, text=True)
    return result.returncode == 0 and result.stdout.strip() == f"{binary} {CONFIG}"


def owns_listeners(pid, system):
    if system == "darwin":
        # A stock macOS installation provides /usr/sbin/lsof to the user that
        # launched this process. Scope each query to both its PID and one port.
        return all(subprocess.run(["/usr/sbin/lsof", "-nP", "-a", "-p", str(pid),
                                   f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
                   for port in (RTMP, API))
    ports = set()
    try:
        inodes = set()
        for fd in Path(f"/proc/{pid}/fd").iterdir():
            with suppress(OSError):  # The server can close a descriptor mid-scan.
                inodes.add(os.readlink(fd))
        for family in ("tcp", "tcp6"):
            table = Path("/proc/net") / family
            if not table.exists():
                continue
            for line in table.read_text().splitlines()[1:]:
                fields = line.split()
                if fields[3] == "0A" and f"socket:[{fields[9]}]" in inodes:
                    ports.add(int(fields[1].split(":")[-1], 16))
    except (OSError, ValueError, IndexError):
        return False
    return {RTMP, API}.issubset(ports)


def ensure_server(binary, system):
    if not CONFIG.is_file():
        raise RuntimeError(f"Missing receiver configuration: {CONFIG}")
    try:
        previous = int(STATE.read_text().strip())
    except (OSError, ValueError):
        previous = 0
    previous_valid = is_kit_server(previous, binary, system)
    if not previous_valid and any(port_open(p) for p in (RTMP, API)):
        raise RuntimeError("TCP 1935 or 9997 is occupied by a service outside this launcher's PID record. "
                           "Inspect it before starting the kit.")
    child = None
    if previous_valid:
        pid = previous
    else:
        with (RUN / "mediamtx.log").open("ab") as log:
            child = subprocess.Popen([binary, str(CONFIG)], cwd=SERVER, stdin=subprocess.DEVNULL,
                                     stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        pid = child.pid
        STATE.write_text(f"{pid}\n")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if child is not None and child.poll() is not None:
            break
        if owns_listeners(pid, system) and port_open(RTMP) and api_ready():
            print(f"MediaMTX PID {pid} ready on TCP {RTMP}; localhost API {API} is responding.", flush=True)
            return
        time.sleep(0.25)
    # Cleanup only a failing child started during this invocation.
    if child is not None and child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
    raise RuntimeError(f"MediaMTX did not own both listeners and answer its API. Inspect {RUN / 'mediamtx.log'}.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--setup", action="store_true", help="open the receiver and OBS for first-time setup")
    parser.add_argument("--obs", metavar="PATH", help="path to native OBS executable")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="path to FFmpeg or command on PATH")
    parser.add_argument("--cameras", nargs="+", choices=("gopro1", "gopro2", "gopro3", "dji"),
                        default=["gopro2", "gopro3", "dji"],
                        help="include gopro1 only when the primary GoPro is configured for RTMP")
    parser.add_argument("--stale", type=int, default=12)
    parser.add_argument("--pixel-freeze", type=int, default=0)
    args = parser.parse_args()
    system = platform.system().lower()
    arch = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(
        platform.machine().lower())
    if system not in {"linux", "darwin"} or not arch:
        parser.error("This launcher supports x86-64 and ARM64 Linux and macOS.")
    if args.stale < 4 or args.pixel_freeze < 0:
        parser.error("--stale must be at least 4; --pixel-freeze must be nonnegative.")
    binary = executable(SERVER / "bin" / f"{system}-{arch}" / "mediamtx")
    command = obs_command(system, args.obs)
    if not args.setup:
        ffmpeg = executable(args.ffmpeg)
        if not importlib.util.find_spec("obsws_python"):
            raise RuntimeError("Install requirements.txt in .venv (or the Python selected by FIELD_PYTHON).")
    RUN.mkdir(mode=0o700, exist_ok=True)
    with (nullcontext() if args.setup else exclusive("monitor.lock")):
        with exclusive("startup.lock", wait=True):
            ensure_server(binary, system)
            if obs_is_open():
                print("Reusing open OBS. Select Field Stream under both Profile and Scene Collection.", flush=True)
            else:
                if not args.setup:
                    command.extend(["--profile", "Field Stream", "--collection", "Field Stream",
                                    "--scene", "Combined Multiview"])
                with (RUN / "obs-launch.log").open("ab") as log:
                    proc = subprocess.Popen(command, cwd=str(Path(command[0]).parent), stdin=subprocess.DEVNULL,
                                            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                (RUN / "obs.pid").write_text(f"{proc.pid}\n")
                time.sleep(0.5)
                if proc.poll() not in (None, 0):
                    raise RuntimeError(f"OBS failed to launch. Inspect {RUN / 'obs-launch.log'}.")
        if args.setup:
            print("Create/select Field Stream under both OBS menus, enable WebSocket, then provision the scenes.")
        else:
            # Run in this process so Ctrl+C or termination releases the monitor lock.
            import runpy
            sys.argv = [str(ROOT / "Monitor-Feeds.py"), "--ffmpeg", ffmpeg, "--stale", str(args.stale),
                        "--pixel-freeze", str(args.pixel_freeze), "--cameras", *args.cameras]
            runpy.run_path(sys.argv[0], run_name="__main__")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        sys.exit(str(error))
