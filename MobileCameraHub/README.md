# Mobile Camera Hub

A Windows x64 field switcher for a Sony ZV-1F, a GoPro Webcam input and local RTMP cameras. The phone selects the program shot. OBS on the hub composites, records and sends the single broadcast. Once the software is installed, the hub can ingest and record without a WAN connection.

## Camera plan

| Input | Camera-to-hub link | Starting mode | What to check |
|---|---|---|---|
| Sony ZV-1F | USB Streaming on a USB 3 host port | 1280 × 720, MJPEG, 30 fps in NTSC or 25 fps in PAL | The camera has a USB 2.0 interface. Native 1080p and SuperSpeed signaling are unavailable on this model. OBS scales it to the canvas. |
| Primary GoPro | USB cable and Windows GoPro Webcam utility | Start at 1920 × 1080, 30 fps, format `auto` if advertised | The utility supplies a virtual Windows camera. Model, firmware and driver determine available modes. This path does not establish native UVC or SuperSpeed signaling. |
| DJI Osmo 360 | Mimo, single-lens livestream, custom RTMP | Use an available single-lens streaming mode | DJI specifies a secured Wi-Fi network with internet access for setup. Verify both starting and maintaining a local stream with the WAN disconnected before field use. |
| Additional GoPros | Quik custom RTMP to the travel router or hotspot | Start at a resolution supported by the particular GoPro | Check custom RTMP, band support and offline startup on each camera and app version. |

A requirement for physically negotiated USB 3.0 on every wired camera is incompatible with the ZV-1F. This build uses the named cameras and USB 3 host ports. Its only external video inputs are the requested USB connections and wireless RTMP.

## Setup checkpoints

Complete these in order on the hub. Each checkpoint includes a result you can verify.

### 1. Prepare the Windows hub

Use 64-bit Windows on Intel or AMD with **Python 3.12 x64**, **OBS Studio 30 or later** with the v5 WebSocket server, and the **GoPro Webcam utility** compatible with your primary GoPro. Install the software and any camera firmware updates while internet access is available. Extract the entire ZIP to a writable location such as `C:\MobileCameraHub`. Keep the project files together.

Connect the Sony by a data-capable cable. On the camera choose **Menu → Setup → USB → USB Connection Mode → USB Streaming**. Connect the primary GoPro to another USB 3 host port and start its Webcam utility. Close programs holding either video input, including any utility preview window.

**Check** — The camera displays USB Streaming standby, the GoPro utility recognizes its camera and `py -3.12 --version` in PowerShell reports Python 3.12.

### 2. Install and select capture modes

Double-click **Setup.cmd**. It creates a project-local virtual environment, installs the pinned dependencies and lists Windows capture devices. FFmpeg is supplied by the imageio-ffmpeg Windows wheel. MediaMTX v1.21.4 for Windows x64 is already in `bin`.

To install from PowerShell, open the extracted project folder and run the commands below.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --only-binary=:all: -r requirements.txt
.\.venv\Scripts\python.exe main.py devices
.\.venv\Scripts\python.exe main.py modes "GoPro Webcam"
.\.venv\Scripts\python.exe main.py modes "ZV-1F"
```

Use the exact names printed by `devices` in the two `modes` commands and the `device` fields in `server_config.yml`. DirectShow may also print alternate device IDs. They are useful for identical friendly names. Alternatively set `device_number: 1` on the second device with the same name. FFmpeg ends the mode listing with an exit message once it has printed the capabilities.

For a USB input configure `width`, `height`, `fps` and `format` together from a single driver-advertised mode. Formats are `mjpeg`, `yuy2` and `auto`. Prefer MJPEG when advertised. The app copies MJPEG frames without re-encoding. For YUY2 or auto it converts the negotiated frames to JPEG for the internal OBS connection. At 1080p30, uncompressed YUY2 carries about 995 Mb/s before USB overhead. At 60 fps it carries about 1.99 Gb/s. Two physical connectors may share a host controller. Use Windows Device Manager’s **View → Devices by connection** to inspect the topology if frames drop. A powered USB hub adds power capacity but shares its upstream link.

**Check** — Each configured USB device has a distinct identity. The chosen size, frame rate and input format appear together in its mode list. Leave the GoPro at a supported mode if 60 fps is absent.

### 3. Prepare OBS and the master microphone

1. In OBS choose **Scene Collection → New** and name it **Mobile Hub**. Select this collection. The app checks its name before editing scenes.
2. In **Settings → Video**, start with a 1920 × 1080 base and output canvas at 30 fps. Set your recording directory under **Settings → Output**, use a local MKV recording and select a hardware encoder supported by your tablet when available. Calibration analyzes the first audio track in the recording. Use a standard, unsplit OBS recording for that measurement.
3. Under **Settings → Audio**, select one global **Mic/Auxiliary Audio** device. The Sony USB microphone, a USB microphone or the hub microphone can serve as master. Set other global audio devices to Disabled for calibration. In Advanced Audio Properties, send the master mic to recording track 1 and to your chosen streaming track. Use headphones if monitoring the mic.
4. Confirm that the input name in the mixer exactly matches `master_mic` in the YAML. The default is `Mic/Aux`. Speak and watch its meter. The app mutes each managed camera’s audio so cuts between cameras keep one continuous microphone.
5. Open **Tools → WebSocket Server Settings**, enable the server on port **4455**, enable authentication and keep the password available.

**Check** — The Mobile Hub collection is selected, the microphone meter moves, and OBS can make and play back a short recording with audible speech.

### 4. Connect the field network and start the receiver

Connect the Windows hub, the operator phone and compatible cameras to a private, secured 5 GHz network. Use a travel router with wireless client isolation disabled or a hotspot that permits peers to communicate. Keep the hub’s Wi-Fi address stable with a DHCP reservation where available. A separate operator phone is helpful when a camera app needs its phone to remain paired.

Double-click **Start.cmd**, or run the following:

```powershell
.\.venv\Scripts\python.exe main.py serve
```

Enter the OBS WebSocket password at the hidden prompt. Keep the terminal open. It prints a fresh **control key** and candidate LAN addresses. On the hub, open `http://127.0.0.1:8088`. On the phone, use the **hub’s Wi-Fi IPv4 address**, for example `http://192.168.77.10:8088`. If the hub also has Tailscale, Ethernet or a virtual adapter, use `ipconfig` to identify the field Wi-Fi address.

