# openamika-iot — smart-home MCP server

Zigbee smart-home control for OpenAmika over **mosquitto + zigbee2mqtt**. The MQTT core (dynamic
device registry, nickname resolution, 3-second confirm-on-command, 45-second watchdog reconnect) is
ported near-verbatim from the proven Amika_IoT bridge — with the REST/OpenClaw layer replaced by MCP.

## Stack

```
Zigbee devices ── Zigbee USB dongle ── zigbee2mqtt ── mosquitto (:1883) ── this server ── OpenAmika
```

1. **mosquitto** — install from mosquitto.org; run with the 2-line `mosquitto.conf` here
   (`mosquitto -c mosquitto.conf`). LAN-only by default; add auth if the broker leaves localhost.
2. **zigbee2mqtt** — install per zigbee2mqtt.io with your dongle's serial port;
   `base_topic: zigbee2mqtt`, `homeassistant: enabled: false`. Pair devices from its own dashboard
   (pairing is deliberately NOT a model-callable tool).
3. **this server** — `pip install -e .` (or `pip install mcp paho-mqtt`), then it's spawned by
   OpenAmika's `config/tools.json`:

```jsonc
"iot": {
  "command": "python", "args": ["-m", "openamika_iot"],
  "cwd": "../servers/openamika-iot",
  "env": { "AMIKA_MQTT_HOST": "127.0.0.1", "AMIKA_MQTT_PORT": "1883" },
  "enabled": true,
  "tools": { "list_devices": "auto", "get_state": "auto", "health": "auto",
             "set_state": "confirm", "set_nickname": "confirm", "activate_scene": "confirm" }
}
```

Dongle on another machine? Run `python -m openamika_iot --http --host 0.0.0.0 --port 8010` there and
register the URL instead.

## Tools

| Tool | Policy default | Notes |
|---|---|---|
| `list_devices` | auto | names, nicknames, state, availability, power metrics |
| `get_state` | auto | one device (nickname or friendly name, case-insensitive) |
| `health` | auto | broker + z2m status; **self-heals** a stale connection before reporting |
| `set_state` | confirm | on/off/toggle; waits 3 s for the device's own confirmation |
| `set_nickname` | confirm | persisted to `devices.json` |
| `activate_scene` | confirm | from `scenes.json` (copy `scenes.example.json`) |

Alternative: if you already run Home Assistant, skip all of this and register HA's official MCP
Server URL in OpenAmika instead — same Tools page, zero code.
