"""
Vehicle Holiday Controller for Raspberry Pi Pico W (MicroPython)

Single-file project that exposes a mobile-friendly web UI for lights/servos.
This script is designed for clarity and extensibility with ample comments.
"""

import json
import machine
import network
import os
import random
import socket
import time
from machine import PWM, Pin

# --------------------------- Configuration ---------------------------------
AP_SSID = "PicoXmasController"
AP_PASSWORD = "pico-holiday"
CONFIG_FILE = "config.json"
HTTP_PORT = 80

# Servo tuning (20 ms period)
SERVO_FREQ = 50
SERVO_MIN_US = 500
SERVO_MAX_US = 2500
PWM_FULL_SCALE = 65535

# --------------------------- Data Models -----------------------------------
class Channel:
    """Represents a controllable output channel."""

    def __init__(self, cfg):
        self.id = cfg.get("id")
        self.name = cfg.get("name", self.id)
        self.gpio = cfg.get("gpio_pin", 15)
        self.device_type = cfg.get("device_type", "light")
        self.group = cfg.get("group", "Default")
        self.state = cfg.get("state", {})
        self._setup_hardware()

    def _setup_hardware(self):
        self.pin = Pin(self.gpio, Pin.OUT)
        if self.device_type in ("light", "servo"):
            self.pwm = PWM(self.pin)
            self.pwm.freq(SERVO_FREQ if self.device_type == "servo" else 1000)
        else:
            self.pwm = None

    # -------- Servo helpers --------
    def _angle_to_duty(self, angle):
        pulse = SERVO_MIN_US + (SERVO_MAX_US - SERVO_MIN_US) * angle / 180
        duty = int(pulse * PWM_FULL_SCALE / 20000)
        return min(max(duty, 0), PWM_FULL_SCALE)

    # -------- Update loop --------
    def update(self, now_ms, global_dimmer=1.0, safety_limit=1.0, group_pattern=None):
        if self.device_type == "servo":
            self._update_servo(now_ms)
        elif self.device_type == "light":
            self._update_light(now_ms, global_dimmer, safety_limit, group_pattern)
        elif self.device_type == "generic_output":
            self._update_digital()

    # -------- Lights --------
    def _update_light(self, now_ms, global_dimmer, safety_limit, group_pattern):
        pattern = group_pattern or self.state.get("pattern", "steady")
        brightness = self.state.get("brightness", 0)
        params = self.state.get("pattern_params", {})
        level = brightness / 100
        if pattern == "blink":
            period = params.get("period", 1000)
            duty = params.get("duty", 0.5)
            phase = (now_ms % period) / period
            level = level if phase < duty else 0
        elif pattern == "fade":
            period = params.get("period", 2000)
            phase = (now_ms % period) / period
            level = (1 - abs(2 * phase - 1)) * level
        elif pattern == "chase":
            period = params.get("period", 1200)
            offset = params.get("offset", 0)
            phase = ((now_ms + offset) % period) / period
            level = level if phase < 0.25 else 0
        elif pattern == "sparkle":
            if random.random() < 0.05:
                self.state["_spark"] = now_ms
            if now_ms - self.state.get("_spark", 0) < 120:
                level = level
            else:
                level = 0
        elif pattern == "driving":
            period = params.get("period", 2500)
            phase = (now_ms % period) / period
            level = level * (0.3 + 0.2 * (1 - abs(2 * phase - 1)))
        elif pattern == "showtime":
            period = params.get("period", 700)
            phase = (now_ms % period) / period
            level = level if phase < 0.5 else level * 0.2
        # steady is default

        level = min(level * global_dimmer, safety_limit)
        duty = int(PWM_FULL_SCALE * level)
        if self.pwm:
            self.pwm.duty_u16(duty)

    # -------- Servo motion --------
    def _update_servo(self, now_ms):
        target = self.state.get("target_angle", 90)
        current = self.state.get("current_angle", 90)
        speed = self.state.get("speed", 2)  # degrees per tick
        mode = self.state.get("mode", "hold")
        min_a = self.state.get("min_angle", 0)
        max_a = self.state.get("max_angle", 180)
        last = self.state.get("_last_move", 0)
        if now_ms - last < 50:
            return
        self.state["_last_move"] = now_ms

        if mode == "sweep":
            direction = self.state.get("_dir", 1)
            target = max_a if direction > 0 else min_a
            if abs(current - target) < speed:
                self.state["_dir"] = -direction
            else:
                current += direction * speed
        elif mode == "pulse":
            a1 = self.state.get("pulse_a1", min_a)
            a2 = self.state.get("pulse_a2", max_a)
            period = self.state.get("pulse_period", 1500)
            phase = (now_ms % period) / period
            target = a1 if phase < 0.5 else a2
            step = speed if target > current else -speed
            if abs(current - target) > speed:
                current += step
            else:
                current = target
        elif mode == "jitter":
            if random.random() < 0.1:
                target = min(max_a, max(min_a, target + random.randint(-3, 3)))
            step = speed if target > current else -speed
            if abs(current - target) > speed:
                current += step
            else:
                current = target
        else:  # hold/go to
            step = speed if target > current else -speed
            if abs(current - target) > speed:
                current += step
            else:
                current = target

        current = min(max(current, min_a), max_a)
        self.state["current_angle"] = current
        duty = self._angle_to_duty(current)
        self.pwm.duty_u16(duty)

    # -------- Digital output --------
    def _update_digital(self):
        val = 1 if self.state.get("on", False) else 0
        self.pin.value(val)

    # -------- API helpers --------
    def to_dict(self):
        light = None
        servo = None
        if self.device_type in ("light", "generic_output"):
            brightness = self.state.get("brightness", 0)
            light = {
                "brightness": brightness / 100.0,
                "pattern": self.state.get("pattern", "steady"),
                "on": self.state.get("on", brightness > 0),
            }
        if self.device_type == "servo":
            servo = {
                "angle": self.state.get("current_angle", 90),
                "mode": self.state.get("mode", "hold"),
                "min_angle": self.state.get("min_angle", 0),
                "max_angle": self.state.get("max_angle", 180),
            }
        return {
            "id": self.id,
            "name": self.name,
            "gpio_pin": self.gpio,
            "gpio": self.gpio,
            "device_type": self.device_type,
            "group": self.group,
            "state": self.state,
            "light": light,
            "servo": servo,
        }


