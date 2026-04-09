"""FastMCP server — proxies to the FastAPI backend.

Tools:
  Tasks:  get_tasks, create_task, complete_task, delete_task, update_task
  Lists:  get_lists, get_list_items, add_list_item, remove_list_item
  Legacy: get_all_items, get_incomplete_items, get_completed_items,
          add_item, delete_item, mark_item_completed, mark_item_incomplete
  Util:   check_api_status
"""

import json
import logging
import sys
from typing import Dict, List, Optional, Union

import requests
from fastmcp import FastMCP

try:
    from . import config as mcp_config
except ImportError as exc:
    print(f"Error importing MCP config: {exc}", file=sys.stderr)
    sys.exit(1)

logging.basicConfig(
    level=mcp_config.LOG_LEVEL_INT,
    format="%(asctime)s - %(name)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

if mcp_config.LOG_LEVEL_INT > logging.DEBUG:
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

API_BASE_URL = mcp_config.API_BASE_URL
logger.info("MCP Server → API at %s", API_BASE_URL)

mcp = FastMCP("Alexa (Tasks + Lists)")


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _req(method: str, endpoint: str, json_data: Optional[Dict] = None) -> Dict:
    url = f"{API_BASE_URL}{endpoint}"
    logger.debug("%s %s", method.upper(), url)
    try:
        if method.upper() == "GET":
            r = requests.get(url, timeout=15)
        elif method.upper() == "POST":
            r = requests.post(url, json=json_data, timeout=15)
        elif method.upper() == "PUT":
            r = requests.put(url, json=json_data, timeout=15)
        elif method.upper() == "DELETE":
            r = requests.delete(url, json=json_data, timeout=15)
        else:
            return {"error": f"Unsupported method: {method}"}

        r.raise_for_status()
        try:
            return r.json()
        except json.JSONDecodeError:
            return {"message": r.text}

    except requests.exceptions.ConnectionError:
        return {"error": f"Cannot connect to API at {API_BASE_URL}. Is it running?"}
    except requests.exceptions.HTTPError as exc:
        try:
            detail = exc.response.json().get("detail", str(exc))
        except Exception:
            detail = str(exc)
        return {"error": detail}
    except Exception as exc:
        return {"error": str(exc)}


# ===========================================================================
# TASK TOOLS
# ===========================================================================

@mcp.tool()
def get_tasks() -> list:
    """
    Returns all Alexa tasks. Each task has an id, reminderLabel (title),
    status ('ON' = active, 'OFF' = completed), and other fields.
    Returns an empty list on error.
    """
    result = _req("GET", "/tasks")
    if isinstance(result, list):
        return result
    if "error" in result:
        logger.error("get_tasks error: %s", result["error"])
        return []
    return result if isinstance(result, list) else []


@mcp.tool()
def create_task(title: str, due_date: Optional[str] = None) -> dict:
    """
    Creates a new Alexa task with the given title.
    Optionally accepts a due_date in ISO 8601 format (e.g. '2026-04-15').
    Returns the created task object or an error dict.
    """
    body: Dict = {"title": title}
    if due_date:
        body["due_date"] = due_date
    return _req("POST", "/tasks", body)


@mcp.tool()
def complete_task(task_id: str) -> dict:
    """
    Marks the task with the given task_id as complete (sets status='OFF').
    Returns a success message or an error dict.
    """
    return _req("POST", f"/tasks/{task_id}/complete")


@mcp.tool()
def delete_task(task_id: str) -> dict:
    """
    Permanently deletes the task with the given task_id.
    Returns a success message or an error dict.
    """
    return _req("DELETE", f"/tasks/{task_id}")


@mcp.tool()
def update_task(task_id: str, title: str) -> dict:
    """
    Updates the title of the task with the given task_id.
    Returns a success message or an error dict.
    """
    return _req("PUT", f"/tasks/{task_id}", {"title": title})


# ===========================================================================
# LIST TOOLS
# ===========================================================================

@mcp.tool()
def get_lists() -> list:
    """
    Returns all Alexa named lists (e.g. 'Shopping', 'Test') with metadata.
    Returns an empty list if no lists are found or an error occurs.
    """
    result = _req("GET", "/lists")
    if isinstance(result, list):
        return result
    if "error" in result:
        logger.error("get_lists error: %s", result["error"])
        return []
    return []


@mcp.tool()
def get_list_items(list_name: str) -> list:
    """
    Returns all items in the named Alexa list (case-insensitive name match).
    Example: get_list_items('Shopping') or get_list_items('Test').
    Returns an empty list if the list is not found or an error occurs.
    """
    result = _req("GET", f"/lists/{list_name}/items")
    if isinstance(result, list):
        return result
    if "error" in result:
        logger.error("get_list_items error: %s", result["error"])
        return []
    return []


@mcp.tool()
def add_list_item(list_name: str, value: str) -> dict:
    """
    Adds an item with the given text value to the named Alexa list.
    Example: add_list_item('Shopping', 'Milk')
    Returns a success message or an error dict.
    """
    return _req("POST", f"/lists/{list_name}/items", {"value": value})


@mcp.tool()
def remove_list_item(list_name: str, item_id: str) -> dict:
    """
    Removes the item with the given item_id from the named Alexa list.
    Use get_list_items() to discover item IDs.
    Returns a success message or an error dict.
    """
    return _req("DELETE", f"/lists/{list_name}/items/{item_id}")


# ===========================================================================
# LEGACY SHOPPING LIST TOOLS (backward compat)
# ===========================================================================

@mcp.tool()
def get_all_items() -> list:
    """
    [Legacy] Returns all items from the Alexa Shopping list (completed + active).
    Use get_list_items('Shopping') for the new interface.
    """
    result = _req("GET", "/items/all")
    if isinstance(result, list):
        return result
    logger.error("get_all_items error: %s", result.get("error", "unknown"))
    return []


@mcp.tool()
def get_incomplete_items() -> list:
    """[Legacy] Returns only the incomplete (active) items from the Shopping list."""
    result = _req("GET", "/items/incomplete")
    if isinstance(result, list):
        return result
    return []


@mcp.tool()
def get_completed_items() -> list:
    """[Legacy] Returns only the completed items from the Shopping list."""
    result = _req("GET", "/items/completed")
    if isinstance(result, list):
        return result
    return []


@mcp.tool()
def add_item(item_name: Union[str, List[str]]) -> dict:
    """
    [Legacy] Adds one or more items to the Alexa Shopping list.
    Accepts a single string or a list of strings.
    """
    names = [item_name] if isinstance(item_name, str) else item_name
    results = []
    all_ok = True
    for name in names:
        if not isinstance(name, str) or not name.strip():
            results.append({"item": name, "success": False, "message": "Invalid name"})
            all_ok = False
            continue
        r = _req("POST", "/items", {"item_name": name.strip()})
        ok = "error" not in r
        results.append({"item": name.strip(), "success": ok, "message": r.get("message", r.get("error", ""))})
        if not ok:
            all_ok = False
    msg = results[0]["message"] if len(names) == 1 else (
        f"Added {sum(1 for r in results if r['success'])}/{len(names)} items"
    )
    return {"success": all_ok, "message": msg, "details": results}


@mcp.tool()
def delete_item(item_name: Union[str, List[str]]) -> dict:
    """[Legacy] Deletes one or more items from the Shopping list by name."""
    names = [item_name] if isinstance(item_name, str) else item_name
    results = []
    all_ok = True
    for name in names:
        if not isinstance(name, str) or not name.strip():
            results.append({"item": name, "success": False, "message": "Invalid name"})
            all_ok = False
            continue
        r = _req("DELETE", "/items", {"item_name": name.strip()})
        ok = "error" not in r
        results.append({"item": name.strip(), "success": ok, "message": r.get("message", r.get("error", ""))})
        if not ok:
            all_ok = False
    msg = results[0]["message"] if len(names) == 1 else (
        f"Deleted {sum(1 for r in results if r['success'])}/{len(names)} items"
    )
    return {"success": all_ok, "message": msg, "details": results}


@mcp.tool()
def mark_item_completed(item_name: Union[str, List[str]]) -> dict:
    """[Legacy] Marks one or more Shopping list items as completed."""
    names = [item_name] if isinstance(item_name, str) else item_name
    results = []
    all_ok = True
    for name in names:
        r = _req("PUT", "/items/mark_completed", {"item_name": name.strip()})
        ok = "error" not in r
        results.append({"item": name, "success": ok, "message": r.get("message", r.get("error", ""))})
        if not ok:
            all_ok = False
    msg = results[0]["message"] if len(names) == 1 else (
        f"Marked {sum(1 for r in results if r['success'])}/{len(names)} items completed"
    )
    return {"success": all_ok, "message": msg, "details": results}


@mcp.tool()
def mark_item_incomplete(item_name: Union[str, List[str]]) -> dict:
    """[Legacy] Marks one or more completed Shopping list items as incomplete."""
    names = [item_name] if isinstance(item_name, str) else item_name
    results = []
    all_ok = True
    for name in names:
        r = _req("PUT", "/items/mark_incomplete", {"item_name": name.strip()})
        ok = "error" not in r
        results.append({"item": name, "success": ok, "message": r.get("message", r.get("error", ""))})
        if not ok:
            all_ok = False
    msg = results[0]["message"] if len(names) == 1 else (
        f"Marked {sum(1 for r in results if r['success'])}/{len(names)} items incomplete"
    )
    return {"success": all_ok, "message": msg, "details": results}


# ===========================================================================
# UTIL
# ===========================================================================

@mcp.tool()
def check_api_status() -> dict:
    """
    Checks whether the backend FastAPI server is reachable.
    Returns {'status': 'OK', ...} or {'status': 'ERROR', ...}.
    """
    result = _req("GET", "/")
    if "error" in result:
        return {"status": "ERROR", "message": result["error"]}
    return {"status": "OK", "message": "API is running", "details": result}


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("--- MCP Server: Starting ---", file=sys.stderr)
    try:
        mcp.run()
    except Exception as exc:
        logger.exception("Fatal error in mcp.run(): %s", exc)
        sys.exit(1)
