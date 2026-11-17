# FieldStream Dual Hub

A tablet shows a silent 480p15 preview of every camera. Tap a tile to cut the corresponding incoming-quality picture and audio in desktop OBS. The red PROGRAM border follows the actual OBS program scene, including cuts made with the mouse in OBS. The separate Broadcast and Master recording indicators show whether either output is running.

## First run with no cameras

Extract the entire folder onto the hub's internal SSD. Use a desktop session on Windows, Linux or macOS. The hub requires Python 3.10+, OBS Studio 30.2+ with its built-in WebSocket server, MediaMTX **v1.21.4**, and an FFmpeg build with libx264, AAC, RTMP, RTSP and the host's capture backend. From the FieldStream repository, its top-level `python setup_receiver.py` installs the matching MediaMTX binary for this launcher too. Download dependencies while online. Expose the extracted MediaMTX and FFmpeg executables on PATH or place them in a folder named `bin` next to `launch.py`. A macOS OBS install in /Applications and a standard Windows OBS install are detected automatically. Linux desktop instructions assume a native OBS installation.

1. Open a terminal in this folder. Create a virtual environment with `py -3 -m venv .venv` on Windows or `python3 -m venv .venv` on Linux/macOS. Use the following commands for the platform. Activating the environment is optional.
2. Open OBS once, finish its first-run dialog, and enable **Tools → WebSocket Server Settings → Enable WebSocket server**. Use port **4455**, keep authentication enabled and obtain the password. Leave OBS open. The launcher can start a configured OBS desktop on subsequent runs.
3. Join the hub and tablet to the field router's secured 5 GHz SSID. Reserve the hub's IPv4 address. The examples use **192.168.77.10/24**. Substitute the address and subnet actually assigned to the hub. For a laptop-only rehearsal, use `--ip 127.0.0.1 --network 127.0.0.0/8`.
4. Install the Python packages and launch the rehearsal. Enter the OBS password when prompted.

| Platform | Install once | Start the five-camera rehearsal |
|---|---|---|
| Windows PowerShell | `.\.venv\Scripts\python.exe -m pip install -r requirements.txt` | `.\.venv\Scripts\python.exe launch.py --ip 192.168.77.10 --configure-obs --demo` |
| Linux or macOS | `.venv/bin/python -m pip install -r requirements.txt` | `.venv/bin/python launch.py --ip 192.168.77.10 --configure-obs --demo` |

5. Open **http://192.168.77.10:8000** on the tablet. Wait for the moving test patterns, tap **Unlock switching**, and enter the operator key printed in the terminal. Tap each tile. Check the matching picture in OBS. Select another FieldStream scene inside OBS and watch the tally return to the tablet. Make a short master recording before attaching cameras.

`--configure-obs` provisions and selects a dedicated **FieldStream Dual Hub** profile and scene collection. It sets video, output parameters, managed sources and sync filters, fits each source to the canvas, and selects the first camera. Use it during setup with OBS outputs stopped. Normal starts omit that flag to preserve your encoder choices and current program scene. Setup also briefly reloads the profile so the first recording uses its selected muxer.

## Connect the cameras

These are the default routes. In apps with separate fields, use server `rtmp://192.168.77.10:1935/live` and the raw key shown below. In apps with one field, append a slash and the key. Each camera gets a different key.

| Camera | Hub connection | Raw key | OBS scene | Backup while streaming |
|---|---|---|---|---|
| Sony ZV-1F | USB streaming over a USB data cable to a USB 3 host port | cam_sony | FS / sony | Automatic hub recording at delivered quality. Sony disables camera recording during USB Streaming. |
| Primary GoPro | USB webcam device, if exposed by its model/driver | cam_gopro1 | FS / gopro1 | Use the hub recording. For SD backup on a compatible model, operate this camera via Quik RTMP and turn on SD saving. |
| DJI Osmo 360 | Mimo custom RTMP on field Wi-Fi | cam_osmo | FS / osmo | Select microSD storage and enable in-camera recording during the single-lens livestream. |
| GoPro 2 | Quik custom RTMP on field Wi-Fi | cam_gopro2 | FS / gopro2 | Enable the high-resolution SD-save option in Quik. |
| GoPro 3 | Quik custom RTMP on field Wi-Fi | cam_gopro3 | FS / gopro3 | Same setting, with its own stream key. |

Existing FieldStream camera presets using `live/dji`, `live/gopro1`, `live/gopro2` or `live/gopro3` also work. MediaMTX forwards them to the matching canonical input without re-encoding. Run one FieldStream launch mode at a time.

