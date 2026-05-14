import json
import queue
import threading
from datetime import datetime
from uuid import uuid4

import paho.mqtt.client as mqtt
import functools
from datetime import timedelta

from flask import Flask, Response, jsonify, redirect, render_template, request, session, url_for

from config import (
    CALMAG_ML_PER_GAL,
    DEFAULT_STRENGTH,
    DELAY_BLOOM_SEC,
    DELAY_CALMAG_SEC,
    DELAY_GRO_SEC,
    DELAY_MICRO_SEC,
    MQTT_CMD_TOPIC,
    MQTT_HOST,
    MQTT_PASS,
    MQTT_PORT,
    MQTT_RESP_TOPIC,
    MQTT_STATUS_TOPIC,
    MQTT_USER,
    SECRET_KEY,
    STAGE_LABELS,
    STAGE_RECIPES,
    SYSTEMS,
    UI_PASS,
    UI_USER,
)

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = False
app.permanent_session_lifetime = timedelta(days=30)


def login_required(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("authed"):
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return wrapper

# ── SSE broadcast ─────────────────────────────────────────────────────────────
# Each connected browser gets its own queue; broadcast pushes to all of them.
_sse_clients: list[queue.Queue] = []
_sse_lock = threading.Lock()
_mqtt_connected = False


def broadcast(data: dict) -> None:
    msg = "data: {}\n\n".format(json.dumps(data))
    with _sse_lock:
        dead = []
        for q in _sse_clients:
            try:
                q.put_nowait(msg)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _sse_clients.remove(q)


# ── MQTT client ───────────────────────────────────────────────────────────────
_mqtt = mqtt.Client(
    callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    client_id="doser-ui",
)


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _on_connect(client, userdata, connect_flags, reason_code, properties):
    global _mqtt_connected
    if reason_code.is_failure:
        _mqtt_connected = False
        broadcast({"type": "ui_event", "state": "mqtt_error", "rc": str(reason_code), "ts": _ts()})
    else:
        _mqtt_connected = True
        client.subscribe(MQTT_RESP_TOPIC)
        client.subscribe(MQTT_STATUS_TOPIC)
        broadcast({"type": "ui_event", "state": "mqtt_connected", "ts": _ts()})


def _on_disconnect(client, userdata, disconnect_flags, reason_code, properties):
    global _mqtt_connected
    _mqtt_connected = False
    broadcast({"type": "ui_event", "state": "mqtt_disconnected", "ts": _ts()})


def _on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode())
    except Exception:
        payload = {"raw": msg.payload.decode()}
    payload["_topic"] = msg.topic
    payload["_ts"] = _ts()
    broadcast(payload)


def start_mqtt() -> None:
    if MQTT_USER:
        _mqtt.username_pw_set(MQTT_USER, MQTT_PASS)
    _mqtt.on_connect = _on_connect
    _mqtt.on_disconnect = _on_disconnect
    _mqtt.on_message = _on_message
    _mqtt.connect_async(MQTT_HOST, MQTT_PORT, keepalive=30)
    _mqtt.loop_start()


# ── Nutrient math ─────────────────────────────────────────────────────────────
def compute_doses(gallons: float, stage: str, strength: float) -> dict:
    recipe = STAGE_RECIPES[stage]
    return {
        "calmag": round(gallons * CALMAG_ML_PER_GAL, 2),
        "micro":  round(gallons * recipe["micro"] * strength, 2),
        "gro":    round(gallons * recipe["gro"]   * strength, 2),
        "bloom":  round(gallons * recipe["bloom"] * strength, 2),
    }


def build_batch(doses: dict, system_id: str) -> dict:
    system = next(s for s in SYSTEMS if s["id"] == system_id)
    pumps = system["pumps"]
    return {
        "v": 1,
        "type": "pump_batch",
        "batch_id": "nutrient-{}".format(uuid4().hex[:8]),
        "options": {"stop_on_error": True},
        "commands": [
            {
                "item_id": "step-1",
                "pump_id": pumps["calmag"],
                "cmd": "D,{:.2f}".format(doses["calmag"]),
                "post_delay_ms": DELAY_CALMAG_SEC * 1000,
            },
            {
                "item_id": "step-2",
                "pump_id": pumps["micro"],
                "cmd": "D,{:.2f}".format(doses["micro"]),
                "post_delay_ms": DELAY_MICRO_SEC * 1000,
            },
            {
                "item_id": "step-3",
                "pump_id": pumps["gro"],
                "cmd": "D,{:.2f}".format(doses["gro"]),
                "post_delay_ms": DELAY_GRO_SEC * 1000,
            },
            {
                "item_id": "step-4",
                "pump_id": pumps["bloom"],
                "cmd": "D,{:.2f}".format(doses["bloom"]),
                "post_delay_ms": DELAY_BLOOM_SEC * 1000,
            },
        ],
    }


def _parse_form(data: dict) -> tuple:
    """Return (gallons, stage, strength, system_id) or raise ValueError."""
    gallons   = float(data["gallons"])
    stage     = data["stage"]
    strength  = float(data.get("strength", DEFAULT_STRENGTH))
    system_id = data.get("system_id", SYSTEMS[0]["id"])

    if gallons <= 0:
        raise ValueError("gallons must be > 0")
    if stage not in STAGE_RECIPES:
        raise ValueError("unknown stage: {}".format(stage))
    if not (0 < strength <= 1.0):
        raise ValueError("strength must be between 0 and 1")
    if not any(s["id"] == system_id for s in SYSTEMS):
        raise ValueError("unknown system_id: {}".format(system_id))

    return gallons, stage, strength, system_id


