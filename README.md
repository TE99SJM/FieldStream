# FieldStream

One battery-powered hub receives the Sony ZV-1F and primary GoPro over USB and DJI Osmo 360 and additional GoPros over private field Wi-Fi. OBS on the hub records the composite and owns the outbound stream key.

## First setup

Use Python 3.11 or 3.12 on the hub and run `python setup_receiver.py` from this folder while online (use `python3` on Linux/macOS). This installs MediaMTX v1.21.4 for the host and checks it against the original handoff binary. Install native OBS Studio and the dependencies in your chosen guide before going offline.

Choose **one** launch mode per session. Each mode manages its own receiver, scenes and monitoring. Stop it before changing modes.

| Mode | Platforms | Start here |
| --- | --- | --- |
| Five-camera grid, PiP and automatic standby slates | Windows x64, Linux and macOS x64/arm64 | [OBS setup and launcher guide](obs/Field_Stream_Guide.html) |
| Five-camera portable rig with phone panel and sync target | Windows x64 | [Portable hub guide](Portable_OBS_Field_Hub/OPEN-FIRST.html) |
| Python capture, touch dashboard and measured sync offsets | Windows x64 | [Mobile Camera Hub setup](MobileCameraHub/README.md) |

Open the HTML guides locally after cloning. Their platform kit directories correspond to the folders above. The Python profile ships with four cameras. Its USB capture uses DirectShow. Linux primary GoPro USB support depends on the model and uses the USB network interface documented in the OBS guide.

## Field network

Use router 192.168.77.1/24 and reserve 192.168.77.10 for the hub. Join cameras and controllers to the private field LAN. Send wireless feeds to `rtmp://192.168.77.10:1935/live` with keys `dji`, `gopro2`, or `gopro3` as enabled in the profile. The five-camera profiles also support `gopro1` when switching the primary GoPro to Wi-Fi. Use USB webcam capture for the Sony. Scope inbound firewall rules to the field adapter and subnet. Leave port forwarding off. Configure the platform stream key only in OBS.

The [field networking and power design](docs/Field_Streaming_Guide.pdf) covers the Beryl AX, cellular uplinks, recording and power. Its original address examples use 192.168.42.x and keys osmo/gp1/gp2/gp3. Substitute the addresses and keys above throughout that document. Its [standalone receiver config](docs/mediamtx.yml) uses the integrated addresses. The launch modes use their own receiver settings for loopback playback and health checks.

## Field acceptance

Connect each physical camera and identify its USB device or local RTMP publisher. Check unplug/reconnect, grid/PiP and missing-feed behavior, flash/clap sync in a recorded clip, and whether Mimo and Quik can start from a cold offline LAN. Disconnect the uplink while recording, then verify OBS recovery on the backup uplink. Measure battery runtime and thermals with all devices active.
