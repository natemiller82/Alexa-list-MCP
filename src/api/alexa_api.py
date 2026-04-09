"""Alexa API client — targets the new alexa.amazon.com (Alexa+) interface.

Supports two independent systems:
  1. Tasks   — /api/notifications  (fully documented)
  2. Lists   — endpoint discovered at runtime via probe
"""

import json
import logging
import time
from typing import Any, Dict, List, Optional

import requests

from . import config as api_config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Required cookies to extract / forward
# ---------------------------------------------------------------------------
REQUIRED_COOKIES = {
    "session-id",
    "session-token",
    "csrf",
    "at-main",
    "sess-at-main",
    "x-main",
    "sst-main",
    "ubid-main",
}

# ---------------------------------------------------------------------------
# Cookie loading
# ---------------------------------------------------------------------------

def load_cookies_from_json_file(cookie_file_path: str) -> Optional[List[Dict[str, Any]]]:
    """Load cookies from a JSON file (list of dicts)."""
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


def _build_session() -> Optional[requests.Session]:
    """
    Build a requests.Session pre-loaded with cookies and Alexa-specific headers.

    Returns None if the cookie file is missing or contains no usable cookies.
    Extracts the `csrf` cookie and injects it as both `anti-csrftoken-a2z`
    and `csrf` request headers as required by the Alexa+ API.
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
        domain = c.get("domain")
        path = c.get("path")
        session.cookies.set(name=name, value=value, domain=domain, path=path)
        cookie_header_parts.append(f"{name}={value}")
        if name == "csrf":
            csrf_value = value

    if not csrf_value:
        logger.warning("No 'csrf' cookie found; anti-CSRF headers will be omitted")

    cookie_header = "; ".join(cookie_header_parts)

    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
            "content-type": "application/json",
            "Cookie": cookie_header,
            "Referer": "https://alexa.amazon.com/",
            "Origin": "https://alexa.amazon.com",
        }
    )

    if csrf_value:
        session.headers["anti-csrftoken-a2z"] = csrf_value
        session.headers["csrf"] = csrf_value

    return session


# ---------------------------------------------------------------------------
# Generic authenticated request
# ---------------------------------------------------------------------------

def make_authenticated_request(
    url: str,
    method: str = "GET",
    payload: Optional[Dict[str, Any]] = None,
) -> Optional[requests.Response]:
    """Make an authenticated request to the Alexa API."""
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


# ===========================================================================
# SYSTEM 1 — TASKS
# ===========================================================================

def get_tasks() -> Optional[List[Dict[str, Any]]]:
    """
    GET /api/notifications → filter type=='Task'.

    Side-effect: auto-populates device config (serial, type, personId)
    from the first result, then persists it.
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

    # Auto-populate device config from first result that has device info
    if notifications and (
        not api_config.DEVICE_SERIAL_NUMBER
        or api_config.DEVICE_SERIAL_NUMBER == "3C918C17CA864CEDA63C76331B8E4632"
    ):
        first = notifications[0]
        dsn = first.get("deviceSerialNumber") or first.get("deviceInfo", {}).get(
            "deviceSerialNumber"
        )
        dtype = first.get("deviceType") or first.get("deviceInfo", {}).get("deviceType")
        pid = (
            first.get("personId")
            or (first.get("personProfile") or {}).get("personId")
        )
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
    """
    POST /api/notifications/null — create a new task.

    `due_date` is accepted but stored as a label annotation for now (the
    Alexa task API does not accept a structured date in this body shape).
    """
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
    """Mark a task complete by fetching it, setting status='OFF', then PUTting it back."""
    tasks = get_tasks()
    if tasks is None:
        return False
    task = next((t for t in tasks if t.get("notificationIndex") == task_id or t.get("id") == task_id), None)
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
# SYSTEM 2 — LISTS
# ===========================================================================

def _probe_list_endpoints() -> Optional[str]:
    """
    Try each candidate list endpoint and return the first one that returns
    an HTTP 200 with a JSON body.  Stores result in api_config.LIST_API_ENDPOINT.
    """
    session = _build_session()
    if session is None:
        return None

    for candidate in api_config.LIST_ENDPOINT_CANDIDATES:
        url = f"{api_config.ALEXA_BASE_URL}{candidate}"
        try:
            resp = session.get(url, timeout=10)
            if resp.status_code == 200:
                try:
                    resp.json()  # validate it's parseable
                    logger.info("Discovered list endpoint: %s", candidate)
                    api_config.LIST_API_ENDPOINT = candidate
                    return candidate
                except ValueError:
                    pass
            else:
                logger.debug("Candidate %s → HTTP %d", candidate, resp.status_code)
        except Exception as exc:
            logger.debug("Candidate %s error: %s", candidate, exc)

    logger.warning("No working list endpoint found among candidates")
    return None


def _ensure_list_endpoint() -> Optional[str]:
    """Return the known list endpoint, probing if not yet discovered."""
    if api_config.LIST_API_ENDPOINT:
        return api_config.LIST_API_ENDPOINT
    return _probe_list_endpoints()