# ── Routes ────────────────────────────────────────────────────────────────────
PUMP_LABELS = {
    "calmag": "Cal-Mag",
    "micro":  "Micro",
    "gro":    "Gro",
    "bloom":  "Bloom",
    "phup":   "pH Up",
    "phdown": "pH Down",
}

PUMP_ORDER = ["calmag", "micro", "gro", "bloom", "phup", "phdown"]


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if request.form.get("username") == UI_USER and request.form.get("password") == UI_PASS:
            session.permanent = True
            session["authed"] = True
            return redirect(request.args.get("next") or url_for("index"))
        error = "Invalid username or password."
    return render_template("login.html", error=error)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    system = SYSTEMS[0]
    pumps = [{"id": pid, "label": PUMP_LABELS.get(pid, pid)} for pid in PUMP_ORDER if pid in system["pumps"].values()]
    return render_template(
        "index.html",
        systems=SYSTEMS,
        stages=STAGE_LABELS,
        default_strength=DEFAULT_STRENGTH,
        pumps=pumps,
        delays={
            "calmag": DELAY_CALMAG_SEC,
            "micro":  DELAY_MICRO_SEC,
            "gro":    DELAY_GRO_SEC,
            "bloom":  DELAY_BLOOM_SEC,
        },
    )


@app.route("/preview", methods=["POST"])
@login_required
def preview():
    try:
        gallons, stage, strength, _ = _parse_form(request.json)
    except (KeyError, ValueError) as e:
        return jsonify({"error": str(e)}), 400

    doses = compute_doses(gallons, stage, strength)
    return jsonify({
        "doses": doses,
        "total_ml": round(sum(doses.values()), 2),
        "delays": {
            "calmag": DELAY_CALMAG_SEC,
            "micro":  DELAY_MICRO_SEC,
            "gro":    DELAY_GRO_SEC,
            "bloom":  DELAY_BLOOM_SEC,
        },
    })


@app.route("/dose", methods=["POST"])
@login_required
def dose():
    try:
        gallons, stage, strength, system_id = _parse_form(request.json)
    except (KeyError, ValueError) as e:
        return jsonify({"error": str(e)}), 400

    doses = compute_doses(gallons, stage, strength)
    payload = build_batch(doses, system_id)
    _mqtt.publish(MQTT_CMD_TOPIC, json.dumps(payload))
    broadcast({"type": "ui_event", "state": "batch_sent", "batch_id": payload["batch_id"], "ts": _ts()})
    return jsonify({"ok": True, "batch_id": payload["batch_id"], "doses": doses})


@app.route("/abort", methods=["POST"])
@login_required
def abort():
    _mqtt.publish(MQTT_CMD_TOPIC, json.dumps({"v": 1, "type": "batch_abort"}))
    return jsonify({"ok": True})


@app.route("/command", methods=["POST"])
@login_required
def command():
    data = request.json
    pump_ids = data.get("pump_ids", [])
    cmd = data.get("cmd", "").strip()

    if not pump_ids:
        return jsonify({"error": "no pumps selected"}), 400
    if not cmd:
        return jsonify({"error": "no command specified"}), 400

    valid_pumps = {pid for s in SYSTEMS for pid in s["pumps"].values()}
    invalid = [p for p in pump_ids if p not in valid_pumps]
    if invalid:
        return jsonify({"error": "unknown pump_ids: {}".format(invalid)}), 400

    c = cmd.upper()
    is_motor_dose = c.startswith("D,") or c.startswith("DS,") or c.startswith("DC,") or c == "R"

    if len(pump_ids) > 1 and is_motor_dose:
        # Batch sequential execution for multi-pump motor commands
        payload = {
            "v": 1,
            "type": "pump_batch",
            "batch_id": "manual-{}".format(uuid4().hex[:8]),
            "options": {"stop_on_error": False},
            "commands": [
                {"item_id": "step-{}".format(i + 1), "pump_id": pid, "cmd": cmd}
                for i, pid in enumerate(pump_ids)
            ],
        }
        _mqtt.publish(MQTT_CMD_TOPIC, json.dumps(payload))
        broadcast({"type": "ui_event", "state": "batch_sent", "batch_id": payload["batch_id"], "ts": _ts()})
    else:
        # Individual commands — queue if motor is busy
        for pid in pump_ids:
            payload = {
                "v": 1,
                "pump_id": pid,
                "cmd": cmd,
                "request_id": "manual-{}".format(uuid4().hex[:6]),
                "options": {"queue_if_busy": True},
            }
            _mqtt.publish(MQTT_CMD_TOPIC, json.dumps(payload))

    return jsonify({"ok": True, "pump_ids": pump_ids, "cmd": cmd})


@app.route("/stream")
@login_required
def stream():
    """SSE endpoint — each browser connection gets its own message queue."""
    client_q: queue.Queue = queue.Queue(maxsize=200)
    with _sse_lock:
        _sse_clients.append(client_q)

    def generate():
        try:
            yield "data: {}\n\n".format(
                json.dumps({"type": "ui_event", "state": "stream_connected", "ts": _ts()})
            )
            # Immediately reflect current MQTT state so badge is correct on page load
            mqtt_state = "mqtt_connected" if _mqtt_connected else "mqtt_disconnected"
            yield "data: {}\n\n".format(
                json.dumps({"type": "ui_event", "state": mqtt_state, "ts": _ts()})
            )
            while True:
                try:
                    yield client_q.get(timeout=25)
                except queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            with _sse_lock:
                if client_q in _sse_clients:
                    _sse_clients.remove(client_q)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    start_mqtt()
    app.run(host="0.0.0.0", port=5001, debug=False, threaded=True)
