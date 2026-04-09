# Configuration for the Auth (Login) Script
import logging

# Target the new Alexa+ interface
AMAZON_URL = "https://alexa.amazon.com"

# Path where the login script temporarily saves cookies before sending to API
LOCAL_TEMP_COOKIE_PATH = "./alexa_cookie.json"

# Logging level for the login script
LOG_LEVEL = "INFO"

# Host and Port of the running API container
API_HOST = "localhost"
API_PORT = 8000

# --- Derived --- #
LOG_LEVEL_INT = getattr(logging, LOG_LEVEL.upper(), logging.INFO)
API_COOKIE_ENDPOINT = f"http://{API_HOST}:{API_PORT}/auth/cookies"
