"""Alexa API client — targets two independent systems:

  System 1 — Tasks    (alexa.amazon.com /api/notifications)
  System 2 — Shopping (www.amazon.com /alexashoppinglists/api)
"""

import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

from . import config as api_config

logger = logging.getLogger(__name__)


# ===========================================================================
# Shared cookie loader
# ===========================================================================

def load_cookies_from_json_file(cookie_file_path: str) -> Optional[List[Dict[str, Any]]]:
    """Load cookies from a JSON file (list of dicts). Returns None on any error."""
    try:
        with open(cookie_file_path, "r", encoding="utf-8") as f:
            cookies_list = json.load(f)
        if not isinstance(cookies_list, list):
            logger.error("Expected list in %s, got %s", cookie_file_path, type(cookies_list))
            return None
        logger.debug("Loaded %d cookie dicts from %s", len(cookies_list), cookie_file_path)
        return cookies_list
    except FileNotFoundError:
        logger.error("Cookie file not found: %s", cookie_file_path)
        return None
    except json.JSONDecodeError as exc:
        logger.error("JSON decode error in %s: %s", cookie_file_path, exc)
        return None
    except Exception as exc:
        logger.error("Failed to load cookies from %s: %s", cookie_file_path, exc, exc_info=True)
        return None


# ===========================================================================
# SYSTEM 1 — TASKS  (alexa.amazon.com)
# DO NOT MODIFY — this section is the working Tasks API implementation
# ===========================================================================

# Required cookies for alexa.amazon.com
REQUIRED_ALEXA_COOKIES = {
    "session-id", "session-token", "csrf", "at-main",
    "sess-at-main", "x-main", "sst-main", "ubid-main",
}


def _build_session() -> Optional[requests.Session]:
    """
    Build a requests.Session for alexa.amazon.com (Tasks API).
    Extracts the `csrf` cookie and injects it as anti-CSRF headers.
    Returns None if the cookie file is missing or has no usable cookies.
    """
    cookie_list = load_cookies_from_json_file(api_config.COOKIE_PATH)
    if not cookie_list:
        logger.error("No cookies available at %s", api_config.COOKIE_PATH)
        return None

    session = requests.Session()
    csrf_value: Optional[str] = None
    cookie_header_parts: List[str] = []

    for c in cookie_list:
        name = c.get("name")
        value = c.get("value")
        if not name or not value:
            continue
        session.cookies.set(name=name, value=value, domain=c.get("domain"), path=c.get("path") or "/")
        cookie_header_parts.append(f"{name}={value}")
        if name == "csrf":
            csrf_value = value

    if not csrf_value:
        logger.warning("No 'csrf' cookie found; anti-CSRF headers will be omitted")

    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
            "content-type": "application/json",
            "Cookie": "; ".join(cookie_header_parts),
            "Referer": "https://alexa.amazon.com/",
            "Origin": "https://alexa.amazon.com",
        }
    )
    if csrf_value:
        session.headers["anti-csrftoken-a2z"] = csrf_value
        session.headers["csrf"] = csrf_value

    return session


def make_authenticated_request(
    url: str,
    method: str = "GET",
    payload: Optional[Dict[str, Any]] = None,
) -> Optional[requests.Response]:
    """Make an authenticated request to alexa.amazon.com. Returns None on any error."""
    session = _build_session()
    if session is None:
        return None
    try:
        logger.debug("%s %s", method.upper(), url)
        if method.upper() == "GET":
            response = session.get(url)
        elif method.upper() == "POST":
            response = session.post(url, json=payload)
        elif method.upper() == "PUT":
            response = session.put(url, json=payload)
        elif method.upper() == "DELETE":
            response = session.delete(url, json=payload)
        else:
            logger.error("Unsupported HTTP method: %s", method)
            return None
        response.raise_for_status()
        logger.debug("Response %d from %s", response.status_code, url)
        return response
    except requests.exceptions.RequestException as exc:
        logger.error("HTTP request failed (%s %s): %s", method, url, exc)
        return None
    except Exception as exc:
        logger.exception("Unexpected error during request: %s", exc)
        return None