**Sony.** Choose Menu → Setup → USB → USB Connection Mode → USB Streaming. Set USB Power Supply to On, insert a charged battery, and connect using a powered data-capable port. The camera outputs 1280×720 MJPEG at 30 fps in NTSC or 25 fps in PAL, plus stereo 48 kHz PCM. Change the capture example to 25 for PAL. The camera connector operates at USB 2.0 speed even on a USB 3 host. A requirement for native SuperSpeed or simultaneous in-camera 4K recording cannot be met by this camera in this mode.

**Primary GoPro.** Identify the exact HERO model and firmware. For supported models the GoPro Webcam desktop utility exposes a virtual camera on Windows/macOS. Some models carry IP video over the USB cable and require a driver to expose a webcam. On Linux use an already working V4L2 device or a compatible USB-to-v4l2loopback driver. The kit consumes the resulting device. If class-compliant UVC on the wire is mandatory and the model only offers GoPro's proprietary transport, connect it via the allowed Quik RTMP path. In that case leave its USB capture disabled and change its link label in cameras.json to Wi-Fi. Camera audio availability varies by driver. The primary GoPro example captures video only.

**DJI Osmo 360.** Update camera firmware and Mimo, connect the camera to Mimo, enter Single Lens mode, select Livestream → RTMP, choose the field SSID and enter `rtmp://192.168.77.10:1935/live/cam_osmo`. Use 16:9 1080p30. Select the card as storage and enable the livestream recording option exposed by your firmware. Verify the card file after a short test. DJI documents recording the single-lens stream. An 8K panoramic backup during the livestream is outside that mode. DJI's setup instructions call for secured Wi-Fi with internet access. Camera video is addressed locally; test app setup and reconnection with the field router's WAN unplugged before relying on an offline site.

**Wireless GoPros.** In Quik, connect to the camera → Control Your GoPro → Live → Set Up Live → RTMP. Select the same SSID, enter the camera-specific URL, choose 1080p if offered, turn on saving a high-resolution copy to the SD card, then start the stream. Resolution, connection options, and the save control depend on model and firmware. Check the saved file's dimensions and frame rate. GoPro describes this as a high-resolution version of the stream, with no guarantee of a simultaneous 4K/5.3K archive. Set up each camera with a unique key and rehearse simultaneous operation, including reconnecting the controller phone.

Power wireless cameras from USB battery packs. Supply wired cameras from a powered data hub or powered host port. Verify charge trend, thermal shutdown behavior, card space and SSD capacity over a full-length rehearsal. An uninterrupted power source cannot enable a disabled camera recording mode.

## Select actual USB devices

Run `launch.py --devices` with your virtual-environment Python. Edit the matching platform entry for each USB camera in **cameras.json**. Set `capture.enabled` to `true` only after choosing the device. The default avoids accidentally recording a laptop webcam or microphone.

| Host | Device selection |
|---|---|
| Windows | Use exact DirectShow video and audio names from the listing. Sony's combined input is `video=VIDEO NAME:audio=AUDIO NAME`. To capture Sony video only, remove the audio suffix. Use the advertised GoPro virtual camera. |
| Linux | Select the Sony's persistent `/dev/v4l/by-id/...` video node and the actual ALSA input from `arecord -l`. Check format and frame rate with `v4l2-ctl -d DEVICE --list-formats-ext`. For video only remove the second `-f alsa -i ...` group. A virtual GoPro device often has a different node and supported mode. Grant the desktop user camera/audio device access. |
| macOS | Choose AVFoundation's separate video and audio indices in `VIDEO:AUDIO`; use `VIDEO:none` for video only. Grant camera/microphone access to the terminal launching FFmpeg. Recheck indices after reconnecting devices. |

By default FFmpeg encodes raw/MJPEG webcam frames once to H.264 CRF 18 at the negotiated capture dimensions/rate, and converts captured audio to AAC. H.264 USB devices that actually supply compressed H.264 can use the camera's matching input options and `"h264_copy": true` inside its capture object. That switch requires H.264 input. After the initial USB ingest encode, the HQ branch is packet-forwarded. Wireless H.264/AAC is packet-forwarded straight from the incoming RTMP session. Camera-original 4K is recoverable only if the camera itself supplies or separately records it.

Stop the rehearsal with Ctrl+C. Start real inputs using **the same launch command without `--demo`**. Once provisioned, also omit `--configure-obs`. To add or remove an angle, edit cameras.json (IDs are lowercase letters and digits), then run configuration again off-air. Disabled USB capture also allows the corresponding raw key to receive a wireless camera.

## OBS master and synchronization

| Setting | Provisioned value |
|---|---|
| Canvas / scaled output / frame rate | 1920×1080 / 1920×1080 / 60 fps |
| Streaming encoder | Simple output, H.264 x264, 6,000 kb/s video, 160 kb/s audio as a starting point |
| Master recording | High Quality profile, separate H.264 encode, Hybrid MP4 under `recordings/program/` |
| MKV alternative | Add `--record-format mkv --configure-obs` once. Convert an MKV for editing via OBS File → Remux Recordings. |
| Scene implementation | One persistent Media Source per camera, RTSP/TCP from localhost, fit inside the 1080 canvas, cut transition |
| Sync | Initial delay 0 ms per camera. Measured camera values are applied to source Render Delay filters and to that source's audio offset. |

