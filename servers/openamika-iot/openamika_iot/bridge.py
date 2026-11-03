"""MQTT ↔ zigbee2mqtt bridge core for the openamika-iot MCP server.

Ported near-verbatim from the legacy Amika_IoT bridge (bridge/server.py), minus the
REST/HTML layer. What's kept, because it's the hard-won part:
  * dynamic device registry built from retained `bridge/devices`, join/announce events,
    per-device state + availability topics (state/power/voltage/current/energy/temp);
  * nickname resolution (case-insensitive) with atomic persistence to devices.json;
  * confirm-on-command: every set waits up to 3 s for the device's own MQTT state echo
    and reports ok/already/offline/no-response distinctly;
  * a watchdog thread that force-reconnects MQTT after 45 s of silence (laptop
    sleep/wake recovery).

Config via environment:
  AMIKA_MQTT_HOST (default 127.0.0.1), AMIKA_MQTT_PORT (1883),
  AMIKA_MQTT_BASE_TOPIC (zigbee2mqtt),
  AMIKA_IOT_DATA_DIR (where devices.json + scenes.json live; default: package folder)
"""
from __future__ import annotations

import difflib
import json
import os
import re
import threading
import time
from pathlib import Path

import paho.mqtt.client as mqtt

MQTT_BROKER = os.getenv("AMIKA_MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.getenv("AMIKA_MQTT_PORT", "1883"))
BASE_TOPIC = os.getenv("AMIKA_MQTT_BASE_TOPIC", "zigbee2mqtt")
DATA_DIR = Path(os.getenv("AMIKA_IOT_DATA_DIR", str(Path(__file__).resolve().parent.parent)))
DEVICES_PATH = DATA_DIR / "devices.json"
SCENES_PATH = DATA_DIR / "scenes.json"

CONFIRM_TIMEOUT = 3.0
WATCHDOG_INTERVAL = 15
MQTT_STALE_THRESHOLD = 45

IGNORED_TOPICS = {"bridge/logging", "bridge/response/permit_join",
                  "bridge/extensions", "bridge/groups", "bridge/definitions"}


class DeviceNotFound(ValueError):
    pass


class Bridge:
    def __init__(self):
        self.devices: dict = {}
        self._lock = threading.Lock()
        self._client = None
        self._connected = threading.Event()
        self.z2m_online = False
        self._state_events: dict = {}
        self._last_rx = time.time()
        self._watchdog = None
        self._started = False
        # discovery: zigbee2mqtt's pairing switch (bridge/info) and its last join/interview events (bridge/event),
        # for the Smart home panel ("searching… found 0x…, interviewing… ready")
        self.permit_join = False
        self.permit_join_end = None
        self.events: list = []

    # ── persistence ──
    def _load_devices(self):
        if DEVICES_PATH.exists():
            try:
                saved = json.loads(DEVICES_PATH.read_text(encoding="utf-8"))
            except Exception:
                return
            with self._lock:
                for fname, info in saved.items():
                    self.devices.setdefault(fname, {"state": "unknown"}).update(info)

    def _save_devices(self):
        with self._lock:
            persistable = {f: {"nickname": i.get("nickname", ""), "note": i.get("note", "")} for f, i in self.devices.items()}
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = DEVICES_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(persistable, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(DEVICES_PATH)

    def scenes(self) -> dict:
        if SCENES_PATH.exists():
            try:
                doc = json.loads(SCENES_PATH.read_text(encoding="utf-8-sig"))
                if isinstance(doc, dict):
                    return {k: v for k, v in doc.items() if isinstance(v, dict)}
            except Exception:
                pass
        return {}

    # ── registry ──
    def _ensure(self, fname, ieee=""):
        with self._lock:
            if fname not in self.devices:
                self.devices[fname] = {"state": "unknown", "nickname": "", "note": "", "ieee": ieee}
            elif ieee and not self.devices[fname].get("ieee"):
                self.devices[fname]["ieee"] = ieee
            return self.devices[fname]

    # words that say nothing about which device is meant
    _FILLER = {"the", "a", "an", "my", "in", "on", "of", "at", "by", "to", "and", "or", "please", "turn", "switch",
               "off", "on", "that", "this", "it", "one", "room", "thing", "device", "plug", "smart", "with", "is"}

    _SYNONYMS = {"light": "lamp", "bulb": "lamp", "lamps": "lamp", "lights": "lamp"}

    def _tokens(self, text: str) -> set:
        words = re.findall(r"[a-z0-9]+|[぀-ヿ一-鿿]+", (text or "").lower())
        words = [self._SYNONYMS.get(w, w) for w in words if w not in self._FILLER]
        return {w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words}

    def label(self, fname: str) -> str:
        """How a device is named in feedback: its nickname, else its zigbee2mqtt name."""
        with self._lock:
            return (self.devices.get(fname, {}).get("nickname") or "").strip() or fname

    def directory(self) -> str:
        """One line naming every device (nickname or name, plus its note) — what not-found feedback lists."""
        with self._lock:
            items = [((i.get("nickname") or "").strip() or f) + (f" ({i['note'].strip()})" if (i.get("note") or "").strip() else "")
                     for f, i in self.devices.items()]
        return "; ".join(items) or "(no devices)"

    def resolve(self, name: str) -> str:
        """The device meant by `name`: an exact zigbee2mqtt name or nickname first, then a fuzzy match on the words of
        its nickname, its note ("a lamp sitting in the corner of the room") and its name — so "lamp in the corner" or
        "the corner lamp" finds Reading Lamp. A tie or no match raises DeviceNotFound naming the candidates."""
        query = (name or "").strip()
        with self._lock:
            devices = {f: dict(i) for f, i in self.devices.items()}
        if query in devices:
            return query
        lowered = query.lower()
        for fname, info in devices.items():
            if (info.get("nickname") or "").strip().lower() == lowered and lowered:
                return fname
        wanted = self._tokens(query)
        scored = []
        for fname, info in devices.items():
            nick = (info.get("nickname") or "").strip()
            words = self._tokens(" ".join([nick, info.get("note") or "", fname]))
            overlap = len(wanted & words)
            # near-identical spelling of the nickname ("readng lamp") counts as a hit; loose similarity does not,
            # or "lamp" would quietly pick one of two lamps instead of asking which
            ratio = difflib.SequenceMatcher(None, lowered, nick.lower()).ratio() if nick else 0.0
            score = overlap + (ratio if ratio >= 0.8 else 0.0)
            if score > 0:
                scored.append((score, fname))
        scored.sort(reverse=True)
        if scored and (len(scored) == 1 or scored[0][0] > scored[1][0]):
            return scored[0][1]
        if scored:
            tied = [self.label(f) for s, f in scored if s == scored[0][0]]
            raise DeviceNotFound(f"'{query}' matches several devices: {', '.join(tied)}. Ask which one.")
        raise DeviceNotFound(f"no device matches '{query}'. Devices: {self.directory()}")

    def _event_for(self, fname):
        if fname not in self._state_events:
            self._state_events[fname] = threading.Event()
        return self._state_events[fname]

    # ── MQTT callbacks ──
    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            self._connected.set()
            self._last_rx = time.time()
            client.subscribe(f"{BASE_TOPIC}/#")

    def _on_disconnect(self, client, userdata, flags, rc, properties=None):
        self._connected.clear()
        self.z2m_online = False

    def _on_message(self, client, userdata, msg):
        self._last_rx = time.time()
        topic = msg.topic
        if not topic.startswith(BASE_TOPIC + "/"):
            return
        subtopic = topic[len(BASE_TOPIC) + 1:]
        try:
            payload = json.loads(msg.payload.decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            try:
                payload = msg.payload.decode()
            except UnicodeDecodeError:
                return

        if subtopic == "bridge/devices" and isinstance(payload, list):
            unknown = []
            for dev in payload:
                if dev.get("type") == "Coordinator":
                    continue
                fname = dev.get("friendly_name", "")
                if fname:
                    entry = self._ensure(fname, dev.get("ieee_address", ""))
                    definition = dev.get("definition") if isinstance(dev.get("definition"), dict) else {}
                    with self._lock:
                        entry["model"] = definition.get("model") or dev.get("model_id") or entry.get("model", "")
                        entry["vendor"] = definition.get("vendor") or dev.get("manufacturer") or entry.get("vendor", "")
                        entry["description"] = definition.get("description") or entry.get("description", "")
                        entry["supported"] = bool(dev.get("supported", True))
                        entry["interview_completed"] = bool(dev.get("interview_completed", True))
                        if entry.get("state", "unknown") == "unknown":
                            unknown.append(fname)
            # zigbee2mqtt does not retain device states: after a (re)start we would only learn a plug's state at its
            # next report. Ask each unknown one now (a read; nothing switches) so the panel and she see it at once.
            for fname in unknown:
                client.publish(f"{BASE_TOPIC}/{fname}/get", json.dumps({"state": ""}))
            return
        if subtopic == "bridge/info" and isinstance(payload, dict):
            self.permit_join = bool(payload.get("permit_join"))
            end = payload.get("permit_join_end")
            self.permit_join_end = (end / 1000.0 if isinstance(end, (int, float)) and end > 1e11 else end) if self.permit_join else None
            return
        if subtopic == "bridge/event" and isinstance(payload, dict):
            etype, data = payload.get("type", ""), payload.get("data", {})
            if etype == "device_joined" and data.get("friendly_name"):
                self._ensure(data["friendly_name"], data.get("ieee_address", ""))
                self._save_devices()
            if etype == "device_announce" and data.get("friendly_name"):
                self._ensure(data["friendly_name"])
            if etype in ("device_joined", "device_interview", "device_announce", "device_leave"):
                event = {"at": time.time(), "type": etype, "device": data.get("friendly_name") or data.get("ieee_address", ""),
                         "status": data.get("status", "")}
                if etype == "device_interview" and isinstance(data.get("definition"), dict):
                    event["description"] = data["definition"].get("description", "")
                with self._lock:
                    self.events.append(event)
                    del self.events[:-30]
            return
        if subtopic == "bridge/state":
            if isinstance(payload, dict):
                self.z2m_online = payload.get("state") == "online"
            elif isinstance(payload, str):
                self.z2m_online = payload == "online"
            return
        if subtopic.startswith("bridge/"):
            return
        if subtopic.endswith("/availability"):
            fname = subtopic.rsplit("/availability", 1)[0]
            avail = payload.get("state", payload) if isinstance(payload, dict) else str(payload)
            self._ensure(fname)["availability"] = avail
            return
        parts = subtopic.split("/")
        if len(parts) == 1:
            fname = parts[0]
            entry = self._ensure(fname)
            if isinstance(payload, dict):
                entry["state"] = payload.get("state", entry.get("state", "unknown"))
                for key in ("power", "voltage", "current", "energy", "temperature"):
                    if key in payload:
                        entry[key] = payload[key]
                entry["last_seen"] = time.time()
            elif isinstance(payload, str) and payload in ("ON", "OFF"):
                entry["state"] = payload
            evt = self._state_events.get(fname)
            if evt:
                evt.set()

    # ── lifecycle ──
    def _new_client(self):
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.reconnect_delay_set(min_delay=1, max_delay=10)
        client.connect_async(MQTT_BROKER, MQTT_PORT, keepalive=30)
        client.loop_start()
        return client

    def force_reconnect(self):
        self._connected.clear()
        if self._client:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:
                pass
        self._client = self._new_client()
        self._last_rx = time.time()

    def _watchdog_loop(self):
        while True:
            time.sleep(WATCHDOG_INTERVAL)
            if time.time() - self._last_rx > MQTT_STALE_THRESHOLD:
                self.force_reconnect()

    def start(self):
        if self._started:
            return
        self._started = True
        self._load_devices()
        self._client = self._new_client()
        self._watchdog = threading.Thread(target=self._watchdog_loop, daemon=True)
        self._watchdog.start()

    # ── operations (all return plain dicts) ──
    def health(self, self_heal=True) -> dict:
        age = time.time() - self._last_rx
        stale = age > MQTT_STALE_THRESHOLD
        if self_heal and (stale or not self._connected.is_set()):
            self.force_reconnect()
            time.sleep(2)
            age = time.time() - self._last_rx
            stale = age > MQTT_STALE_THRESHOLD
        connected = self._connected.is_set()
        return {"status": "ok" if (connected and self.z2m_online and not stale) else "degraded",
                "mqtt_connected": connected, "zigbee2mqtt_online": self.z2m_online,
                "mqtt_last_message_ago_s": round(age, 1), "devices_tracked": len(self.devices)}

    def list_devices(self) -> dict:
        with self._lock:
            return {f: {"nickname": i.get("nickname", ""), "note": i.get("note", ""), "state": i.get("state", "unknown"),
                        "availability": i.get("availability", "unknown"),
                        "power": i.get("power"), "voltage": i.get("voltage"),
                        "current": i.get("current"), "energy": i.get("energy"),
                        "temperature": i.get("temperature"),
                        "model": i.get("model", ""), "vendor": i.get("vendor", ""), "description": i.get("description", ""),
                        "ieee": i.get("ieee", ""), "last_seen": i.get("last_seen"),
                        "supported": i.get("supported", True), "interview_completed": i.get("interview_completed", True)}
                    for f, i in self.devices.items()}

    def set_note(self, name: str, note: str) -> dict:
        """What this device is for, in the person's words ("the lamp by the bed") — persisted, shown to her with
        the device so she knows what she is switching."""
        fname = self.resolve(name)
        note = (note or "").strip()[:300]
        with self._lock:
            self.devices[fname]["note"] = note
        self._save_devices()
        return {"device": fname, "note": note, "ok": True}

    def set_permit_join(self, seconds: int) -> dict:
        """Open (seconds > 0) or close (0) zigbee2mqtt's pairing window; the UI's "find new devices" button."""
        if self._client is None:
            raise RuntimeError("MQTT is not connected")
        seconds = max(0, min(int(seconds or 0), 254))
        self._client.publish(f"{BASE_TOPIC}/bridge/request/permit_join", json.dumps({"time": seconds}))
        deadline = time.time() + 3.0
        while time.time() < deadline and self.permit_join != (seconds > 0):
            time.sleep(0.1)
        return {"ok": True, "permit_join": self.permit_join, "permit_join_end": self.permit_join_end, "requested_s": seconds}

    def discovery(self) -> dict:
        """Pairing state and the latest join/interview events, newest last."""
        with self._lock:
            events = list(self.events)
        return {"zigbee2mqtt_online": self.z2m_online, "permit_join": self.permit_join,
                "permit_join_end": self.permit_join_end, "events": events[-20:]}

    def get_device(self, name: str) -> dict:
        try:
            fname = self.resolve(name)
        except DeviceNotFound as error:
            return {"ok": False, "requested": name, "error": str(error), "feedback": f"NOT FOUND — {error}"}
        with self._lock:
            info = dict(self.devices[fname])
        info.pop("last_seen", None)
        display = (info.get("nickname") or "").strip() or fname
        readings = ", ".join(f"{info[k]} {unit}" for k, unit in (("power", "W"), ("current", "A"), ("voltage", "V"))
                             if isinstance(info.get(k), (int, float)))
        return {"ok": True, "friendly_name": fname, **info,
                "feedback": f"{display} is {info.get('state', 'unknown')}" + (f" ({readings})." if readings else ".")}

    def set_state(self, name: str, state: str) -> dict:
        """Switch a device and report what really happened. Every outcome carries `feedback`: one fixed sentence
        starting DONE / NO CHANGE / NOT DONE — what OpenAmika relays to her word for word, so she can only report the
        switch's real result."""
        command = str(state or "").strip().upper()
        if command not in ("ON", "OFF", "TOGGLE"):
            raise ValueError("state must be 'on', 'off' or 'toggle'")
        try:
            fname = self.resolve(name)
        except DeviceNotFound as error:
            return {"device": None, "requested": name, "ok": False, "error": str(error),
                    "feedback": f"NOT DONE — {error}"}
        with self._lock:
            info = self.devices.get(fname, {})
            before, avail, nick = info.get("state", "unknown"), info.get("availability", "unknown"), info.get("nickname", "")
        display = nick or fname
        base = {"device": fname, "nickname": nick, "requested": name, "matched": display}
        if avail == "offline":
            return {**base, "ok": False, "state": before, "error": f"{display} is offline.",
                    "feedback": f"NOT DONE — {display} is offline (unplugged or out of range); it was {before}."}
        if command in ("ON", "OFF") and before == command:
            return {**base, "ok": True, "state": before, "message": f"{display} is already {command}.",
                    "feedback": f"NO CHANGE — {display} was already {command}."}
        if self._client is None:
            return {**base, "ok": False, "state": before, "error": "MQTT is not connected",
                    "feedback": f"NOT DONE — the smart-home link is down, so {display} was not switched; it is still {before}."}
        expected = command if command != "TOGGLE" else {"ON": "OFF", "OFF": "ON"}.get(before)
        evt = self._event_for(fname)
        evt.clear()
        self._client.publish(f"{BASE_TOPIC}/{fname}/set", json.dumps({"state": command}))
        # wait for the device's own report of the new state (a power report in between is not a confirmation)
        deadline = time.time() + CONFIRM_TIMEOUT
        after = before
        while time.time() < deadline:
            if not evt.wait(max(0.0, deadline - time.time())):
                break
            evt.clear()
            with self._lock:
                after = self.devices.get(fname, {}).get("state", "unknown")
            if expected is None or after == expected:
                break
        if after in ("ON", "OFF") and (expected is None or after == expected) and (after != before or command == "TOGGLE"):
            return {**base, "ok": True, "state": after, "message": f"{display} is now {after}.",
                    "feedback": f"DONE — {display} is now {after}."}
        return {**base, "ok": False, "state": after,
                "error": f"{display} did not confirm the switch.",
                "feedback": f"NOT DONE — {display} did not answer the switch command; it is still {after}."}

    def set_nickname(self, name: str, nickname: str) -> dict:
        fname = self.resolve(name)
        nickname = (nickname or "").strip()
        with self._lock:
            self.devices[fname]["nickname"] = nickname
        self._save_devices()
        return {"device": fname, "nickname": nickname, "ok": True}

    def activate_scene(self, scene: str) -> dict:
        scenes = self.scenes()
        if scene not in scenes:
            raise ValueError(f"Unknown scene '{scene}'. Available: {', '.join(sorted(scenes)) or '(none configured)'}")
        results = {}
        for name, state in scenes[scene].items():
            try:
                results[name] = self.set_state(name, str(state))
            except (DeviceNotFound, ValueError) as error:
                results[name] = {"ok": False, "error": str(error)}
        ok = all(r.get("ok") for r in results.values()) if results else False
        return {"scene": scene, "ok": ok, "results": results}


BRIDGE = Bridge()