def get_tasks() -> Optional[List[Dict[str, Any]]]:
    """
    GET /api/notifications → filter type=='Task'.
    Side-effect: auto-populates device config from the first result.
    """
    url = f"{api_config.ALEXA_BASE_URL}/api/notifications"
    response = make_authenticated_request(url)
    if response is None:
        return None
    try:
        data = response.json()
    except ValueError:
        logger.error("Non-JSON response from /api/notifications")
        return None

    notifications = data if isinstance(data, list) else data.get("notifications", [])

    if notifications and (
        not api_config.DEVICE_SERIAL_NUMBER
        or api_config.DEVICE_SERIAL_NUMBER == "3C918C17CA864CEDA63C76331B8E4632"
    ):
        first = notifications[0]
        dsn = first.get("deviceSerialNumber") or first.get("deviceInfo", {}).get("deviceSerialNumber")
        dtype = first.get("deviceType") or first.get("deviceInfo", {}).get("deviceType")
        pid = first.get("personId") or (first.get("personProfile") or {}).get("personId")
        if dsn:
            api_config.DEVICE_SERIAL_NUMBER = dsn
        if dtype:
            api_config.DEVICE_TYPE = dtype
        if pid:
            api_config.PERSON_ID = pid
        api_config.save_device_config()

    tasks = [n for n in notifications if n.get("type") == "Task"]
    logger.debug("Found %d tasks", len(tasks))
    return tasks


