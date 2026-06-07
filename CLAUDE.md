# CLAUDE.md

## Purpose

This is the grow-ui project — a lightweight web-based control panel for the hydroponic dosing system. It runs on the Raspberry Pi as a Flask app and serves as the primary operator interface for day-to-day grow operations.

At a high level, this repo is responsible for:

- presenting a single-page UI for nutrient dosing, manual pump control, and sensor monitoring
- computing nutrient doses from gallons added, growth stage, and strength factor
- publishing batch dosing commands to the ESP32 pump controller via MQTT
- sending sensor read/calibration commands to the ESP32 sensor controller via MQTT
- streaming live ESP32 events to the browser via SSE
- logging dosing history to a shared SQLite database

This repo is intentionally simple and focused. It is not a general-purpose automation platform — it is a dedicated control panel for one grow system.

## How This Repo Relates to the Other Repos

This repo sits between the operator and the hardware:

- **grow-ui (this repo):** UI, nutrient math, command dispatch, history logging
- **Mycodo repo:** alternative orchestration layer, custom outputs/functions, Mycodo UI integration
- **ESP32 pump controller:** executes pump batches, reports events
- **ESP32 sensor controller:** reads Atlas EZO-pH, EZO-EC, DS18B20, DHT22; reports sensor data

grow-ui and the Mycodo repo can both publish to `esp32/pump/cmd` — they are not mutually exclusive, but they should not both be running active batches at the same time.

## Tech Stack

- Python 3.10+ / Flask 3.0+
- paho-mqtt 1.6+ (paho v2 callback API — use `CallbackAPIVersion.VERSION2`)
- python-dotenv
- Vanilla JS single-page app (no frontend framework)
- SQLite (shared with Mycodo side via `grow_history.db`)

## Project Architecture

### Files

- `app.py` — Flask app, all routes, MQTT client, SSE broadcast, dosing logic, auth
- `config.py` — loads `.env`, defines topics, recipes, systems, delays, defaults
- `history.py` — SQLite read/write for dosing events and sensor readings
- `templates/index.html` — full SPA UI (tabs: Dose, Manual Pumps, Sensors, History)
- `templates/login.html` — login form
- `.env` — runtime secrets and config overrides (gitignored)

### MQTT Topics

| Topic | Direction | Purpose |
|---|---|---|
| `esp32/pump/cmd` | publish | Batch and direct pump commands |
| `esp32/pump/resp` | subscribe | Batch events, item events, pump responses |
| `esp32/pump/status` | subscribe | Pump controller heartbeat |
| `esp32/sensors/cmd` | publish | Sensor read and raw calibration commands |
| `esp32/sensors/resp` | subscribe | Per-sensor command responses |
| `esp32/sensors/status` | subscribe | Periodic sensor telemetry |

All subscribed MQTT messages are forwarded to the browser via SSE unchanged (with `_topic` and `_ts` fields appended).

### SSE Event Stream (`/stream`)

Each browser connection gets its own queue. `broadcast()` pushes to all live queues. A 100-event in-memory buffer (`_event_buffer`) is replayed to new connections on connect so the browser catches up after a refresh or reconnect. The keepalive comment is sent every 25s to keep proxies from closing the connection.

### Dosing Flow

1. User fills in gallons, stage, strength, optional per-dose delay overrides
2. Preview (`POST /preview`) — computes doses, returns amounts and delays for display
3. Dose (`POST /dose`) — validates, builds `pump_batch` JSON, publishes to MQTT, logs to SQLite
4. ESP32 executes batch sequentially, publishes `batch_item_event` and `batch_event` to `esp32/pump/resp`
5. Events arrive at grow-ui via MQTT subscription and are broadcast to the browser via SSE
6. Abort (`POST /abort`) — publishes `{"v":1,"type":"batch_abort"}` to stop the active batch

### Nutrient Math

- Cal-Mag: `gallons × CALMAG_ML_PER_GAL` (flat rate, not affected by strength)
- Micro/Gro/Bloom: `gallons × recipe[stage][nutrient] × strength`
- Stage recipes defined in `config.py` as `ml/gal` at full strength

