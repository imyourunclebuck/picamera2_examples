"""
Vehicle Holiday Controller for Raspberry Pi Pico W (MicroPython)

Single-file project that exposes a mobile-friendly web UI for lights/servos.
This script is designed for clarity and extensibility with ample comments.
"""

import json
import network
import os
import random
import socket
import time
from machine import PWM, Pin

# --------------------------- Configuration ---------------------------------
AP_SSID = "picopico"
AP_PASSWORD = "picopico"
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
        return {
            "id": self.id,
            "name": self.name,
            "gpio_pin": self.gpio,
            "device_type": self.device_type,
            "group": self.group,
            "state": self.state,
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
}


# --------------------------- State Management ------------------------------
def load_config():
    if CONFIG_FILE in os.listdir():
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.loads(f.read())
        except Exception:
            pass
    return DEFAULT_CONFIG


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
    if scene_id == "calm":
        cfg["global"]["dimmer"] = 0.4
        for ch in channels.values():
            if ch.device_type == "light":
                ch.state.update({"brightness": 30, "pattern": "fade"})
            elif ch.device_type == "servo":
                ch.state.update({"mode": "jitter", "speed": 1, "target_angle": 90})
    elif scene_id == "northpole":
        for ch in channels.values():
            if ch.device_type == "light":
                ch.state.update({"brightness": 80, "pattern": "blink", "pattern_params": {"period": 600}})
            elif ch.device_type == "servo":
                ch.state.update({"mode": "pulse", "pulse_a1": 70, "pulse_a2": 110, "pulse_period": 1000})
    elif scene_id == "spooky":
        cfg["global"]["dimmer"] = 0.3
        for ch in channels.values():
            if ch.device_type == "light":
                ch.state.update({"brightness": 50, "pattern": "showtime", "pattern_params": {"period": 1200}})
            elif ch.device_type == "servo":
                ch.state.update({"mode": "jitter", "speed": 3})
    elif scene_id == "showstopper":
        cfg["global"]["dimmer"] = 1.0
        for ch in channels.values():
            if ch.device_type == "light":
                ch.state.update({"brightness": 100, "pattern": "showtime", "pattern_params": {"period": 600}})
            elif ch.device_type == "servo":
                ch.state.update({"mode": "sweep", "min_angle": 30, "max_angle": 150, "speed": 4})