Choose the actual broadcast service/server and account/key under **OBS Settings → Stream**. Only OBS sends the master to the destination. In Settings → General → Output, enable **Automatically record when streaming** to start the master with the broadcast. Alternatively, start recording for a local-only session. Match bitrate and keyframe interval to the chosen destination; if it requires a two-second keyframe interval, select it in Advanced output or set the encoder's equivalent to 120 frames at 60 fps. For a battery laptop, select its available H.264 NVENC, Quick Sync, AMF or Apple hardware encoder in the OBS output settings after provisioning. Confirm dropped/render-skipped frames in View → Stats while all previews, the broadcast, and recording are active. Lower-rate camera views retain their original motion cadence within the 60 fps output.

Camera audio in the selected scene follows that camera if present. The default primary GoPro USB example is silent. For continuous narration, choose one dedicated **Mic/Aux** in Settings → Audio and mute the camera inputs in OBS to avoid echo. Turn off unused desktop/mic devices. Set the audio sample rate to 48 kHz, and test lip sync on every scene.

**Measure the offsets on the HQ feeds.** Aim all cameras at one millisecond timer, open OBS View → Multiview, and capture all camera scenes together after warming up. For each sample subtract the oldest displayed time from each camera's time. The camera showing the newest time needs the largest extra delay. Example: if the Sony shows 12.900 s and the Osmo shows 12.250 s, delay Sony by 650 ms and leave Osmo at zero. Take several samples and use the median difference for each angle. Enter those values in the corresponding `delay_ms` fields and relaunch with `--configure-obs` off-air. The provisioner splits video delays into 500 ms stages. Repeat the timer check, record a clap, and adjust any independent mic in Advanced Audio Properties. High jitter or changing delay requires improving the wireless link and remeasuring. Multiple seconds of Render Delay can consume substantial GPU memory at 1080p. Preview tiles have their own transport delay and are unsuitable for this measurement.

## Media routes and network

| Purpose | Endpoint for the Sony example |
|---|---|
| Raw camera publish | `rtmp://HUB_IP:1935/live/cam_sony` |
| OBS HQ input, hub only | `rtsp://127.0.0.1:8554/live/cam_sony_hq` |
| Video-only proxy, hub only | `rtsp://127.0.0.1:8554/live/cam_sony_proxy` |
| Browser WHEP session | `POST /api/preview/sony` on the dashboard with an SDP offer. The backend forwards to `http://127.0.0.1:8889/live/cam_sony_proxy/whep`. |
| Per-camera backup | `recordings/live/cam_sony_hq/` (fragmented MP4, approximately five minutes per segment, one-second parts) |

The raw path starts one FFmpeg worker through MediaMTX's runOnReady hook. The native HQ relay and recorder operate independently of that worker. Each browser shares the same encoded proxy. Five cameras target about **2.5 Mb/s of preview video per tablet**, up to about **3.25 Mb/s** at the configured per-camera caps plus network overhead. Incoming Wi-Fi camera feeds retain their own bitrate. OBS reads HQ locally. A weak camera link can still limit the quality of the arriving signal.

Use a private field SSID with wireless client isolation off. Permit LAN TCP 1935 for RTMP, TCP 8000 for the dashboard, and UDP/TCP 8189 for WebRTC ICE to the hub's camera-LAN address. Keep router port forwarding off. The launcher binds UI and ICE to the selected IP. It keeps the MediaMTX API, RTSP and WHEP HTTP listeners on loopback; RTMP listens on both LAN and loopback with source IP and publish-path controls. Keep OBS WebSocket port 4455 blocked to the LAN using the host firewall. MediaMTX's internal RTSP hops never run over a camera cable or the field Wi-Fi.

Anyone on the trusted field LAN can monitor the grid. Program changes require the operator key. The HTTP control connection assumes trusted field Wi-Fi. Use `--cert fullchain.pem --key-file private-key.pem` for HTTPS on the same port; use a certificate the tablet trusts that matches the hub's address or DNS name. The browser handles receive-only WebRTC and retries connections. Trusted HTTPS also covers browsers or policies that restrict WebRTC on a plain LAN HTTP origin.

## Files and field checks

