"""Configuration for the Alexa API server."""

import os
import json
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Base URLs
# ---------------------------------------------------------------------------
ALEXA_BASE_URL = "https://alexa.amazon.com"
AMAZON_URL = ALEXA_BASE_URL  # backward-compat alias

# ---------------------------------------------------------------------------
# Paths (inside Docker container)
# ---------------------------------------------------------------------------
COOKIE_PATH = "/app/data/cookies.json"
DEVICE_CONFIG_PATH = "/app/data/device_config.json"

# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------
LOG_LEVEL = "INFO"
API_PORT = 8000
LOG_LEVEL_INT = getattr(logging, LOG_LEVEL.upper(), logging.INFO)

# ---------------------------------------------------------------------------
# Device config — seeded with known account values; auto-updated at runtime
# from the first successful /api/notifications call.
# ---------------------------------------------------------------------------
DEVICE_SERIAL_NUMBER: str = os.getenv(
    "DEVICE_SERIAL_NUMBER", "3C918C17CA864CEDA63C76331B8E4632"
)
DEVICE_TYPE: str = os.getenv("DEVICE_TYPE", "A2IVLV5VM2W81")
PERSON_ID: str = os.getenv(
    "PERSON_ID", "amzn1.account.AHWHY7SJOPOF46YDWX54OD4SLH7Q"
)

# ---------------------------------------------------------------------------
# Shopping List (www.amazon.com) — confirmed working endpoints
# ---------------------------------------------------------------------------
AMAZON_BASE_URL = "https://www.amazon.com"
SHOPPING_LIST_BASE = f"{AMAZON_BASE_URL}/alexashoppinglists/api"
DEFAULT_LIST_ID = (
    "YW16bjEuYWNjb3VudC5BR1Q2VUk0TVdOU1FISlRKVE5SQ1lKNVZIRVZBLVNIT1BQSU5HX0lURU0="
)
# Separate cookie store for www.amazon.com (different domain/auth from alexa.amazon.com)
AMAZON_COOKIE_PATH = "/app/data/amazon_cookies.json"

# ---------------------------------------------------------------------------
# Helpers — persist / restore device config across container restarts
# ---------------------------------------------------------------------------

def load_device_config() -> None:
    """Load device config from persistent storage into module-level vars."""
    global DEVICE_SERIAL_NUMBER, DEVICE_TYPE, PERSON_ID
    try:
        if os.path.exists(DEVICE_CONFIG_PATH):
            with open(DEVICE_CONFIG_PATH, encoding="utf-8") as f:
                data = json.load(f)
            DEVICE_SERIAL_NUMBER = data.get("deviceSerialNumber", DEVICE_SERIAL_NUMBER)
            DEVICE_TYPE = data.get("deviceType", DEVICE_TYPE)
            PERSON_ID = data.get("personId", PERSON_ID)
            logger.info("Loaded device config from %s", DEVICE_CONFIG_PATH)
    except Exception as exc:
        logger.warning("Could not load device config: %s", exc)


def save_device_config() -> None:
    """Persist current device config to disk."""
    try:
        os.makedirs(os.path.dirname(DEVICE_CONFIG_PATH), exist_ok=True)
        with open(DEVICE_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "deviceSerialNumber": DEVICE_SERIAL_NUMBER,
                    "deviceType": DEVICE_TYPE,
                    "personId": PERSON_ID,
                },
                f,
                indent=2,
            )
        logger.info("Saved device config to %s", DEVICE_CONFIG_PATH)
    except Exception as exc:
        logger.warning("Could not save device config: %s", exc)