Enter the control key in the phone dashboard. Tap **Build OBS scenes** once. The app creates a grid and one full-screen scene per camera using shared inputs. Running it again refreshes the managed layout. Select **All cameras** to put the grid on program. Only the hub needs OBS.

If Windows blocks local access, mark the trusted field Wi-Fi as **Private**. For the default ports, run the following once in administrator PowerShell and allow only traffic from the field subnet. Replace the example subnet with yours.

```powershell
New-NetFirewallRule -DisplayName "Mobile Camera Hub - field Wi-Fi" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 1935,8088 -Profile Private -RemoteAddress 192.168.77.0/24
```

OBS control and MediaMTX management use loopback. Keep ports 4455 and 9997 closed to other devices. Use the dashboard and RTMP only on your private field network. The control key travels over local HTTP, and a device on that network can publish to a configured RTMP path.

**Check** — The phone shows **Receiver · Listening**, an OBS frame rate and two receiving USB inputs. Enable **Preview** to see the program thumbnail. It refreshes periodically.

### 5. Publish each wireless camera

With a sample hub address of `192.168.77.10`, use the following values. Replace that address with the one printed for your hub’s Wi-Fi interface.

| Camera | Server if the app separates fields | Key | Full URL on the camera or phone | URL used inside OBS on the hub |
|---|---|---|---|---|
| DJI Osmo 360 | `rtmp://192.168.77.10:1935/live` | `dji` | `rtmp://192.168.77.10:1935/live/dji` | `rtmp://127.0.0.1:1935/live/dji` |
| Second GoPro | `rtmp://192.168.77.10:1935/live` | `gopro2` | `rtmp://192.168.77.10:1935/live/gopro2` | `rtmp://127.0.0.1:1935/live/gopro2` |

For the Osmo, connect Mimo to the camera, open Preview, select **single-lens mode → Livestream → RTMP**, choose the field Wi-Fi, and enter the destination. For each compatible GoPro, connect Quik to that camera, open **Live / Set Up Live → RTMP**, select the same network, enter its own destination, and start the stream. Menu names vary by model and app version. If the app takes one field, use the full URL once.

The receiver listens on port 1935 on the hub’s IPv4 interfaces. The management API listens only on `127.0.0.1:9997`. Loopback refers to whichever device is using it, so a camera using `127.0.0.1` would send to itself. Internet throughput is irrelevant to these local RTMP hops once the camera has agreed to publish.

To add a GoPro, stop the hub, append another camera row, restart and tap **Build OBS scenes**. IDs must be unique. The server generates its allowed RTMP paths from this same list.

```yaml
  - id: gopro3
    label: GoPro Side
    transport: rtmp
```

**Check** — All camera rows show **receiving** and measured FPS, and every solo button shows its matching angle in OBS. Disconnect the router WAN and test ongoing transit, then stop and restart each camera’s stream to test offline startup separately.