# --------------------------- Defaults --------------------------------------
DEFAULT_CONFIG = {
    "wifi": {"mode": "AP", "ap_ssid": AP_SSID, "ap_password": AP_PASSWORD},
    "global": {"dimmer": 1.0, "safety_limit": 1.0},
    "channels": [
        {
            "id": "CH1",
            "name": "Roof Lights",
            "gpio_pin": 15,
            "device_type": "light",
            "group": "Roof",
            "state": {"brightness": 0, "pattern": "steady"},
        },
        {
            "id": "CH2",
            "name": "Grille Lights",
            "gpio_pin": 16,
            "device_type": "light",
            "group": "Front",
            "state": {"brightness": 0, "pattern": "blink", "pattern_params": {"period": 900}},
        },
        {
            "id": "CH3",
            "name": "Reindeer Head",
            "gpio_pin": 17,
            "device_type": "servo",
            "group": "Creatures",
            "state": {"current_angle": 90, "target_angle": 90, "mode": "hold", "speed": 2},
        },
        {
            "id": "CH4",
            "name": "Tail Servo",
            "gpio_pin": 18,
            "device_type": "servo",
            "group": "Creatures",
            "state": {"current_angle": 90, "target_angle": 90, "mode": "sweep", "min_angle": 60, "max_angle": 120},
        },
    ],
    "scenes": [
        {
            "id": "calm",
            "name": "Calm Cruise",
            "description": "Soft fades, gentle servo breathing",
        },
        {
            "id": "northpole",
            "name": "North Pole",
            "description": "Bright blink + twinkle",
        },
        {
            "id": "spooky",
            "name": "Spooky Sleigh",
            "description": "Red glow with jittery motions",
        },
        {
            "id": "showstopper",
            "name": "Showstopper",
            "description": "Max brightness with big sweeps",
        },
    ],
    "user_scenes": [],
    "active_scene": "",
}


# --------------------------- State Management ------------------------------
def load_config():
    base = json.loads(json.dumps(DEFAULT_CONFIG))
    if CONFIG_FILE in os.listdir():
        try:
            with open(CONFIG_FILE, "r") as f:
                loaded = json.loads(f.read())
                if isinstance(loaded, dict):
                    base.update(loaded)
        except Exception:
            pass
    base.setdefault("wifi", {}).setdefault("mode", "AP")
    base.setdefault("wifi", {}).setdefault("ap_ssid", AP_SSID)
    base.setdefault("wifi", {}).setdefault("ap_password", AP_PASSWORD)
    base.setdefault("global", {}).setdefault("dimmer", 1.0)
    base.setdefault("global", {}).setdefault("safety_limit", 1.0)
    base.setdefault("channels", DEFAULT_CONFIG.get("channels", []))
    base.setdefault("scenes", DEFAULT_CONFIG.get("scenes", []))
    base.setdefault("user_scenes", [])
    base.setdefault("active_scene", "")
    return base


def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w") as f:
            f.write(json.dumps(cfg))
    except Exception:
        pass


def build_channels(cfg):
    return {c["id"]: Channel(c) for c in cfg.get("channels", [])}


# --------------------------- Wi-Fi Setup -----------------------------------
def start_wifi_ap(ssid, password):
    wlan = network.WLAN(network.AP_IF)
    wlan.active(True)
    wlan.config(essid=ssid, password=password)
    return wlan


def start_wifi_sta(ssid, password):
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(ssid, password)
    for _ in range(30):
        if wlan.isconnected():
            break
        time.sleep(0.2)
    return wlan


