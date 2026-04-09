"""Login script — opens alexa.amazon.com, waits for manual sign-in,
then extracts the required session cookies and sends them to the API container.

Required cookies extracted:
  session-id, session-token, csrf, at-main, sess-at-main,
  x-main, sst-main, ubid-main
"""

import json
import logging
import sys
from typing import Dict, List

import nodriver as uc
import requests

try:
    from . import config as auth_config
except ImportError as exc:
    print(f"Error importing auth config: {exc}", file=sys.stderr)
    sys.exit(1)

logger = logging.getLogger("login_script")

# The 8 cookies required for authenticated Alexa API calls
REQUIRED_COOKIE_NAMES = {
    "session-id",
    "session-token",
    "csrf",
    "at-main",
    "sess-at-main",
    "x-main",
    "sst-main",
    "ubid-main",
}

TARGET_URL = auth_config.AMAZON_URL  # https://alexa.amazon.com


async def post_cookies_to_api(cookies: List[Dict]) -> bool:
    """POST the cookie list as JSON to the API /auth/cookies endpoint."""
    logger.info("Sending %d cookies to %s", len(cookies), auth_config.API_COOKIE_ENDPOINT)
    try:
        response = requests.post(auth_config.API_COOKIE_ENDPOINT, json=cookies, timeout=15)
        response.raise_for_status()
        logger.info("Cookies accepted by API (HTTP %d)", response.status_code)
        return True
    except requests.exceptions.ConnectionError:
        logger.error("Cannot reach API at %s — is it running?", auth_config.API_COOKIE_ENDPOINT)
    except requests.exceptions.Timeout:
        logger.error("Timeout posting cookies to %s", auth_config.API_COOKIE_ENDPOINT)
    except requests.exceptions.RequestException as exc:
        logger.error("Error posting cookies: %s", exc)
        if exc.response is not None:
            logger.error("API responded: %d %s", exc.response.status_code, exc.response.text)
    return False


async def main():
    logging.basicConfig(
        level=auth_config.LOG_LEVEL_INT,
        format="%(asctime)s - %(name)s [%(levelname)s] %(message)s",
        stream=sys.stdout,
    )
    logger.info("Starting Alexa auth process → %s", TARGET_URL)

    browser = None
    try:
        logger.info("Launching nodriver browser...")
        browser = await uc.start()
        page = await browser.get(TARGET_URL)
        logger.info("Navigated to %s", TARGET_URL)

        print("-" * 60)
        print("*** MANUAL LOGIN REQUIRED ***")
        print(f"A browser has opened to: {TARGET_URL}")
        print("Please complete sign-in (including any 2FA / CAPTCHA).")
        input("--> Press Enter here AFTER you have successfully signed in... ")
        print("-" * 60)

        logger.info("Extracting cookies...")
        raw_cookies = await browser.cookies.get_all(requests_cookie_format=True)

        if not raw_cookies:
            logger.error("No cookies extracted after login")
            sys.exit(1)

        logger.info("Extracted %d raw cookies total", len(raw_cookies))

        # Build serialisable dicts, filtering to only the required set
        required_cookies: List[Dict] = []
        found_names = set()

        for cookie in raw_cookies:
            name = getattr(cookie, "name", None)
            value = getattr(cookie, "value", None)
            if not name or not value:
                continue
            if name not in REQUIRED_COOKIE_NAMES:
                continue
            cookie_dict = {
                "name": name,
                "value": value,
                "domain": getattr(cookie, "domain", None),
                "path": getattr(cookie, "path", None),
            }
            expires = getattr(cookie, "expires", None)
            if expires is not None:
                cookie_dict["expires"] = expires
            secure = getattr(cookie, "secure", None)
            if secure is not None:
                cookie_dict["secure"] = secure
            http_only = getattr(cookie, "httpOnly", None)
            if http_only is not None:
                cookie_dict["httpOnly"] = http_only
            required_cookies.append({k: v for k, v in cookie_dict.items() if v is not None})
            found_names.add(name)

        missing = REQUIRED_COOKIE_NAMES - found_names
        if missing:
            logger.warning("Missing expected cookies: %s", missing)
            logger.warning("Proceeding with %d of %d required cookies", len(required_cookies), len(REQUIRED_COOKIE_NAMES))
        else:
            logger.info("All %d required cookies found", len(REQUIRED_COOKIE_NAMES))

        if not required_cookies:
            logger.error("No required cookies were found — aborting")
            sys.exit(1)

        success = await post_cookies_to_api(required_cookies)
        if not success:
            logger.error("Failed to send cookies to API")
            sys.exit(1)

        logger.info("Authentication complete — cookies sent successfully")

    except Exception as exc:
        logger.exception("Unexpected error during login: %s", exc)
        sys.exit(1)
    finally:
        if browser:
            try:
                browser.stop()
                logger.info("Browser closed")
            except Exception as exc:
                logger.warning("Error closing browser: %s", exc)


if __name__ == "__main__":
    try:
        uc.loop().run_until_complete(main())
    except KeyboardInterrupt:
        logging.getLogger().info("Login interrupted by user")
        sys.exit(0)
    except Exception as exc:
        logging.getLogger().exception("Critical error: %s", exc)
        sys.exit(1)