### 6. Measure and verify sync

1. Keep the stream and recording stopped. Put all cameras in front of the same phone or tablet. Position it so its screen fills the **center of each tile** and set its speaker near the master mic. Close any OBS overlays covering the tile centers. Keep the cameras and phone still.
2. On that device, open the paired dashboard and tap **Open sync slate → Measure & apply**. Keep the tab in front, screen awake and speaker audible. The app selects the grid and makes a short OBS recording of three white flashes with 1 kHz tones. It retains the file in the OBS recording directory.
3. After analysis, the dashboard reports the observed maximum spread and the offsets actually applied. The calculator uses the three frame and audio timestamps, the current offsets, and the latest arriving channel. It adjusts each early source, including faster wireless cameras if needed, and the master microphone. Weak signals, ambiguous pulses, unstable timing and corrections greater than four seconds do not produce new settings.
4. Tap **Verify only** for a second independent recording. A pass means all three measured events, across the cameras and mic, fell within one recorded frame (plus 1 ms timestamp rounding). At 30 fps a frame is about 33 ms. If verification exceeds a frame, inspect each input’s measured lead and repeat **Measure & apply**, then **Verify only**. Do a spoken clap check on the recording as well. A phone’s speaker/display skew, rolling shutter, exposure, mic distance and changing wireless buffers affect real-world lip-sync.

The default `delay_filter: async` uses OBS **Video Delay (Async)** for multi-second buffering. To use the literal **Render Delay** effect, choose `delay_filter: render` before first calibration. The app builds stages of up to 500 ms each, so 2,125 ms uses four 500 ms stages and one 125 ms stage. Multi-second buffers use substantial memory, especially with GPU Render Delay at 1080p60. To change methods later, remove only the app’s **Hub • Camera delay** filters from its sources while off air, change the YAML, restart and recalibrate.

**Check** — An independent verification reports the measured spread. Recheck after reconnecting a publisher or changing FPS, camera mode, network or OBS buffering. Independent cameras and RTMP have no shared hardware clock.

### 7. Record or broadcast

Tap a solo camera or **All cameras** on the phone to switch the OBS program. The **On air** indicator follows actual OBS stream status. For offline use, choose **Start Recording** in OBS. For an internet broadcast, give the hub a working uplink, configure the platform and stream key in **OBS → Settings → Stream**, then select **Start Streaming** in OBS. Camera publishers keep the local RTMP destinations from step 5.

Wireless **Reset** disconnects only the current publisher for that camera. The endpoint remains available. If the camera does not retry automatically, restart its custom RTMP stream in Mimo or Quik. If it is already disconnected, the app reports that the endpoint is ready. Stop the hub with **Ctrl+C** after ending the OBS broadcast.

**Check** — Record a local rehearsal with several scene changes. Unplug and reconnect one camera, confirm the other angles remain usable, then review picture, sound and sync on the recording before going live.

## What the status panel measures

- **USB FPS** counts complete MJPEG frames delivered by the camera bridge. The USB input opens once per camera. OBS reads the latest complete frame over loopback HTTP. Slow readers skip stale frames. The private bridge accepts connections only from the hub.
- **Local RTMP FPS** estimates received coded video frames from demultiplexed video access units over a rolling wall-clock window. The monitor uses a local read-only connection per active publisher. Audio traffic and encoder metadata do not count as video. A connected publisher can still be waiting for video. Repeated identical images with advancing timestamps count as delivered frames.
- **OBS output FPS** is the compositor’s rate. The optional program thumbnail shows the actual selected scene. A blank tile with healthy ingest warrants checking the OBS input and program preview.
- **Sync history** is saved to `runtime/calibration.json`. OBS persists its scene collection and delay filters. Rebuilding refreshes the grid after a camera-list change. Old solo scenes remain in OBS for manual cleanup.

## Troubleshooting

| Symptom | Field check |
|---|---|
| Sony offline | Select USB Streaming, use a data cable, match the exact device name, close other capture apps, and check 720p MJPEG at the camera’s NTSC/PAL frame rate. |
| GoPro USB offline | Check compatibility with the Windows Webcam utility. Close its preview, list DirectShow modes and try the exact advertised profile with `format: auto`. |
| Phone cannot reach hub | Confirm the Wi-Fi IPv4 address, subnet, Windows Private firewall profile and router peer isolation. Test the dashboard on the hub first. |
| Receiver fails on launch | Check `runtime/mediamtx.log`. Stop any other hub instance or service already using 1935 or 9997. The dashboard also needs its configured port free. |
| OBS offline or controls unavailable | Check port 4455 and password. Restart the hub after correcting its password. Select the configured collection, then build scenes while off air. |
| Missing wireless image | Verify the exact case-sensitive path, active publisher, compatible camera mode and Wi-Fi band. Use the LAN address in the camera app. Start its stream again if the manufacturer app requires it. |
| Sync reports a weak mic or missing pulse | Select the right global mic, unmute it, assign it to recording track 1, and verify the speaker tone is audible on the saved MKV. Increase screen brightness or move closer to fill every tile center. |
| Variable sync or dropped frames | Try 30 fps, lower the wireless bitrate, put the router nearer the cameras, and check OBS Stats, CPU, memory, thermals and battery draw during a full-length rehearsal. |