| File | Function |
|---|---|
| mediamtx.yml | Standalone receiver configuration for the example 192.168.77.0/24 LAN with runOnReady worker, native HQ relay and HQ recorder |
| launch.py | Cross-platform startup, device listing, readiness checks, USB/demo capture supervision and scoped shutdown |
| hub_controller.py | FastAPI, MediaMTX health checks, WHEP signaling and authenticated scene cuts |
| index.html | Locally served touch grid, WebRTC player, OBS tally and output status |
| obs_setup.py | Dedicated profile, sources, scene transforms, measured delay filters and master settings |
| cameras.json / settings.py / requirements.txt | Camera assignments, common routes and Python dependencies |

**Storage.** The default is a recordings folder next to the launcher. Set `FIELDSTREAM_RECORDINGS` to an SSD folder **before configuring OBS and starting the hub** to relocate both masters and camera backups. Camera backups start on HQ arrival irrespective of OBS status. Auto-deletion is disabled. At 6 Mb/s, each camera uses about 2.7 GB/hour plus audio and container overhead. Budget for the separately encoded program master. Stop OBS recording before stopping the launcher. Let Ctrl+C finish so open camera segments finalize.

**Operation.** Start MediaMTX, OBS and the dashboard with launch.py. When OBS already listens on 4455 it is reused. The launcher leaves OBS open on exit. Supplying `OBS_PASSWORD` through the local environment avoids the startup prompt; `FIELDSTREAM_KEY` can set a private persistent operator key. By default the key changes each launch. `--no-obs` suppresses desktop startup for receiver-only diagnostics. The launcher writes effective settings and process logs under `.runtime/`; inspect media.log, web.log and capture-<id>.log. A disconnected USB capture is retried automatically. Each Wi-Fi camera/app controls its own RTMP reconnection.

| Symptom | Check |
|---|---|
| Camera offline | Verify the raw key, camera-LAN IP, private subnet, app permissions and publisher connection. On USB, inspect the device name, supported mode, selected audio device and capture log. |
| HQ ready, blank preview | Check whether the proxy path is online in /api/status. See media.log for FFmpeg or ICE errors. Allow TCP/UDP 8189 to the selected IP, check client isolation, and try trusted HTTPS. Opening port 8000 alone only enables the dashboard and signaling. |
| Cut rejected | Re-enter the operator key from this run. Confirm the HQ path is ready, the configured scene exists, OBS is connected and the FieldStream collection is selected. A proxy failure alone does not block an HQ cut. |
| Red border on an offline camera | OBS still has that scene selected. Tap another healthy camera. Red indicates program selection, even when broadcasting is stopped. |
| Uneven cut or sync drift | Verify persistent inputs and measured HQ offsets, watch OBS Stats, and repeat the timing check after a wireless reconnect. |
| Card file missing | Inspect the card in the camera after ending the test. The dashboard cannot read an on-camera recording light or card status. |

**Field acceptance.** Rehearse on the actual router and tablet. Check moving video in every tile, cut each angle while recording, unplug and reconnect one camera, and verify a file from every HQ camera folder plus the program file. Check card copies independently. Test thermal and disk margin, a manual cut in OBS, audio, WAN loss, and the destination's received master before deployment.

**Browser self-test.** With all synthetic inputs running, install Playwright in the same environment (`python -m pip install playwright`) and use an installed Google Chrome browser. Run `python verify_previews.py --url http://192.168.77.10:8000`. It checks for decoded moving 480-line video from every camera and verifies that the grid fits a phone-sized viewport. The actual phone should also be checked on the field WLAN.

## References

- [MediaMTX 1.21.4 binaries](https://github.com/bluenviron/mediamtx/releases/tag/v1.21.4) · [WebRTC browser playback](https://mediamtx.org/docs/read/web-browsers) · [Control API](https://mediamtx.org/docs/features/control-api)
- [OBS Studio](https://obsproject.com/download) · [OBS WebSocket v5 requests](https://github.com/obsproject/obs-websocket/blob/master/docs/generated/protocol.md) · [Hybrid MP4](https://obsproject.com/kb/hybrid-mp4) · [FFmpeg](https://ffmpeg.org/download.html)
- [Sony ZV-1F USB streaming and recording limits](https://helpguide.sony.net/dc/2210/v1/en/contents/TP1000934575.html) · [Sony USB interface specification](https://www.sony.com/electronics/support/compact-cameras-dsc-zv-series/zv-1f/specifications)
- [DJI Osmo 360 streaming procedure](https://www.dji.com/360/faq) · [Livestream specifications](https://www.dji.com/support/product/360) · [Firmware release notes for in-camera recording](https://dl.djicdn.com/downloads/Osmo_360/20251215/DJI_Osmo_360_Release_Notes_en.pdf)
- [GoPro RTMP and SD saving](https://community.gopro.com/s/article/How-To-Live-Stream-From-Your-GoPro?language=en_US) · [GoPro Webcam](https://gopro.com/webcam) · [Community Linux driver](https://github.com/jschmid1/gopro_as_webcam_on_linux)