def create_task(title: str, due_date: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """POST /api/notifications/null — create a new task."""
    url = f"{api_config.ALEXA_BASE_URL}/api/notifications/null"
    now_ms = int(time.time() * 1000)
    body: Dict[str, Any] = {
        "type": "Task",
        "reminderLabel": title,
        "status": "ON",
        "deviceSerialNumber": api_config.DEVICE_SERIAL_NUMBER,
        "deviceType": api_config.DEVICE_TYPE,
        "rRuleData": {},
        "recurrenceEligibility": False,
        "originalDate": None,
        "originalTime": None,
        "alarmTime": 0,
        "triggerTime": 0,
        "lastUpdatedDate": now_ms,
        "personProfile": {"personId": api_config.PERSON_ID},
        "assigner": {"person": {"id": api_config.PERSON_ID}},
        "taskMetadata": None,
    }
    response = make_authenticated_request(url, method="POST", payload=body)
    if response is None:
        return None
    try:
        return response.json()
    except ValueError:
        logger.error("Non-JSON response from task creation")
        return None


def update_task(task_id: str, task_body: Dict[str, Any]) -> bool:
    """PUT /api/notifications/<task-id> — update a task (pass full task object)."""
    url = f"{api_config.ALEXA_BASE_URL}/api/notifications/{task_id}"
    response = make_authenticated_request(url, method="PUT", payload=task_body)
    return response is not None


def complete_task(task_id: str) -> bool:
    """Mark a task complete: fetch → set status='OFF' → PUT."""
    tasks = get_tasks()
    if tasks is None:
        return False
    task = next(
        (t for t in tasks if t.get("notificationIndex") == task_id or t.get("id") == task_id),
        None,
    )
    if task is None:
        logger.error("Task %s not found", task_id)
        return False
    task_copy = dict(task)
    task_copy["status"] = "OFF"
    return update_task(task_id, task_copy)


def delete_task(task_id: str) -> bool:
    """DELETE /api/notifications/<task-id>."""
    url = f"{api_config.ALEXA_BASE_URL}/api/notifications/{task_id}"
    response = make_authenticated_request(url, method="DELETE")
    return response is not None


# ===========================================================================
# SYSTEM 2 — SHOPPING LIST  (www.amazon.com)
# Confirmed working endpoints as of 2026-04.
# Requires separate amazon_cookies (different domain from Tasks cookies).
# ===========================================================================

class AmazonCookiesNotConfiguredError(Exception):
    """Raised when amazon_cookies have not been POSTed to /auth/cookies."""


# Exact headers required by the Amazon shopping list WAF
_SHOPPING_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/plain, */*",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/146.0.0.0 Safari/537.36 Edg/146.0.0.0"
    ),
    "Referer": "https://www.amazon.com/alexaquantum/sp/alexaShoppingList",
    "Origin": "https://www.amazon.com",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}


def _build_amazon_session() -> requests.Session:
    """
    Build a requests.Session for www.amazon.com shopping list requests.

    Raises AmazonCookiesNotConfiguredError if no cookies are stored.
    Warns if aws-waf-token is absent (requests will likely be blocked without it).
    """
    cookie_list = load_cookies_from_json_file(api_config.AMAZON_COOKIE_PATH)
    if not cookie_list:
        raise AmazonCookiesNotConfiguredError(
            "Amazon cookies not configured. "
            "POST to /auth/cookies with the amazon_cookies field."
        )

    session = requests.Session()
    cookie_header_parts: List[str] = []
    has_waf_token = False

    for c in cookie_list:
        name = c.get("name")
        value = c.get("value")
        if not name or not value:
            continue
        session.cookies.set(name=name, value=value, domain=c.get("domain"), path=c.get("path") or "/")
        cookie_header_parts.append(f"{name}={value}")
        if name == "aws-waf-token":
            has_waf_token = True

    if not has_waf_token:
        logger.warning(
            "aws-waf-token is missing from amazon_cookies. "
            "Shopping list requests will likely be blocked by AWS WAF. "
            "This token must be harvested from a browser session on a US IP address."
        )

    session.headers.update(_SHOPPING_HEADERS)
    session.headers["Cookie"] = "; ".join(cookie_header_parts)
    return session


def make_amazon_request(
    url: str,
    method: str = "GET",
    payload: Optional[Dict[str, Any]] = None,
) -> requests.Response:
    """
    Make an authenticated request to the Amazon shopping list API.

    Raises:
        AmazonCookiesNotConfiguredError — cookies not set
        requests.HTTPError             — non-2xx response (includes status code)
        requests.RequestException      — network / timeout errors
    """
    session = _build_amazon_session()
    logger.debug("%s %s", method.upper(), url)

    if method.upper() == "GET":
        response = session.get(url, timeout=15)
    elif method.upper() == "POST":
        response = session.post(url, json=payload, timeout=15)
    elif method.upper() == "PUT":
        response = session.put(url, json=payload, timeout=15)
    elif method.upper() == "DELETE":
        response = session.delete(url, json=payload, timeout=15)
    else:
        raise ValueError(f"Unsupported HTTP method: {method}")

    logger.debug("Response %d from %s", response.status_code, url)
    response.raise_for_status()
    return response


def _get_all_shopping_items() -> Tuple[str, List[Dict[str, Any]]]:
    """
    GET /alexashoppinglists/api/getlistitems

    Returns (list_id, all_items) for the default list.
    all_items includes both completed and incomplete items.

    Raises AmazonCookiesNotConfiguredError or requests.HTTPError on failure.
    """
    url = f"{api_config.SHOPPING_LIST_BASE}/getlistitems"
    response = make_amazon_request(url)
    data = response.json()

    # Response is a dict keyed by listId; find the one with defaultList == true
    for list_id, list_data in data.items():
        list_info = list_data.get("listInfo", {})
        if list_info.get("defaultList", False):
            return list_id, list_data.get("listItems", [])

    # Fallback: try the hardcoded DEFAULT_LIST_ID
    if api_config.DEFAULT_LIST_ID in data:
        return api_config.DEFAULT_LIST_ID, data[api_config.DEFAULT_LIST_ID].get("listItems", [])

    # Last resort: return the first list in the response
    for list_id, list_data in data.items():
        return list_id, list_data.get("listItems", [])

    return api_config.DEFAULT_LIST_ID, []


def get_shopping_list_items() -> List[Dict[str, Any]]:
    """
    Return active (incomplete) items from the default Alexa Shopping list.

    Raises AmazonCookiesNotConfiguredError or requests.HTTPError on failure.
    Each item dict contains at minimum: id, value, completed, listId,
    categoryValue, createdDateTime, updatedDateTime.
    """
    _, all_items = _get_all_shopping_items()
    return [item for item in all_items if not item.get("completed", False)]


def add_shopping_list_item(value: str) -> Dict[str, Any]:
    """
    POST /alexashoppinglists/api/addlistitem/{listId}

    Discovers the default list ID dynamically (falls back to DEFAULT_LIST_ID).
    Returns the created item object from the API response.

    Raises AmazonCookiesNotConfiguredError or requests.HTTPError on failure.
    """
    try:
        list_id, _ = _get_all_shopping_items()
    except Exception as exc:
        logger.warning("Could not discover list ID (%s), using DEFAULT_LIST_ID", exc)
        list_id = api_config.DEFAULT_LIST_ID

    url = f"{api_config.SHOPPING_LIST_BASE}/addlistitem/{list_id}"
    response = make_amazon_request(url, method="POST", payload={"value": value, "listItemMetadata": []})
    return response.json()


def complete_shopping_list_item(item_id: str) -> Dict[str, Any]:
    """
    PUT /alexashoppinglists/api/updatelistitem

    Fetches the full item object, sets completed=True and listItemMetadata=[],
    then PUTs the full object back.
    Returns the updated item object.

    Raises ValueError if the item is not found.
    Raises AmazonCookiesNotConfiguredError or requests.HTTPError on failure.
    """
    _, all_items = _get_all_shopping_items()
    item = next((i for i in all_items if i.get("id") == item_id), None)
    if item is None:
        raise ValueError(f"Shopping list item '{item_id}' not found")

    payload = dict(item)
    payload["completed"] = True
    payload["listItemMetadata"] = []

    url = f"{api_config.SHOPPING_LIST_BASE}/updatelistitem"
    response = make_amazon_request(url, method="PUT", payload=payload)
    return response.json()


def delete_shopping_list_item(item_id: str) -> bool:
    """
    DELETE /alexashoppinglists/api/deletelistitem

    Fetches the full item object (searches completed items too) and passes
    the entire object as the request body, as required by the API.
    Returns True on success.

    Raises ValueError if the item is not found.
    Raises AmazonCookiesNotConfiguredError or requests.HTTPError on failure.
    """
    _, all_items = _get_all_shopping_items()
    item = next((i for i in all_items if i.get("id") == item_id), None)
    if item is None:
        raise ValueError(f"Shopping list item '{item_id}' not found")

    url = f"{api_config.SHOPPING_LIST_BASE}/deletelistitem"
    make_amazon_request(url, method="DELETE", payload=item)
    return True


# ---------------------------------------------------------------------------
# Legacy wrappers — used by /items/* backward-compat routes in main.py
# These accept full item dicts (as returned by the old API) and delegate
# to the new item-ID-based functions above.
# ---------------------------------------------------------------------------

def mark_item_as_completed(list_item: Dict[str, Any]) -> bool:
    """[Legacy] Mark a shopping item complete; accepts a full item dict."""
    item_id = list_item.get("id")
    if not item_id:
        logger.error("mark_item_as_completed: item has no 'id' field")
        return False
    try:
        complete_shopping_list_item(item_id)
        return True
    except Exception as exc:
        logger.error("Failed to complete item %s: %s", item_id, exc)
        return False


def unmark_item_as_completed(list_item: Dict[str, Any]) -> bool:
    """[Legacy] The Amazon shopping list API does not support un-completing items."""
    logger.warning(
        "unmark_item_as_completed is not supported by the Amazon shopping list API. "
        "The item must be deleted and re-added to restore it as active."
    )
    return False


def filter_incomplete_items(list_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Filter a list of items to only those not yet completed."""
    return [item for item in list_items if not item.get("completed", False)]