# --------------------------- HTTP Helpers ----------------------------------
HTML_PAGE = """
<!doctype html>
<html>
<head>
<meta charset='utf-8'>
<title>Vehicle Holiday Controller</title>
<style>
:root { color-scheme: dark; }
body { font-family: Arial, sans-serif; margin: 0; padding: 0; background: #0b0f14; color: #e4e8ef; }
header { background: #111926; padding: 12px 16px; display: flex; justify-content: space-between; align-items: center; position: sticky; top: 0; }
h1 { margin: 0; font-size: 20px; }
section { padding: 12px 16px; }
.card { background: #141c29; border-radius: 10px; padding: 12px; margin-bottom: 10px; box-shadow: 0 4px 12px rgba(0,0,0,0.3); }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 10px; }
button, select, input[type=range] { width: 100%; margin-top: 6px; padding: 8px; border-radius: 8px; border: 1px solid #1f2a3a; background: #182233; color: #e4e8ef; }
button { background: #1f6feb; border: none; cursor: pointer; font-weight: bold; }
button.danger { background: #c0392b; }
small { opacity: 0.7; }
.flex { display: flex; gap: 8px; align-items: center; }
.tag { padding: 2px 6px; border-radius: 6px; background: #1f2a3a; font-size: 12px; }
#debug { white-space: pre-wrap; font-size: 12px; background: #0f1622; padding: 8px; border-radius: 8px; }
</style>
</head>
<body>
<header>
  <div>
    <h1>Vehicle Holiday Controller</h1>
    <small id="status">Loading...</small>
  </div>
  <button class="danger" onclick="panicOff()">Panic Off</button>
</header>

<section>
  <h2>Overview</h2>
  <div id="channels" class="grid"></div>
</section>

<section class="card">
  <h3>Scenes</h3>
  <div id="scenes" class="grid"></div>
  <div class="flex">
    <input id="customScene" placeholder="Custom scene name" style="flex:1;" />
    <button onclick="saveScene()">Save Current</button>
  </div>
</section>

<section class="card">
  <h3>Global Controls</h3>
  <label>Night Mode Dimmer <span id="dimmerLabel"></span></label>
  <input type="range" id="dimmer" min="0" max="100" value="100" oninput="updateDimmer(this.value)" />
  <label>Safety Limit <span id="safetyLabel"></span></label>
  <input type="range" id="safety" min="10" max="100" value="100" oninput="updateSafety(this.value)" />
  <button onclick="applyDrivingMode()">Driving Mode (soft)</button>
  <button onclick="applyShowtime()">Showtime Mode (bright)</button>
</section>

<section class="card">
  <h3>Debug</h3>
  <details>
    <summary>Raw State JSON</summary>
    <div id="debug"></div>
  </details>
</section>

<script>
let state = {};
async function fetchState(){
  try {
    const res = await fetch('/api/state');
    state = await res.json();
    render();
  } catch(e) { console.log(e); }
}

function render(){
  document.getElementById('status').innerText = `Mode: ${state.wifi.mode} | IP: ${state.ip || 'n/a'} | Uptime: ${state.uptime_s}s`;
  document.getElementById('dimmer').value = Math.round(state.global.dimmer*100);
  document.getElementById('safety').value = Math.round(state.global.safety_limit*100);
  document.getElementById('dimmerLabel').innerText = `${Math.round(state.global.dimmer*100)}%`;
  document.getElementById('safetyLabel').innerText = `${Math.round(state.global.safety_limit*100)}%`;
  renderChannels();
  renderScenes();
  document.getElementById('debug').innerText = JSON.stringify(state, null, 2);
}

function renderChannels(){
  const wrap = document.getElementById('channels');
  wrap.innerHTML = '';
  state.channels.forEach(ch => {
    const div = document.createElement('div');
    div.className = 'card';
    div.innerHTML = `
      <div class="flex" style="justify-content:space-between;">
        <strong>${ch.name}</strong>
        <span class="tag">${ch.device_type}</span>
      </div>
      <small>GPIO ${ch.gpio_pin} · Group ${ch.group}</small>
    `;
    if(ch.device_type === 'light'){
      div.innerHTML += `
        <label>Brightness: <span>${ch.state.brightness||0}%</span></label>
        <input type="range" min="0" max="100" value="${ch.state.brightness||0}" onchange="setBrightness('${ch.id}', this.value)" />
        <label>Pattern</label>
        <select onchange="setPattern('${ch.id}', this.value)">
          ${['steady','blink','fade','chase','sparkle','driving','showtime'].map(p=>`<option ${ch.state.pattern===p?'selected':''} value="${p}">${p}</option>`).join('')}
        </select>
      `;
    } else if(ch.device_type === 'servo'){
      div.innerHTML += `
        <label>Angle: <span>${Math.round(ch.state.current_angle||90)}°</span></label>
        <input type="range" min="0" max="180" value="${Math.round(ch.state.target_angle||90)}" onchange="setServo('${ch.id}', this.value)" />
        <label>Mode</label>
        <select onchange="setServoMode('${ch.id}', this.value)">
          ${['hold','sweep','pulse','jitter'].map(p=>`<option ${ch.state.mode===p?'selected':''} value="${p}">${p}</option>`).join('')}
        </select>
      `;
    } else {
      div.innerHTML += `<button onclick="toggleDigital('${ch.id}')">Toggle</button>`;
    }
    wrap.appendChild(div);
  });
}

function renderScenes(){
  const wrap = document.getElementById('scenes');
  wrap.innerHTML = '';
  state.scenes.forEach(sc => {
    const div = document.createElement('div');
    div.className = 'card';
    div.innerHTML = `<strong>${sc.name}</strong><br/><small>${sc.description||''}</small><button onclick="applyScene('${sc.id}')">Apply</button>`;
    wrap.appendChild(div);
  });
}

async function setBrightness(id, value){
  await sendControl({action:'set_brightness', id, value: Number(value)});
}
async function setPattern(id, pattern){
  await sendControl({action:'set_pattern', id, pattern});
}
async function setServo(id, angle){
  await sendControl({action:'set_servo', id, angle: Number(angle)});
}
async function setServoMode(id, mode){
  await sendControl({action:'set_servo_mode', id, mode});
}
async function toggleDigital(id){ await sendControl({action:'toggle_digital', id}); }
async function applyScene(id){ await sendControl({action:'apply_scene', id}); }
async function applyDrivingMode(){ await sendControl({action:'set_pattern_all', pattern:'driving'}); }
async function applyShowtime(){ await sendControl({action:'set_pattern_all', pattern:'showtime'}); }
async function panicOff(){ await sendControl({action:'panic'}); }
async function updateDimmer(val){ await sendControl({action:'set_dimmer', value:Number(val)/100}); }
async function updateSafety(val){ await sendControl({action:'set_safety', value:Number(val)/100}); }
async function saveScene(){ const name=document.getElementById('customScene').value||'Custom'; await sendControl({action:'save_scene', name}); }

async function sendControl(payload){
  try {
    await fetch('/api/control', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
    fetchState();
  } catch(e){ console.log(e); }
}

setInterval(fetchState, 1000);
fetchState();
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
    uptime_s = int(time.time() - boot_time)
    return {
        "wifi": cfg.get("wifi", {}),
        "ip": wlan.ifconfig()[0] if wlan else None,
        "uptime_s": uptime_s,
        "global": cfg.get("global", {}),
        "channels": [c.to_dict() for c in channels.values()],
        "scenes": cfg.get("scenes", []),
    }


def apply_control(payload, cfg, channels):
    action = payload.get("action")
    if action == "set_brightness":
        ch = channels.get(payload.get("id"))
        if ch:
            ch.state["brightness"] = payload.get("value", 0)
    elif action == "set_pattern":
        ch = channels.get(payload.get("id"))
        if ch:
            ch.state["pattern"] = payload.get("pattern", "steady")
    elif action == "set_servo":
        ch = channels.get(payload.get("id"))
        if ch and ch.device_type == "servo":
            ch.state["target_angle"] = payload.get("angle", 90)
    elif action == "set_servo_mode":
        ch = channels.get(payload.get("id"))
        if ch and ch.device_type == "servo":
            ch.state["mode"] = payload.get("mode", "hold")
    elif action == "toggle_digital":
        ch = channels.get(payload.get("id"))
        if ch and ch.device_type == "generic_output":
            ch.state["on"] = not ch.state.get("on", False)
    elif action == "set_pattern_all":
        pattern = payload.get("pattern", "steady")
        for ch in channels.values():
            if ch.device_type == "light":
                ch.state["pattern"] = pattern
    elif action == "set_dimmer":
        cfg["global"]["dimmer"] = payload.get("value", 1.0)
    elif action == "set_safety":
        cfg["global"]["safety_limit"] = payload.get("value", 1.0)
    elif action == "apply_scene":
        apply_scene(payload.get("id"), cfg, channels)
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
    elif action == "save_scene":
        name = payload.get("name", "Custom")
        snapshot = {
            "id": str(int(time.time())),
            "name": name,
            "description": "User saved scene",
            "channels": {cid: c.state for cid, c in channels.items()},
            "global": cfg["global"].copy(),
        }
        cfg.setdefault("scenes", []).append(snapshot)
        save_config(cfg)
    elif action == "set_wifi":
        cfg["wifi"]["mode"] = payload.get("mode", "AP")
        cfg["wifi"]["sta_ssid"] = payload.get("ssid")
        cfg["wifi"]["sta_password"] = payload.get("password")
        save_config(cfg)
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