## Project layout

```text
MobileCameraHub/
  main.py                 Launch, device enumeration and mode inspection
  server_config.yml       Camera identities, modes and hub settings
  Setup.cmd / Start.cmd   Windows entry points
  requirements.txt        Python dependencies
  bin/                    Windows x64 MediaMTX 1.21.4 and its license
  dashboard/              Offline touch switcher and audiovisual slate
  hub/
    calibration.py        Analyze actual OBS recordings and verify offsets
    capture.py            Device manager, MJPEG transport and frame health
    config.py             Validated camera configuration and layout
    media.py              Receiver lifecycle, path monitoring and targeted reset
    obs.py                WebSocket v5, scene provisioning and delay filters
    probe.py              Isolated compressed-video RTMP frame counter
    process.py            Child-process lifecycle
    web.py                FastAPI dashboard and authenticated control routes
  tests/                  Regression tests with a generated four-view AV recording
  runtime/                Created at launch for receiver config, log and last measurement
```

From the project directory, run `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` with the supplied four-camera configuration. For unattended launch, supply `OBS_PASSWORD` through the process environment. `HUB_CONTROL_KEY` optionally provides a stable dashboard key (12 or more printable ASCII characters). The default is a new random key each launch. `HUB_FFMPEG` and `HUB_MEDIAMTX` can point at alternate local binaries. The generated receiver configuration targets the bundled MediaMTX version.

## Bench verification

The software was exercised with a local Linux OBS Studio 30.2.3 instance and MediaMTX 1.21.4. Two generated MJPEG feeds and two live RTMP publishers appeared together in the OBS program grid. An authenticated phone-sized Chromium session switched the program to a solo source. Resetting one publisher left the other publisher running, and the disconnected camera path accepted a new publisher. Repeated provisioning reused the existing scene items.

A separate generated audiovisual reference was recorded and analyzed by OBS. The app read the actual recording, measured approximately two seconds of relative timing difference, applied per-camera and microphone offsets, and read them back from OBS. On the final 30 fps bench run, the initial maximum spread was 2,066.7 ms. After correction, a second three-pulse recording measured 33.3 ms across the four reference views and the reference audio, within one recorded frame. The tests also cover known-offset MKV decoding and multi-stage Render Delay. Windows x64 dependency wheels were resolved for Python 3.12. DirectShow negotiation with your cameras, the camera apps’ WAN-disconnected behavior, sustained tablet performance and delivery to your chosen streaming platform require a field rehearsal.

## Technical references

- [Sony ZV-1F USB Streaming modes](https://helpguide.sony.net/dc/2210/v1/en/contents/TP1000934575.html) · [Sony ZV-1F interface specifications](https://www.sony.com/electronics/support/compact-cameras-zv-series/zv-1f/specifications)
- [Osmo 360 support and livestream setup](https://www.dji.com/support/product/360) · [DJI Mimo livestream guide](https://repair.dji.com/help/content?customId=en-us03400006728&lang=en)
- [GoPro Webcam software](https://gopro.com/webcam) · [GoPro custom RTMP setup](https://gopro.com/en/us/news/how-to-live-stream-on-gopro)
- [OBS Studio downloads](https://obsproject.com/download) · [OBS WebSocket setup](https://obsproject.com/kb/remote-control-guide) · [WebSocket v5 protocol](https://github.com/obsproject/obs-websocket/blob/master/docs/generated/protocol.md)
- [OBS async delay implementation](https://github.com/obsproject/obs-studio/blob/master/plugins/obs-filters/async-delay-filter.c) · [OBS Render Delay implementation](https://github.com/obsproject/obs-studio/blob/master/plugins/obs-filters/gpu-delay.c)
- [MediaMTX v1.21.4 binary release](https://github.com/bluenviron/mediamtx/releases/tag/v1.21.4) · [version-matched API](https://github.com/bluenviron/mediamtx/blob/v1.21.4/api/openapi.yaml) · [FFmpeg DirectShow options](https://ffmpeg.org/ffmpeg-devices.html#dshow)
