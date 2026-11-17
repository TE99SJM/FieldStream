# Field camera network

MediaMTX receives a separate local stream from each wireless camera. Only OBS receives the public broadcast URL and stream key. The browser control phone can stay on the camera network. Install, activate, sign in to the camera apps and update firmware at home before testing offline startup.

## Router and uplink

Use the GL.iNet Beryl AX (GL-MT3000) in **Router** mode. Connect its LAN port to the Windows hub over Ethernet so each camera feed takes one Wi-Fi hop. An Ethernet dongle may share the hub’s USB bus. The cameras themselves transmit over Wi-Fi. Power the router at **5 V / 3 A**. Its published consumption is under 8 W. Budget power delivered to any USB-tethered phone separately. [Router guide](https://docs.gl-inet.com/router/en/4/user_guide/gl-mt3000/) · [Router specifications](https://www.gl-inet.com/products/gl-mt3000)

| Setting | Field value |
|---|---|
| Network → LAN → Main Network | Router address `192.168.77.1`, mask `255.255.255.0`, DHCP enabled, range `192.168.77.100`–`192.168.77.199` |
| Windows field Ethernet | Manual IPv4 `192.168.77.10`, prefix 24. Set gateway and DNS according to uplink choice below |
| Wireless → 5 GHz → Main | SSID `Forge-Cameras`, WPA2-Personal/AES, unique passphrase, AP/client isolation off. Start with channel 36 at 40 MHz in the US |
| Controls and cameras | Join the main SSID. Automatic client addresses are fine. The cameras always publish to the fixed Windows address |
| Physical placement | Keep the router elevated with clear sight to camera positions. If a camera cannot see the network, check its 5 GHz setting, try another supported non-DFS channel, then a separate 2.4 GHz SSID on the same LAN as a fallback |

The router starts on its factory address `192.168.8.1`. After changing the LAN, reconnect at `192.168.77.1`. Keep port forwarding and UPnP off. Use a Windows firewall rule scoped to local address `192.168.77.10`, remote subnet `192.168.77.0/24` and TCP port 1935 on the field adapter for camera inputs. Playback and management bind to Windows loopback. [LAN settings](https://docs.gl-inet.com/router/en/4/interface_guide/lan/)

Choose the row that matches the physical uplink. In both cases the private RTMP path remains on the local subnet.

| Uplink | Windows Ethernet settings | Field check |
|---|---|---|
| Router joins a separate phone hotspot as WAN (default) | Set Ethernet gateway and DNS to `192.168.77.1`. On the router, choose Internet → Repeater and join the phone's 2.4 GHz hotspot. Keep the camera SSID on 5 GHz. A separately budgeted USB phone tether can use Internet → Tethering | Give the router an upstream only while needed. Camera apps may use some control or validation data. With OBS stopped, measure WAN upload once idle and again with all cameras publishing. The WAN should stay near baseline while LAN ingress rises. A WAN upload increase comparable to one camera's bitrate warrants checking camera destinations |
| Hub uses its own cellular modem or connects by Wi-Fi to a separate phone hotspot | Leave Ethernet gateway and DNS empty. The WAN adapter supplies the default route. Leave router WAN disconnected. Disable Windows Internet Connection Sharing and bridging on the field adapter | Open `route print -4`. The camera /24 route uses Ethernet. The internet default route uses the WAN adapter. Check a DJI cold start on this topology before relying on it |

## Camera destinations

| Camera | App destination | OBS and watchdog playback on the hub |
|---|---|---|
| Osmo 360 | `rtmp://192.168.77.10/live/dji` | `rtsp://127.0.0.1:8554/live/dji` |
| GoPro 1, when assigned wireless | `rtmp://192.168.77.10/live/gopro1` | `rtsp://127.0.0.1:8554/live/gopro1` |
| GoPro 2 | `rtmp://192.168.77.10/live/gopro2` | `rtsp://127.0.0.1:8554/live/gopro2` |
| GoPro 3 | `rtmp://192.168.77.10/live/gopro3` | `rtsp://127.0.0.1:8554/live/gopro3` |

If an app displays separate server and stream key fields, enter `rtmp://192.168.77.10/live` as server and the last component, such as `gopro2`, as key. TCP 1935 is the default. All four paths can publish concurrently. An already active path rejects a second publisher until the first disconnects or times out.

**DJI Mimo.** Osmo 360 firmware V01.03.08.70 and Mimo 2.6.8 introduced livestreaming. Update both, pair the phone, open the camera preview, select Single Lens → Livestream → RTMP, select the secured field Wi-Fi and enter the DJI URL above. The supported output is 1920 × 1080 at 30 fps from one lens. Front/rear switching is available during a stream. Reserve 6 Mbps as an initial planning figure, then measure actual ingress. DJI’s general bitrate table omits Osmo 360. Use the bitrate choices actually offered on the camera/app. [DJI release notes](https://dl.djicdn.com/downloads/Osmo_360/20260224/DJI_Osmo_360_Release_Notes_en.pdf) · [Osmo 360 specs](https://www.dji.com/360/specs) · [Mimo streaming guide](https://support.dji.com/help/content?customId=en-us03400006728&lang=en&re=US&spaceId=34)

**DJI offline check.** DJI's model-specific guide calls for secured Wi-Fi with internet access during setup. With the receiver running, test starting from a powered-off camera with the field router WAN disconnected. Check for an actual moving image in OBS. If setup refuses, use the router-uplink arrangement above while keeping the RTMP URL private. Test what happens when the WAN drops during a stream and when the camera needs a fresh start. Record both results for your firmware. A successful warm stream alone does not qualify an offline restart. [DJI FAQ](https://www.dji.com/360/faq)

**GoPro Quik.** Choose one compatible camera → Control Your GoPro → Live → Set Up Live → RTMP. Select the field SSID, paste that camera's URL, choose the offered resolution, then select Set Up Live Stream → Go Live. Confirm motion in its OBS tile before configuring the next camera. Use another phone for additional setup if changing camera connections with the first phone interrupts a stream. Quik starts the camera’s own Wi-Fi station-mode publisher. If the camera stops publishing after an interruption, resume or repeat Go Live. The hub can accept a returning stream regardless of whether that camera retries automatically. Pairing, available resolutions and stream setup depend on model. GoPro lists HERO7 Black and newer with exceptions including MAX2, LIT HERO and HERO (2024). Check the exact model. HERO7 Black streams up to 720p. [Quik instructions and supported models](https://community.gopro.com/s/article/How-To-Live-Stream-From-Your-GoPro?language=en_US) · [GoPro streaming guide](https://gopro.com/en/us/news/how-to-live-stream-on-gopro)

## Measuring data usage

Allow 4–6 Mbps per camera for initial planning. GoPro’s documented wireless RTMP range reaches 8 Mbps and Quik automatically adapts its bitrate. Begin at 720p for GoPros if the grid tiles are small or the radio is crowded. Model-supported Open GoPro controls expose desired starting/minimum/maximum values, and the camera may override them. A router throttle can create RTMP queues and delay. Choose the lowest recording quality that meets the shot, then check the measured result. [Open GoPro streaming specifications](https://gopro.github.io/OpenGoPro/docs/faq/) · [Bitrate controls](https://gopro.github.io/OpenGoPro/docs/ble/live_streaming/)

When a camera exposes manual controls, start at **4,500 kbps** and request a **6,000 kbps** maximum. On compatible Open GoPro integrations, the corresponding Set Live Stream Mode fields are `starting_bitrate=4500`, `minimum_bitrate=2000` and `maximum_bitrate=6000` in kbps. The vendor documents the limits as desired values. Check the resulting data rate below. An Osmo 360 rate selector, if present, depends on Mimo and firmware. [GoPro protobuf definitions](https://gopro.github.io/OpenGoPro/docs/ble/protocol/protobuf/)

For DJI plus two wireless GoPros, the example reservation is 6 + 8 + 8 = **22 Mbps**. A third wireless GoPro raises it to **30 Mbps**. As a field acceptance target, measure at least twice the planned camera payload from the furthest camera position to the wired hub. Budget OBS's internet upload separately using the platform's selected output bitrate, audio, overhead and coverage margin. Measure cellular service on site with a short private broadcast.

To see current per-camera ingress, run this on the hub in PowerShell while all cameras are live. It averages media bytes across ten seconds. If a publisher restarted during the sample, take a fresh sample.

```powershell
$pathApi = 'http://127.0.0.1:9997/v3/paths/list'
$baseline = @{}
foreach ($path in (Invoke-RestMethod $pathApi).items) {
    if ($path.online) { $baseline[$path.name] = [int64]$path.inboundBytes }
}
$timer = [Diagnostics.Stopwatch]::StartNew()
Start-Sleep -Seconds 10
$current = (Invoke-RestMethod $pathApi).items
$timer.Stop()
foreach ($path in $current) {
    if ($path.online -and $baseline.ContainsKey($path.name)) {
        $bytes = [int64]$path.inboundBytes - $baseline[$path.name]
        if ($bytes -ge 0) {
            '{0,-14} {1,6:N2} Mbps' -f $path.name, ($bytes * 8.0 / $timer.Elapsed.TotalSeconds / 1e6)
        }
    }
}
```

[MediaMTX Control API](https://mediamtx.org/docs/features/control-api) · [MediaMTX metrics](https://mediamtx.org/docs/features/metrics)
