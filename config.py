import os
from dotenv import load_dotenv

load_dotenv()

UI_USER      = os.environ.get("UI_USER", "admin")
UI_PASS      = os.environ.get("UI_PASS", "")
SECRET_KEY   = os.environ.get("SECRET_KEY", "change-me")

MQTT_HOST         = os.environ.get("MQTT_HOST", "10.0.0.232")
MQTT_PORT         = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USER         = os.environ.get("MQTT_USER", "")
MQTT_PASS         = os.environ.get("MQTT_PASS", "")
MQTT_CMD_TOPIC    = os.environ.get("MQTT_CMD_TOPIC", "esp32/pump/cmd")
MQTT_RESP_TOPIC   = os.environ.get("MQTT_RESP_TOPIC", "esp32/pump/resp")
MQTT_STATUS_TOPIC = os.environ.get("MQTT_STATUS_TOPIC", "esp32/pump/status")

# Dosing defaults — can move these to .env later if needed
CALMAG_ML_PER_GAL  = float(os.environ.get("CALMAG_ML_PER_GAL", "3.8"))
DEFAULT_STRENGTH   = float(os.environ.get("DEFAULT_STRENGTH", "0.33"))
DELAY_CALMAG_SEC   = int(os.environ.get("DELAY_CALMAG_SEC", "600"))
DELAY_MICRO_SEC    = int(os.environ.get("DELAY_MICRO_SEC", "150"))
DELAY_GRO_SEC      = int(os.environ.get("DELAY_GRO_SEC", "90"))
DELAY_BLOOM_SEC    = int(os.environ.get("DELAY_BLOOM_SEC", "90"))

# System definitions — add entries here to support multiple tents/dosers.
# pumps maps logical nutrient roles to ESP32 pump_id strings.
SYSTEMS = [
    {
        "id":   "tent1",
        "name": "Tent 1",
        "pumps": {
            "calmag": "calmag",
            "micro":  "micro",
            "gro":    "gro",
            "bloom":  "bloom",
            "phup":   "phup",
            "phdown": "phdown",
        },
    },
]

# GH Flora Basic Applications — full-strength ml/gal.
# Cal-Mag is not stage-dependent and lives in CALMAG_ML_PER_GAL above.
STAGE_RECIPES = {
    "seedling":       {"micro": 1.25, "gro": 1.25, "bloom": 1.25},
    "early_veg":      {"micro": 5.0,  "gro": 5.0,  "bloom": 5.0 },
    "late_veg":       {"micro": 10.0, "gro": 15.0, "bloom": 10.0},
    "early_bloom":    {"micro": 10.0, "gro": 10.0, "bloom": 10.0},
    "mid_late_bloom": {"micro": 10.0, "gro": 5.0,  "bloom": 15.0},
}

STAGE_LABELS = {
    "seedling":       "Seedling",
    "early_veg":      "Early Veg",
    "late_veg":       "Late Veg",
    "early_bloom":    "Early Bloom",
    "mid_late_bloom": "Mid / Late Bloom",
}