# --------------------------- Scene Logic -----------------------------------
def apply_scene(scene_id, cfg, channels):
    if not scene_id:
        return

    # user-defined snapshots
    for snap in cfg.get("user_scenes", []):
        if snap.get("name") == scene_id or snap.get("id") == scene_id:
            cfg["global"].update(snap.get("global", {}))
            for cid, state in snap.get("channels", {}).items():
                if cid in channels:
                    channels[cid].state.update(state)
            return

    name_map = {s.get("name"): s.get("id") for s in DEFAULT_CONFIG.get("scenes", [])}
    scene_key = name_map.get(scene_id, scene_id)

    if scene_key == "calm":
        cfg["global"]["dimmer"] = 0.4
        for ch in channels.values():
            if ch.device_type == "light":
                ch.state.update({"brightness": 30, "pattern": "fade"})
            elif ch.device_type == "servo":
                ch.state.update({"mode": "jitter", "speed": 1, "target_angle": 90})
    elif scene_key == "northpole":
        for ch in channels.values():
            if ch.device_type == "light":
                ch.state.update({"brightness": 80, "pattern": "blink", "pattern_params": {"period": 600}})
            elif ch.device_type == "servo":
                ch.state.update({"mode": "pulse", "pulse_a1": 70, "pulse_a2": 110, "pulse_period": 1000})
    elif scene_key == "spooky":
        cfg["global"]["dimmer"] = 0.3
        for ch in channels.values():
            if ch.device_type == "light":
                ch.state.update({"brightness": 50, "pattern": "showtime", "pattern_params": {"period": 1200}})
            elif ch.device_type == "servo":
                ch.state.update({"mode": "jitter", "speed": 3})
    elif scene_key == "showstopper":
        cfg["global"]["dimmer"] = 1.0
        for ch in channels.values():
            if ch.device_type == "light":
                ch.state.update({"brightness": 100, "pattern": "showtime", "pattern_params": {"period": 600}})
            elif ch.device_type == "servo":
                ch.state.update({"mode": "sweep", "min_angle": 30, "max_angle": 150, "speed": 4})


# --------------------------- HTTP Helpers ----------------------------------
HTML_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Vehicle Holiday Controller – Midnight Dashboard</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
:root {
  --bg: #020617;
  --bg-alt: #020617;
  --bg-card: #0f172a;
  --bg-card-alt: #020617;
  --accent: #38bdf8;
  --accent-soft: #f97316;
  --accent-green: #22c55e;
  --text: #e5e7eb;
  --text-muted: #9ca3af;
  --border: #1f2937;
  --danger: #f97373;
  --radius-lg: 18px;
  --radius-sm: 12px;
  --shadow-soft: 0 16px 40px rgba(0,0,0,0.55);
  --gap: 14px;
  --font: -apple-system, BlinkMacSystemFont, system-ui, sans-serif;
}
* { box-sizing: border-box; }
html, body {
  margin: 0;
  padding: 0;
  font-family: var(--font);
  background: radial-gradient(circle at top, #020617, #000000 65%);
  color: var(--text);
}
body {
  padding: 14px;
}
header {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 12px;
}
header .title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
h1 {
  margin: 0;
  font-size: 1.2rem;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}
header .subtitle {
  font-size: 0.72rem;
  color: var(--text-muted);
}
.badge {
  display: inline-flex;
  align-items: center;
  padding: 4px 11px;
  border-radius: 999px;
  font-size: 0.68rem;
  border: 1px solid rgba(148,163,184,0.5);
  background: radial-gradient(circle at top, #0f172a, #020617);
  box-shadow: 0 0 0 1px rgba(15,23,42,0.5);
}
.badge-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 7px;
}
.badge-dot.ap { background: var(--accent-soft); box-shadow: 0 0 10px rgba(249,115,22,0.8); }
.badge-dot.sta { background: var(--accent-green); box-shadow: 0 0 10px rgba(34,197,94,0.8); }

.status-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  font-size: 0.7rem;
  color: var(--text-muted);
}
.status-pill {
  padding: 4px 9px;
  border-radius: 999px;
  border: 1px solid rgba(55,65,81,0.8);
  background: radial-gradient(circle at top, rgba(15,23,42,0.95), rgba(15,23,42,0.6));
}

/* Layout */
main {
  display: flex;
  flex-direction: column;
  gap: var(--gap);
}
.section {
  background: radial-gradient(circle at top left, rgba(30,64,175,0.15), rgba(15,23,42,0.97));
  border-radius: var(--radius-lg);
  padding: 10px;
  box-shadow: var(--shadow-soft);
  border: 1px solid rgba(31,41,55,0.9);
}
.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}
.section-title {
  font-size: 0.85rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--text-muted);
}
.section-actions {
  display: flex;
  gap: 6px;
}
.section-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

