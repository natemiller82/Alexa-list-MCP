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
# List endpoint — None until discovered at runtime via probe
# ---------------------------------------------------------------------------
LIST_API_ENDPOINT: str | None = None

# Candidate endpoints to probe in order (first 200-OK with list data wins)
LIST_ENDPOINT_CANDIDATES = [
    "/api/namedLists",
    "/api/todos?type=SHOPPING_ITEM&size=100",
    "/api/todos?type=TASK&size=100",
    "/api/lists",
    "/api/household-lists",
]

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
