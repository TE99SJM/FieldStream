# Field Hub control reference

The hub shows the composited program in OBS. A browser on the field Wi-Fi switches the same program and can request a still of the selected scene. The camera-control apps handle camera-specific settings. Pairing the touch panel needs the panel key only.

| Panel choice | Program |
| --- | --- |
| Five-camera grid | Sony and GoPro 1 on top, DJI, GoPro 2 and GoPro 3 below |
| Sony + DJI inset | Full-frame Sony with a DJI inset at lower right |
| Single camera | Full-frame Sony, DJI, or a selected GoPro |

A backdrop under each camera reads “Waiting for signal” when its tile is transparent or the watchdog hides it. The scene builder mutes the five newly created cameras. Choose a single always-active global microphone in OBS **Settings → Audio**, and check the mixer in each layout. Camera audio routed separately in Windows, desktop audio and additional global microphones may duplicate the program audio.

## Host and phone

On the initial build the designated Windows **Field Hub** scene collection gets seven scenes. The separate **Profile** holds your streaming service and encoder settings. Select each physical camera from the appropriate **Video Capture Device → Properties → Device**, then view both previews. Windows device identifiers vary by computer. The builder retains existing selections.

GoPro 1 defaults to a Windows capture source. The first build with `FIELDHUB_GOPRO1=wifi` creates a local network media source instead. Additional launches recognize its saved source type if the environment variable is unset. For a second transport preset, rename the current collection, e.g. **Field Hub USB backup**, then use the launcher with the new setting to create a fresh **Field Hub** collection. The receiver paths for the optional fourth wireless feed and the other cameras are listed in the main guide.

Open the field panel on a phone connected to the router. Use the pairing URL displayed on the hub or enter the panel key at `http://192.168.77.10:8088/`. The key is stored under `%LOCALAPPDATA%\ForgeFieldHub\panel-token.txt`. The initial URL keeps it in a fragment, then the browser removes the fragment and remembers the pairing for that tab. The panel uses HTTP inside the private field network. Keep port 8088 off the router WAN. The OBS WebSocket password stays on the laptop.

Tap a layout or camera once to switch. The highlighted button shows the program scene. **Snapshot** requests a single 640 × 360 program-scene still. **On air** indicates the OBS streaming output is active. Choose your final destination and start or stop broadcasting in OBS. For a live on-hub monitor, use **View → Multiview** in OBS (or the Multiview projector option in your OBS version). Keep the grid visible in Multiview between cuts so the GPU filters continue to render. Check a cold cut and the first seconds after a wireless reconnect during rehearsal.

## Camera timing

Use the calibration target and recording analyzer in the main guide. Measurements must come from the saved **Field - Grid** program recording. An OBS preview, a phone panel snapshot and a screen recording of the app use different timing paths. Capture all cameras looking at the same repeated flash. Compare the first changed frame at the center of each tile. Use constant recording and OBS frame rates, and warm all receivers before recording. Disable manually configured timing filters when establishing the baseline or keep them identical in all subsequent runs.

The following PowerShell commands run from the extracted kit using the same authenticated OBS environment as the launcher. For a separate terminal, read the OBS password privately into the process first.

```powershell
$env:FIELDHUB_OBS_PASSWORD = [System.Net.NetworkCredential]::new('', (Read-Host 'OBS WebSocket password' -AsSecureString)).Password
.\.venv\Scripts\python.exe control\scenes.py clear-sync
```

The clear command removes the kit's existing Render Delay filters. Record a fresh baseline **after** clearing. The command names below are references for calibration or diagnosis.

| Command after `control\scenes.py` | Effect |
| --- | --- |
| `build` | Creates or repairs kit layouts and camera-source bindings in the current Field Hub collection. |
| `devices` | Lists the available Windows capture devices and current matches. |
| `clear-sync` | Removes kit-owned GPU delay filters from the five named inputs. |
| `calibrate --measured-fps 30 --frames sony=120,dji=180 --dry-run` | Prints a **hypothetical** 60-frame delay for Sony. Replace both numbers with frames from the same flash in your recording. |
| `calibrate --measured-fps 30 --frames sony=120,dji=180` | Applies the measured adjustment to the listed sources. Supply additional `--frames` arguments for other flashes with the same participating cameras. |
| `panel.py --show-url` (invoke as a separate script) | Displays the pairing URL for the current user and active environment. |

The tool calculates frame differences against a common reference for each flash and uses the median for each camera. It delays early inputs to match the latest median arrival. For 30 fps, one frame is 33.33 ms. Render Delay has a 500 ms per-filter limit. OBS converts each setting to a whole number of frame textures. In the implementation cited below, it draws the oldest remaining texture after inserting the new one, yielding one fewer elapsed frame interval than the buffer length. The field tool accounts for this separately per stage. The sum of displayed filter settings can therefore exceed the requested end-to-end delay. Repeat the recording to confirm the result on the installed OBS release. The script retains calibration for camera IDs omitted from an apply command.

Keep a common microphone close to the clapper used for audio calibration. In the corrected test recording, measure the gap between the audio spike and the aligned clapper closure. For example, if the sound leads closure by 45 frames at 30 fps, enter **+1500 ms** under the microphone’s **Advanced Audio Properties → Sync Offset**. Re-record and confirm. If sound already lags closure, reevaluate microphone buffering or increase the common video delay before applying a positive audio offset.

Accept a static offset only after multiple fresh flashes land within one program frame across all intended angles. Repeat after a reconnect, a bitrate change and a sustained run. Variable spread means a fixed delay cannot guarantee frame lock. Several seconds of full-resolution RGB render buffering across many cameras also uses GPU memory. Check OBS Stats for rendering lag and encoding overload with all five feeds active.

## Implementation references

- [OBS WebSocket v5 requests](https://github.com/obsproject/obs-websocket/blob/master/docs/generated/protocol.md)
- [OBS Render Delay documentation](https://obsproject.com/kb/render-delay-filter/)
- [OBS Render Delay implementation](https://github.com/obsproject/obs-studio/blob/master/plugins/obs-filters/gpu-delay.c)
- [OBS FFmpeg media source](https://github.com/obsproject/obs-studio/blob/master/plugins/obs-ffmpeg/obs-ffmpeg-source.c)
- [OBS Windows DirectShow input](https://github.com/obsproject/obs-studio/blob/master/plugins/win-dshow/win-dshow.cpp)
