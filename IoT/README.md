# IoT — her hands in the room (Zigbee smart plugs)

```
plugs ── SONOFF Zigbee Dongle Plus MG24 (COM port) ── zigbee2mqtt ── mosquitto (:1883) ── servers/openamika-iot ── brain
```

Everything here is started by `Start_OpenAmika.bat` (settings.json → `services.mqtt`, `services.zigbee2mqtt`); the
smart-home MCP server itself is `servers/openamika-iot`, spawned by `config/tools.json` (`iot`). The launcher
finds the dongle's COM port on its own and hands it to zigbee2mqtt (`ZIGBEE2MQTT_CONFIG_SERIAL_PORT`).

| Piece | Where |
|---|---|
| zigbee2mqtt, installed by you ([Windows guide](https://www.zigbee2mqtt.io/guide/installation/03_windows.html); tested with 2.9.1 built with corepack pnpm) | `zigbee2mqtt/` (git-ignored) |
| its data: `configuration.yaml`, network key, coordinator backup, paired devices; private, never commit it | `data/` (git-ignored; starter config below) |
| broker config (local only, anonymous) | `mosquitto.conf` |
| one-time admin installs: CP210x driver (via Windows Update), Mosquitto (winget) | `Setup_IoT_admin.bat` → `Install_CP210x_driver.ps1`, `Setup_IoT_admin.ps1` |
| Windows fix for the dongle (see below) | `patch_herdsman_windows.py` — re-run after reinstalling zigbee2mqtt |
| nicknames she uses for the plugs | `../servers/openamika-iot/devices.json` (created on first use, git-ignored; format: `devices.example.json`) |
| zigbee2mqtt dashboard | http://localhost:8080 (pairing/renaming happens there, never through her) |

## The dongle on Windows (why `patch_herdsman_windows.py` exists)

Node's serialport opens a COM port with DTR asserted and RTS *disabled*. On the MG24 dongle the CP210x's DTR/RTS
pins drive the EFR32's reset/boot lines, and exactly that state boots the dongle into its **Gecko bootloader** —
zigbee2mqtt then waits forever for an ASH `RSTACK` ("Failed to start EZSP layer with status=HOST_FATAL_ERROR").
Probed on COM3 (2026-10-24): DTR high + RTS low → bootloader menu; DTR low + RTS low → `RSTACK`. The patch adds
`hupcl: false` to the ember adapter's serial options so DTR stays released. `rtscts` must stay `false` (the
firmware uses XON/XOFF; with RTS/CTS the host waits on CTS and nothing is ever sent).

A starter `data/configuration.yaml` (zigbee2mqtt 2.x; the launcher fills in the COM port, and `GENERATE` makes
zigbee2mqtt create your own network keys on its first start):

```yaml
homeassistant:
  enabled: false
mqtt:
  base_topic: zigbee2mqtt
  server: mqtt://localhost:1883
serial:
  adapter: ember
  rtscts: false
frontend:
  enabled: true
  port: 8080
advanced:
  log_level: info
  network_key: GENERATE
  pan_id: GENERATE
  ext_pan_id: GENERATE
version: 5
```

Then set `services.mqtt.enabled` and `services.zigbee2mqtt.enabled` to `true` in settings.json and enable the `iot`
server on the Tools page (or in `config/tools.json`, from `config/tools.example.json`).

If zigbee2mqtt ever reports the dongle missing: Device Manager must show "Silicon Labs CP210x USB to UART Bridge
(COMx)"; if it shows the dongle with an error (code 28), run `Setup_IoT_admin.bat`.