def get_lists() -> Optional[List[Dict[str, Any]]]:
    """Return all named lists (name + item count)."""
    endpoint = _ensure_list_endpoint()
    if not endpoint:
        return None

    # Strip query params for the base list call
    base_endpoint = endpoint.split("?")[0]
    url = f"{api_config.ALEXA_BASE_URL}{base_endpoint}"
    response = make_authenticated_request(url)
    if response is None:
        return None
    try:
        data = response.json()
    except ValueError:
        logger.error("Non-JSON response from list endpoint")
        return None

    # Normalise across different response shapes
    if isinstance(data, list):
        return data
    for key in ("lists", "namedLists", "householdLists", "result"):
        if key in data:
            return data[key]
    # Return the raw dict wrapped in a list as a fallback
    return [data]


def get_list_items(list_name: str) -> Optional[List[Dict[str, Any]]]:
    """Return items for the named list."""
    lists = get_lists()
    if lists is None:
        return None

    # Find the list by name (case-insensitive)
    target = next(
        (
            lst
            for lst in lists
            if (lst.get("name") or lst.get("listName") or "").lower()
            == list_name.lower()
        ),
        None,
    )
    if target is None:
        logger.error("List '%s' not found", list_name)
        return None

    list_id = target.get("listId") or target.get("id")
    if not list_id:
        logger.error("Could not determine list ID for '%s'", list_name)
        return None

    endpoint = _ensure_list_endpoint()
    if not endpoint:
        return None

    base_endpoint = endpoint.split("?")[0]
    url = f"{api_config.ALEXA_BASE_URL}{base_endpoint}/{list_id}/items"
    response = make_authenticated_request(url)
    if response is None:
        return None
    try:
        data = response.json()
    except ValueError:
        logger.error("Non-JSON response from list items endpoint")
        return None

    if isinstance(data, list):
        return data
    for key in ("listItems", "items", "result"):
        if key in data:
            return data[key]
    return [data]


def add_list_item(list_name: str, value: str) -> bool:
    """Add an item to the named list."""
    lists = get_lists()
    if lists is None:
        return False

    target = next(
        (
            lst
            for lst in lists
            if (lst.get("name") or lst.get("listName") or "").lower()
            == list_name.lower()
        ),
        None,
    )
    if target is None:
        logger.error("List '%s' not found", list_name)
        return False

    list_id = target.get("listId") or target.get("id")
    if not list_id:
        return False

    endpoint = _ensure_list_endpoint()
    if not endpoint:
        return False

    base_endpoint = endpoint.split("?")[0]
    url = f"{api_config.ALEXA_BASE_URL}{base_endpoint}/{list_id}/items"
    response = make_authenticated_request(url, method="POST", payload={"value": value})
    return response is not None


def remove_list_item(list_name: str, item_id: str) -> bool:
    """Remove an item from the named list."""
    lists = get_lists()
    if lists is None:
        return False

    target = next(
        (
            lst
            for lst in lists
            if (lst.get("name") or lst.get("listName") or "").lower()
            == list_name.lower()
        ),
        None,
    )
    if target is None:
        logger.error("List '%s' not found", list_name)
        return False

    list_id = target.get("listId") or target.get("id")
    if not list_id:
        return False

    endpoint = _ensure_list_endpoint()
    if not endpoint:
        return False

    base_endpoint = endpoint.split("?")[0]
    url = f"{api_config.ALEXA_BASE_URL}{base_endpoint}/{list_id}/items/{item_id}"
    response = make_authenticated_request(url, method="DELETE")
    return response is not None


# ===========================================================================
# Backward-compat helpers (used by old /items/* endpoints in main.py)
# ===========================================================================

def get_shopping_list_items() -> Optional[List[Dict[str, Any]]]:
    """Return items from the 'Shopping' list (backward compat)."""
    return get_list_items("Shopping")


def add_shopping_list_item(item_value: str) -> bool:
    return add_list_item("Shopping", item_value)


def delete_shopping_list_item(list_item: Dict[str, Any]) -> bool:
    item_id = list_item.get("itemId") or list_item.get("id")
    if not item_id:
        return False
    return remove_list_item("Shopping", str(item_id))


def mark_item_as_completed(list_item: Dict[str, Any]) -> bool:
    return _update_shopping_item_completion(list_item, completed=True)


def unmark_item_as_completed(list_item: Dict[str, Any]) -> bool:
    return _update_shopping_item_completion(list_item, completed=False)


def _update_shopping_item_completion(
    list_item: Dict[str, Any], completed: bool
) -> bool:
    lists = get_lists()
    if lists is None:
        return False
    target = next(
        (lst for lst in lists if (lst.get("name") or "").lower() == "shopping"),
        None,
    )
    if target is None:
        return False
    list_id = target.get("listId") or target.get("id")
    if not list_id:
        return False
    item_id = list_item.get("itemId") or list_item.get("id")
    if not item_id:
        return False

    endpoint = _ensure_list_endpoint()
    if not endpoint:
        return False

    base_endpoint = endpoint.split("?")[0]
    url = f"{api_config.ALEXA_BASE_URL}{base_endpoint}/{list_id}/items/{item_id}"
    payload = dict(list_item)
    payload["completed"] = completed
    response = make_authenticated_request(url, method="PUT", payload=payload)
    return response is not None


def filter_incomplete_items(
    list_items: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    return [item for item in list_items if not item.get("completed", False)]