/* Overview cards */
#overview-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(170px, 1fr));
  gap: 9px;
}
.card {
  background: linear-gradient(145deg, var(--bg-card), var(--bg-card-alt));
  border-radius: var(--radius-sm);
  padding: 9px;
  border: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  gap: 6px;
  position: relative;
  overflow: hidden;
}
.card::before {
  content: "";
  position: absolute;
  inset: -40%;
  background: radial-gradient(circle at top, rgba(56,189,248,0.08), transparent 60%);
  opacity: 0;
  transition: opacity 0.3s ease;
}
.card:hover::before {
  opacity: 1;
}
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.card-title {
  font-size: 0.8rem;
  font-weight: 600;
}
.card-sub {
  font-size: 0.65rem;
  color: var(--text-muted);
}
.card-chip {
  font-size: 0.64rem;
  padding: 2px 7px;
  border-radius: 999px;
  border: 1px solid rgba(148,163,184,0.4);
}
.card-chip.light { color: #facc15; border-color: rgba(250,204,21,0.55); }
.card-chip.servo { color: #a5b4fc; border-color: rgba(129,140,248,0.7); }
.card-chip.generic_output { color: #6ee7b7; border-color: rgba(34,197,94,0.7); }

.card-main-row {
  display: flex;
  gap: 8px;
}
.card-main-left {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.card-main-right {
  width: 60px;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 4px;
}
.small-label {
  font-size: 0.64rem;
  color: var(--text-muted);
}
.small-value {
  font-size: 0.7rem;
}
.slider-row {
  display: flex;
  align-items: center;
  gap: 4px;
}
input[type="range"] {
  flex: 1;
  -webkit-appearance: none;
  height: 4px;
  border-radius: 999px;
  background: #1e293b;
}
input[type="range"]::-webkit-slider-thumb {
  -webkit-appearance: none;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: var(--accent-soft);
  border: 1px solid #020617;
}
input[type="range"]::-moz-range-thumb {
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: var(--accent-soft);
  border: 1px solid #020617;
}
.badge-mini {
  font-size: 0.62rem;
  padding: 2px 7px;
  border-radius: 999px;
  border: 1px solid rgba(148,163,184,0.35);
}
.badge-on { color: var(--accent-green); border-color: rgba(34,197,94,0.7); }
.badge-off { color: #9ca3af; }
.badge-angle { color: #a5b4fc; }

/* Buttons */
.btn {
  font-size: 0.7rem;
  padding: 4px 9px;
  border-radius: 999px;
  border: 1px solid rgba(148,163,184,0.4);
  background: radial-gradient(circle at top, #020617, #020617);
  color: var(--text);
  cursor: pointer;
}
.btn-ghost {
  background: transparent;
}
.btn-primary {
  border-color: rgba(248,250,252,0.4);
  background: radial-gradient(circle at top, #f97316, #b91c1c);
}
.btn-danger {
  border-color: rgba(248,113,113,0.7);
  background: radial-gradient(circle at top, #fecaca, #7f1d1d);
}
.btn-sm {
  font-size: 0.65rem;
  padding: 3px 7px;
}

/* Selects */
select {
  background: #020617;
  color: var(--text);
  border-radius: 999px;
  border: 1px solid rgba(55,65,81,0.9);
  padding: 2px 7px;
  font-size: 0.7rem;
}

/* Scenes */
#scenes-list {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
}
.scene-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 9px;
  border-radius: 999px;
  border: 1px solid rgba(148,163,184,0.5);
  font-size: 0.7rem;
  cursor: pointer;
  background: radial-gradient(circle at top, rgba(15,23,42,0.95), rgba(15,23,42,0.6));
}
.scene-pill.active {
  border-color: var(--accent-soft);
  background: radial-gradient(circle at top, rgba(249,115,22,0.25), rgba(15,23,42,1));
}

/* Channels table */
#channels-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.7rem;
}
#channels-table th, #channels-table td {
  padding: 4px 5px;
  border-bottom: 1px solid rgba(31,41,55,0.95);
}
#channels-table th {
  text-align: left;
  color: var(--text-muted);
}

/* Settings */
.settings-row {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  align-items: center;
  font-size: 0.7rem;
}
.settings-row label {
  color: var(--text-muted);
}
.settings-row input[type="text"],
.settings-row input[type="password"] {
  background: #020617;
  border-radius: 999px;
  border: 1px solid rgba(55,65,81,0.9);
  padding: 3px 9px;
  color: var(--text);
  font-size: 0.7rem;
}

/* Debug */
details.debug {
  font-size: 0.7rem;
}
details.debug summary {
  cursor: pointer;
  list-style: none;
}
details.debug summary::-webkit-details-marker {
  display: none;
}
#debug-json {
  max-height: 160px;
  overflow: auto;
  background: #020617;
  padding: 7px;
  border-radius: 10px;
  font-size: 0.65rem;
  border: 1px solid #111827;
}

/* Responsive */
@media (min-width: 820px) {
  body {
    max-width: 960px;
    margin: 0 auto;
  }
}
</style>
</head>
<body>
<header>
  <div class="title-row">
    <div>
      <h1>Vehicle Holiday Controller</h1>
      <div class="subtitle">Midnight Dashboard UI</div>
    </div>
    <div id="wifi-badge" class="badge">
      <span class="badge-dot ap"></span>
      <span id="wifi-badge-text">AP MODE</span>
    </div>
  </div>
  <div class="status-row">
    <div class="status-pill">IP: <span id="ip-span">-</span></div>
    <div class="status-pill">Uptime: <span id="uptime-span">-</span></div>
    <div class="status-pill">Temp: <span id="temp-span">-</span></div>
    <div class="status-pill">Night Dimmer: <span id="night-span">-</span></div>
    <div class="status-pill">Power Limit: <span id="limit-span">-</span></div>
  </div>
</header>

<main>
  <section class="section" id="section-overview">
    <div class="section-header">
      <div class="section-title">Overview</div>
      <div class="section-actions">
        <button class="btn btn-sm btn-danger">Panic Off</button>
      </div>
    </div>
    <div class="section-body">
      <div id="overview-grid"></div>
    </div>
  </section>

  <section class="section" id="section-scenes">
    <div class="section-header">
      <div class="section-title">Scenes</div>
      <div class="section-actions">
        <button class="btn btn-sm btn-primary">Save Current</button>
      </div>
    </div>
    <div class="section-body">
      <div id="scenes-list"></div>
    </div>
  </section>

  <section class="section" id="section-channels">
    <div class="section-header">
      <div class="section-title">Channels & Pins</div>
      <div class="section-actions">
        <span style="font-size:0.7rem;color:var(--text-muted);">Tap fields to edit</span>
      </div>
    </div>
    <div class="section-body">
      <table id="channels-table">
        <thead>
          <tr>
            <th>ID</th>
            <th>Name</th>
            <th>GPIO</th>
            <th>Type</th>
            <th>Group</th>
          </tr>
        </thead>
        <tbody id="channels-tbody"></tbody>
      </table>
    </div>
  </section>

  <section class="section" id="section-settings">
    <div class="section-header">
      <div class="section-title">Advanced & Settings</div>
      <div class="section-actions">
        <button class="btn btn-sm btn-danger">Factory Reset</button>
        <button class="btn btn-sm">Reboot</button>
      </div>
    </div>
    <div class="section-body">
      <div class="settings-row">
        <label>Night Mode Dimmer</label>
        <input type="range" min="0" max="100" value="100">
      </div>
      <div class="settings-row">
        <label>Global Brightness Limit</label>
        <input type="range" min="10" max="100" value="100">
      </div>
      <hr style="border-color:#1f2937;border-width:0.5px;width:100%;opacity:0.8;">
      <div class="settings-row">
        <label>Wi-Fi Mode:</label>
        <select>
          <option value="AP">AP (Direct)</option>
          <option value="STA">STA (Join Wi-Fi)</option>
        </select>
      </div>
      <div class="settings-row">
        <label>STA SSID</label>
        <input type="text" placeholder="Your Wi-Fi name">
      </div>
      <div class="settings-row">
        <label>STA Password</label>
        <input type="password" placeholder="••••••••">
      </div>
      <div class="settings-row">
        <label>AP SSID</label>
        <input type="text" placeholder="ranger_pico">
      </div>
      <div class="settings-row">
        <label>AP Password</label>
        <input type="text" placeholder="(blank = open)">
      </div>
      <div class="settings-row">
        <button class="btn btn-sm btn-primary">Save Wi-Fi Config</button>
      </div>
      <div style="font-size:0.65rem;color:var(--text-muted);">
        STA changes take effect after reboot. AP mode is always available as fallback.
      </div>
    </div>
  </section>

  <section class="section" id="section-debug">
    <div class="section-header">
      <div class="section-title">Debug & Raw State</div>
    </div>
    <div class="section-body">
      <details class="debug">
        <summary>Show JSON state & logs</summary>
        <pre id="debug-json"></pre>
      </details>
    </div>
  </section>
</main>

<script>
let appState = {
  wifi_mode: "AP",
  cfg_wifi_mode: "AP",
  ip: "192.168.4.1",
  uptime_ms: 4523000,
  night_dimmer: 0.85,
  global_limit: 0.9,
  internal: { temp_c: 31.2 },
  channels: [
    { id:"CH1", name:"Roof Lights", gpio:15, device_type:"light", group:"Roof", light:{ brightness:0.8, pattern:"showtime", on:true } },
    { id:"CH2", name:"Grille Lights", gpio:16, device_type:"light", group:"Front", light:{ brightness:0.45, pattern:"driving", on:true } },
    { id:"CH3", name:"Reindeer Head", gpio:17, device_type:"servo", group:"Front", servo:{ angle:95, mode:"sweep", min_angle:60, max_angle:120 } },
    { id:"CH4", name:"Aux Relay", gpio:18, device_type:"generic_output", group:"Rear", light:{ brightness:0.2, pattern:"steady", on:false } }
  ],
  builtin_scenes:["Calm Cruise","North Pole","Spooky Sleigh","Showstopper"],
  user_scenes:["My Parade"],
  active_scene:"Showstopper"
};

function renderOverview() {
  const grid = document.getElementById("overview-grid");
  if (!appState || !grid) return;
  const chans = appState.channels || [];
  let html = "";
  chans.forEach(ch => {
    const light = ch.light || {};
    const servo = ch.servo || {};
    const isLight = ch.device_type === "light" || ch.device_type === "generic_output";
    const isServo = ch.device_type === "servo";
    const on = !!light.on;
    const brightness = Math.round((light.brightness || 0) * 100);
    const angle = Math.round(servo.angle || 0);
    const pattern = light.pattern || "steady";
    const mode = servo.mode || "idle";

    html += `
      <div class="card">
        <div class="card-header">
          <div>
            <div class="card-title">${escapeHtml(ch.name || ch.id)}</div>
            <div class="card-sub">${escapeHtml(ch.id)} · GPIO ${ch.gpio}</div>
          </div>
          <div class="card-main-right">
            <div class="card-chip ${ch.device_type}">
              ${ch.device_type === "light" ? "Light" :
                ch.device_type === "servo" ? "Servo" :
                "Output"}
            </div>
            <button class="btn btn-sm btn-ghost">
              ${on ? "Turn Off" : "Turn On"}
            </button>
          </div>
        </div>
        <div class="card-main-row">
          <div class="card-main-left">
            ${isLight ? `
            <div class="small-label">Brightness</div>
            <div class="slider-row">
              <input type="range" min="0" max="100" value="${brightness}">
              <span class="small-value">${brightness}%</span>
            </div>
            <div class="small-label">Pattern</div>
            <div>
              <select>
                ${renderPatternOptions(pattern)}
              </select>
            </div>
            ` : ""}
            ${isServo ? `
            <div class="small-label">Servo Angle</div>
            <div class="slider-row">
              <input type="range" min="0" max="180" value="${angle}">
              <span class="small-value">${angle}°</span>
            </div>
            <div class="small-label">Motion</div>
            <div>
              <select>
                ${renderServoModeOptions(mode)}
              </select>
            </div>
            ` : ""}
          </div>
          <div class="card-main-right">
            ${isLight ? `
            <div class="badge-mini ${on ? "badge-on" : "badge-off"}">
              ${on ? "ON" : "OFF"}
            </div>
            <div class="small-label" style="margin-top:4px;">${pattern}</div>
            ` : ""}
            ${isServo ? `
            <div class="badge-mini badge-angle">${angle}°</div>
            <div class="small-label" style="margin-top:4px;">${mode}</div>
            ` : ""}
          </div>
        </div>
      </div>
    `;
  });
  grid.innerHTML = html;
}

function renderPatternOptions(selected) {
  const patterns = [
    "steady","blink","fade","chase",
    "sparkle","driving","showtime"
  ];
  return patterns.map(p =>
    `<option value="${p}" ${p===selected?"selected":""}>${p}</option>`
  ).join("");
}

function renderServoModeOptions(selected) {
  const modes = ["idle","hold","goto","sweep","pulse","jitter"];
  return modes.map(m =>
    `<option value="${m}" ${m===selected?"selected":""}>${m}</option>`
  ).join("");
}

function renderScenes() {
  const el = document.getElementById("scenes-list");
  if (!appState || !el) return;
  const builtin = appState.builtin_scenes || [];
  const user = appState.user_scenes || [];
  const active = appState.active_scene || "";

  let html = "";
  if (builtin.length) {
    builtin.forEach(n => {
      html += `
        <div class="scene-pill ${n===active?"active":""}">
          <span>${escapeHtml(n)}</span>
        </div>`;
    });
  }
  if (user.length) {
    html += `<div style="flex-basis:100%;height:4px;"></div>`;
    user.forEach(n => {
      html += `
        <div class="scene-pill ${n===active?"active":""}">
          <span>${escapeHtml(n)}</span>
        </div>`;
    });
  }
  if (!html) {
    html = `<span style="font-size:0.7rem;color:var(--text-muted);">
      No scenes yet. Use "Save Current" to capture one.
    </span>`;
  }
  el.innerHTML = html;
}

function renderChannelsTable() {
  const tbody = document.getElementById("channels-tbody");
  if (!appState || !tbody) return;
  const chans = appState.channels || [];
  let html = "";
  chans.forEach(ch => {
    html += `
      <tr>
        <td>${escapeHtml(ch.id)}</td>
        <td>${escapeHtml(ch.name)}</td>
        <td>${ch.gpio}</td>
        <td>${ch.device_type}</td>
        <td>${escapeHtml(ch.group || "")}</td>
      </tr>
    `;
  });
  tbody.innerHTML = html;
}

function renderHeader() {
  if (!appState) return;
  const wifiBadge = document.getElementById("wifi-badge");
  const wifiBadgeText = document.getElementById("wifi-badge-text");
  document.getElementById("ip-span").textContent = appState.ip;
  document.getElementById("night-span").textContent = Math.round((appState.night_dimmer||1)*100)+"%";
  document.getElementById("limit-span").textContent = Math.round((appState.global_limit||1)*100)+"%";
  document.getElementById("uptime-span").textContent = Math.round((appState.uptime_ms||0)/1000)+"s";
  document.getElementById("temp-span").textContent = appState.internal.temp_c+"°C";
  if (appState.wifi_mode === "AP") {
    wifiBadgeText.textContent = "AP MODE";
    wifiBadge.querySelector(".badge-dot").className = "badge-dot ap";
  } else {
    wifiBadgeText.textContent = "STA MODE";
    wifiBadge.querySelector(".badge-dot").className = "badge-dot sta";
  }
  document.getElementById("debug-json").textContent = JSON.stringify(appState, null, 2);
}

function escapeHtml(str) {
  if (str == null) return "";
  return String(str)
    .replace(/&/g,"&amp;")
    .replace(/</g,"&lt;")
    .replace(/>/g,"&gt;")
    .replace(/"/g,"&quot;");
}

function renderApp() {
  renderHeader();
  renderOverview();
  renderScenes();
  renderChannelsTable();
}

renderApp();
</script>
</body>
</html>

"""


# --------------------------- HTTP Server -----------------------------------
def parse_request(raw):
    lines = raw.split("\r\n")
    request_line = lines[0]
    parts = request_line.split(" ")
    method = parts[0]
    path = parts[1]
    body = ""
    if "\r\n\r\n" in raw:
        body = raw.split("\r\n\r\n", 1)[1]
    return method, path, body


def json_response(sock, obj, status="200 OK"):
    payload = json.dumps(obj)
    response = "HTTP/1.1 %s\r\nContent-Type: application/json\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s" % (
        status,
        len(payload),
        payload,
    )
    sock.send(response)


def html_response(sock, html):
    response = "HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s" % (
        len(html),
        html,
    )
    sock.send(response)


# --------------------------- Application Core ------------------------------
def build_state(cfg, channels, wlan, boot_time):
    uptime_ms = int((time.time() - boot_time) * 1000)
    wifi_cfg = cfg.get("wifi", {})
    global_cfg = cfg.get("global", {})
    builtin_names = [s.get("name", s.get("id")) for s in DEFAULT_CONFIG.get("scenes", [])]
    user_names = [s.get("name", s.get("id")) for s in cfg.get("user_scenes", [])]
    return {
        "wifi_mode": wifi_cfg.get("mode", "AP"),
        "cfg_wifi_mode": wifi_cfg.get("mode", "AP"),
        "ip": wlan.ifconfig()[0] if wlan else None,
        "uptime_ms": uptime_ms,
        "night_dimmer": global_cfg.get("dimmer", 1.0),
        "global_limit": global_cfg.get("safety_limit", 1.0),
        "internal": {"temp_c": None},
        "channels": [c.to_dict() for c in channels.values()],
        "builtin_scenes": builtin_names,
        "user_scenes": user_names,
        "active_scene": cfg.get("active_scene", ""),
    }


def apply_control(payload, cfg, channels):
    action = payload.get("action")
    channel_id = payload.get("channel_id") or payload.get("id")

    if action == "set_light":
        ch = channels.get(channel_id)
        if ch and ch.device_type in ("light", "generic_output"):
            if payload.get("toggle"):
                current = ch.state.get("brightness", 0)
                ch.state["brightness"] = 0 if current > 0 else 100
                ch.state["on"] = ch.state["brightness"] > 0
            if "brightness" in payload:
                ch.state["brightness"] = int(payload.get("brightness", 0) * 100)
                ch.state["on"] = ch.state.get("brightness", 0) > 0
            if "pattern" in payload:
                ch.state["pattern"] = payload.get("pattern", "steady")
    elif action == "set_servo":
        ch = channels.get(channel_id)
        if ch and ch.device_type == "servo":
            if "angle" in payload:
                ch.state["target_angle"] = payload.get("angle", 90)
            if "mode" in payload:
                ch.state["mode"] = payload.get("mode", "hold")
    elif action == "set_channel_meta":
        ch = channels.get(channel_id)
        if ch:
            if "name" in payload:
                ch.name = payload["name"]
            if "group" in payload:
                ch.group = payload.get("group", ch.group)
            if "device_type" in payload and payload.get("device_type"):
                ch.device_type = payload.get("device_type")
                ch._setup_hardware()
            if "gpio" in payload and payload.get("gpio") is not None:
                ch.gpio = payload.get("gpio")
                ch._setup_hardware()
    elif action == "set_global":
        if "night_dimmer" in payload:
            cfg["global"]["dimmer"] = payload.get("night_dimmer", 1.0)
        if "global_limit" in payload:
            cfg["global"]["safety_limit"] = payload.get("global_limit", 1.0)
    elif action == "set_wifi":
        wifi = cfg.setdefault("wifi", {})
        wifi["mode"] = payload.get("mode", wifi.get("mode", "AP"))
        if payload.get("sta_ssid"):
            wifi["sta_ssid"] = payload.get("sta_ssid")
        if payload.get("sta_password"):
            wifi["sta_password"] = payload.get("sta_password")
        if payload.get("ap_ssid"):
            wifi["ap_ssid"] = payload.get("ap_ssid")
        if payload.get("ap_password") is not None:
            wifi["ap_password"] = payload.get("ap_password")
    elif action == "apply_scene":
        scene_name = payload.get("name") or payload.get("id")
        apply_scene(scene_name, cfg, channels)
        cfg["active_scene"] = scene_name or ""
    elif action == "save_scene":
        name = payload.get("name", "Custom")
        snapshot = {
            "id": str(int(time.time())),
            "name": name,
            "channels": {cid: c.state.copy() for cid, c in channels.items()},
            "global": cfg["global"].copy(),
        }
        cfg.setdefault("user_scenes", []).append(snapshot)
    elif action == "delete_scene":
        name = payload.get("name")
        cfg["user_scenes"] = [s for s in cfg.get("user_scenes", []) if s.get("name") != name]
    elif action == "panic":
        for ch in channels.values():
            if ch.device_type == "light":
                ch.state["brightness"] = 0
                ch.state["pattern"] = "steady"
            elif ch.device_type == "servo":
                ch.state["mode"] = "hold"
                ch.state["target_angle"] = ch.state.get("current_angle", 90)
            elif ch.device_type == "generic_output":
                ch.state["on"] = False
    elif action == "reset_defaults":
        cfg.clear()
        cfg.update(json.loads(json.dumps(DEFAULT_CONFIG)))
        save_config(cfg)
        try:
            machine.reset()
        except Exception:
            pass
    elif action == "reboot":
        try:
            machine.reset()
        except Exception:
            pass
    # Compatibility with earlier payloads
    elif action == "set_brightness":
        ch = channels.get(channel_id)
        if ch:
            ch.state["brightness"] = payload.get("value", 0)
    elif action == "set_pattern":
        ch = channels.get(channel_id)
        if ch:
            ch.state["pattern"] = payload.get("pattern", "steady")
    elif action == "set_servo_mode":
        ch = channels.get(channel_id)
        if ch and ch.device_type == "servo":
            ch.state["mode"] = payload.get("mode", "hold")
    elif action == "set_pattern_all":
        pattern = payload.get("pattern", "steady")
        for ch in channels.values():
            if ch.device_type == "light":
                ch.state["pattern"] = pattern
    elif action == "set_dimmer":
        cfg["global"]["dimmer"] = payload.get("value", 1.0)
    elif action == "set_safety":
        cfg["global"]["safety_limit"] = payload.get("value", 1.0)

    save_config(cfg)


# --------------------------- Main Loop -------------------------------------
def run():
    cfg = load_config()
    channels = build_channels(cfg)

    wifi_cfg = cfg.get("wifi", {})
    wlan = None
    if wifi_cfg.get("mode") == "STA":
        wlan = start_wifi_sta(wifi_cfg.get("sta_ssid", ""), wifi_cfg.get("sta_password", ""))
        if not wlan or not wlan.isconnected():
            wlan = start_wifi_ap(AP_SSID, AP_PASSWORD)
    else:
        wlan = start_wifi_ap(wifi_cfg.get("ap_ssid", AP_SSID), wifi_cfg.get("ap_password", AP_PASSWORD))

    addr = socket.getaddrinfo("0.0.0.0", HTTP_PORT)[0][-1]
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(addr)
    s.listen(1)
    s.settimeout(0.1)

    boot_time = time.time()
    print("Server listening on", addr)

    while True:
        now_ms = time.ticks_ms()
        for ch in channels.values():
            ch.update(now_ms, cfg["global"].get("dimmer", 1.0), cfg["global"].get("safety_limit", 1.0))

        try:
            client, _ = s.accept()
        except OSError:
            continue

        try:
            client.settimeout(2)
            request = client.recv(4096)
            raw = request.decode()
            method, path, body = parse_request(raw)
            if path == "/":
                html_response(client, HTML_PAGE)
            elif path.startswith("/api/state"):
                state = build_state(cfg, channels, wlan, boot_time)
                json_response(client, state)
            elif path.startswith("/api/control"):
                try:
                    payload = json.loads(body or "{}")
                except ValueError:
                    payload = {}
                apply_control(payload, cfg, channels)
                json_response(client, {"ok": True})
            else:
                json_response(client, {"error": "not found"}, status="404 Not Found")
        except Exception as e:
            try:
                json_response(client, {"error": str(e)}, status="500 Internal Server Error")
            except Exception:
                pass
        finally:
            client.close()


if __name__ == "__main__":
    run()