### Authentication

- Username/password from `.env` (UI_USER, UI_PASS)
- Login issues a random hex token stored in `_authed_tokens` (in-memory set)
- Token stored as HTTP-only cookie (`grow_session`), 30-day max-age
- Tokens are cleared on app restart — users must re-login after service restart
- All routes except `/login` are protected by `login_required`

### History Logging

- SQLite at `/opt/Mycodo/mycodo/databases/grow_history.db` (shared with Mycodo)
- `dosing_events` table: trigger, pump_id, volume_ml, grow_stage, strength_factor, batch_id
- `sensor_readings` table: ph, ec, water_temp_f, air_temp_f, air_humidity
- Tables auto-created on first write
- `log_dosing_event()` never raises — failures are silent

## Configuration via `.env`

All tunables go in `.env`. Key values:

| Variable | Default | Purpose |
|---|---|---|
| `MQTT_HOST` | — | Broker IP |
| `MQTT_PORT` | `1883` | Broker port |
| `MQTT_USER` / `MQTT_PASS` | — | Broker credentials |
| `UI_USER` / `UI_PASS` | — | Web UI login |
| `SECRET_KEY` | — | Flask secret |
| `CALMAG_ML_PER_GAL` | `3.8` | Cal-Mag dose rate |
| `DELAY_CALMAG_SEC` | `600` | Post-Cal-Mag mixing delay |
| `DELAY_MICRO_SEC` | `150` | Post-Micro mixing delay |
| `DELAY_GRO_SEC` | `90` | Post-Gro mixing delay |
| `DELAY_BLOOM_SEC` | `90` | Post-Bloom mixing delay |

## Deployment

Runs as a systemd service on the Pi:

- Service name: `grow-ui`
- Working directory: `/home/pi/code/grow-ui`
- Virtualenv: `/home/pi/code/grow-ui/.venv`
- Port: `5001`
- Access locally at `http://10.0.0.232:5001` or via Tailscale

Typical deploy after changes:

```bash
cd /home/pi/code/grow-ui && git pull && sudo systemctl restart grow-ui
```

## Gotchas and Conventions

### 1. Per-dose delay overrides
The dose form accepts optional delay overrides per nutrient. If not provided, the server falls back to `config.py` defaults (which come from `.env`). Backend uses submitted delays in the batch payload — do not hardcode delays in `build_batch()`.

### 2. Cal-Mag is not strength-adjusted
This is intentional. Cal-Mag is a flat supplement dosed by volume of water added, not by nutrient strength. Do not apply `strength` to the Cal-Mag calculation.

### 3. Dosing order is fixed: Cal-Mag → Micro → Gro → Bloom
This follows GH Flora mixing instructions — Micro goes into fresh water first. Do not change the order without understanding the chemistry.

### 4. SSE buffer replays on reconnect
The last 100 events are buffered and replayed to new SSE connections. This means a browser refresh during a 20-minute batch delay will still show the CalMag completed event. Events are not persisted across app restarts.

### 5. paho-mqtt v2 API
The MQTT client uses `CallbackAPIVersion.VERSION2`. Callback signatures are different from paho v1. Do not mix v1-style callbacks (`on_connect(client, userdata, flags, rc)`) — they will silently fail.

### 6. Manual commands use `queue_if_busy`
Single-pump manual commands include `"options": {"queue_if_busy": true}` so they don't get rejected if a batch is running. Multi-pump motor commands are sent as a `pump_batch` with `stop_on_error: false`.

### 7. pH Up/Down safety threshold
The UI shows a confirmation dialog if dosing more than 20ml of pH Up or pH Down. This threshold is hardcoded in the `sendCommand()` JS function in `index.html`.

### 8. Shared SQLite database
`grow_history.db` is shared with the Mycodo repo. Both sides write to it. Schema is compatible — tables are created if not present. Do not change column names without updating both repos.

### 9. Multi-system support
`config.py` has a `SYSTEMS` list. The UI shows a system selector only if there is more than one system. Each system maps nutrient role names to ESP32 `pump_id` strings. Keep `pump_id` values aligned with the ESP32 firmware.
