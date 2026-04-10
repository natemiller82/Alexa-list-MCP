"""FastAPI server — Alexa Tasks + Shopping List.

Endpoints:
  Tasks:    GET/POST /tasks, GET/PUT/DELETE /tasks/{id}, POST /tasks/{id}/complete
  Shopping: GET/POST /lists, PUT /lists/{item_id}/complete, DELETE /lists/{item_id}

Legacy (backward compat):
  GET  /items/all, /items/incomplete, /items/completed
  POST /items
  DELETE /items
  PUT  /items/mark_completed, /items/mark_incomplete
  POST /auth/cookies
"""

import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, Union

import requests as _requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from apscheduler.schedulers.asyncio import AsyncIOScheduler

try:
    from . import config as api_config
    from .alexa_api import (
        # Tasks (System 1 — alexa.amazon.com)
        get_tasks,
        create_task,
        update_task,
        complete_task,
        delete_task,
        # Shopping List (System 2 — www.amazon.com)
        AmazonCookiesNotConfiguredError,
        get_shopping_list_items,
        add_shopping_list_item,
        complete_shopping_list_item,
        delete_shopping_list_item,
        # Legacy shopping helpers
        mark_item_as_completed,
        unmark_item_as_completed,
        filter_incomplete_items,
    )
except ImportError as exc:
    print(f"FATAL: Could not import modules: {exc}", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=api_config.LOG_LEVEL_INT,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

if api_config.LOG_LEVEL_INT > logging.DEBUG:
    logging.getLogger("urllib3").setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# Scheduler — keep-alive
# ---------------------------------------------------------------------------
scheduler = AsyncIOScheduler()


async def _keep_alive():
    """Periodic keep-alive: fetch tasks to maintain the alexa.amazon.com session."""
    if not os.path.exists(api_config.COOKIE_PATH):
        logger.info("Keep-alive skipped: no alexa cookie file")
        return
    try:
        tasks = get_tasks()
        if tasks is not None:
            logger.info("Keep-alive OK: %d tasks", len(tasks))
        else:
            logger.warning("Keep-alive failed: alexa cookies may be expired")
    except Exception as exc:
        logger.error("Keep-alive error: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    api_config.load_device_config()
    scheduler.add_job(_keep_alive, "interval", seconds=60, id="keep_alive")
    scheduler.start()
    yield
    scheduler.shutdown()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Alexa API",
    description="Tasks (alexa.amazon.com) and Shopping List (www.amazon.com).",
    version="3.0.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class TaskCreateModel(BaseModel):
    title: str = Field(..., description="Task title / reminder label")
    due_date: Optional[str] = Field(None, description="Optional due date (ISO 8601)")


class TaskUpdateModel(BaseModel):
    title: str = Field(..., description="New task title")


class ShoppingItemModel(BaseModel):
    value: str = Field(..., description="Item text to add to the shopping list")


class ItemNameModel(BaseModel):
    item_name: str = Field(..., description="Shopping list item name (legacy)")


class CookieModel(BaseModel):
    name: str
    value: str
    domain: Optional[str] = None
    path: Optional[str] = None
    expires: Optional[Union[str, int, float]] = None
    secure: Optional[bool] = None
    httpOnly: Optional[bool] = None


class CookieUploadModel(BaseModel):
    """
    Accepts alexa_cookies (alexa.amazon.com) and/or amazon_cookies (www.amazon.com).
    Only the provided fields are written — the other is left untouched.
    """
    alexa_cookies: Optional[List[CookieModel]] = Field(
        None,
        description="Cookies for alexa.amazon.com (Tasks API). "
                    "Required cookies: session-id, session-token, csrf, at-main, "
                    "sess-at-main, x-main, sst-main, ubid-main.",
    )
    amazon_cookies: Optional[List[CookieModel]] = Field(
        None,
        description="Cookies for www.amazon.com (Shopping List API). "
                    "Must include aws-waf-token (harvested from a US IP browser session).",
    )


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _task_id(task: Dict[str, Any]) -> str:
    return task.get("notificationIndex") or task.get("id") or ""


def _shopping_error(exc: Exception) -> HTTPException:
    """Convert a shopping list exception to an HTTPException with a clear message."""
    if isinstance(exc, AmazonCookiesNotConfiguredError):
        return HTTPException(
            status_code=503,
            detail=(
                "Amazon cookies not configured. "
                "POST to /auth/cookies with the amazon_cookies field. "
                "Ensure aws-waf-token is included (required; harvested from a US IP browser session)."
            ),
        )
    if isinstance(exc, _requests.HTTPError):
        status = exc.response.status_code if exc.response is not None else 502
        return HTTPException(
            status_code=502,
            detail=f"Amazon API returned HTTP {status}: {exc}",
        )
    if isinstance(exc, ValueError):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=500, detail=f"Shopping list error: {exc}")


# ===========================================================================
# ROOT
# ===========================================================================

@app.get("/", tags=["Status"])
async def read_root():
    return {"status": "Alexa API is running", "version": "3.0.0"}


# ===========================================================================
# TASKS  (alexa.amazon.com — DO NOT MODIFY)
# ===========================================================================

@app.get("/tasks", tags=["Tasks"])
async def list_tasks():
    """Return all tasks (status ON = active, OFF = completed)."""
    tasks = get_tasks()
    if tasks is None:
        raise HTTPException(503, "Could not retrieve tasks from Alexa")
    return tasks


@app.post("/tasks", tags=["Tasks"], status_code=201)
async def create_new_task(body: TaskCreateModel):
    """Create a new task."""
    result = create_task(body.title, body.due_date)
    if result is None:
        raise HTTPException(500, f"Failed to create task '{body.title}'")
    return result


@app.put("/tasks/{task_id}", tags=["Tasks"])
async def update_task_title(task_id: str, body: TaskUpdateModel):
    """Update a task's title."""
    tasks = get_tasks()
    if tasks is None:
        raise HTTPException(503, "Could not retrieve tasks")
    task = next((t for t in tasks if _task_id(t) == task_id), None)
    if task is None:
        raise HTTPException(404, f"Task '{task_id}' not found")
    patched = dict(task)
    patched["reminderLabel"] = body.title
    if not update_task(task_id, patched):
        raise HTTPException(500, f"Failed to update task '{task_id}'")
    return {"message": f"Task '{task_id}' updated"}


@app.delete("/tasks/{task_id}", tags=["Tasks"])
async def delete_task_endpoint(task_id: str):
    """Delete a task entirely."""
    if not delete_task(task_id):
        raise HTTPException(500, f"Failed to delete task '{task_id}'")
    return {"message": f"Task '{task_id}' deleted"}


@app.post("/tasks/{task_id}/complete", tags=["Tasks"])
async def complete_task_endpoint(task_id: str):
    """Mark a task as complete (sets status='OFF')."""
    if not complete_task(task_id):
        raise HTTPException(500, f"Failed to complete task '{task_id}'")
    return {"message": f"Task '{task_id}' marked complete"}


# ===========================================================================
# SHOPPING LIST  (www.amazon.com)
# ===========================================================================

@app.get("/lists", tags=["Shopping List"])
async def get_shopping_list():
    """Return all active (incomplete) Alexa Shopping list items."""
    try:
        items = get_shopping_list_items()
    except Exception as exc:
        raise _shopping_error(exc)
    return items


@app.post("/lists", tags=["Shopping List"], status_code=201)
async def add_shopping_item(body: ShoppingItemModel):
    """Add an item to the Alexa Shopping list. Returns the created item."""
    try:
        item = add_shopping_list_item(body.value)
    except Exception as exc:
        raise _shopping_error(exc)
    return item


@app.put("/lists/{item_id}/complete", tags=["Shopping List"])
async def complete_shopping_item(item_id: str):
    """Mark a shopping list item as complete. Returns the updated item."""
    try:
        item = complete_shopping_list_item(item_id)
    except Exception as exc:
        raise _shopping_error(exc)
    return item


@app.delete("/lists/{item_id}", tags=["Shopping List"])
async def delete_shopping_item(item_id: str):
    """Delete an item from the Alexa Shopping list."""
    try:
        delete_shopping_list_item(item_id)
    except Exception as exc:
        raise _shopping_error(exc)
    return {"message": f"Item '{item_id}' deleted from shopping list"}


# ===========================================================================
# LEGACY — /items/*  (backward compat)
# ===========================================================================

def _legacy_get_items() -> List[Dict[str, Any]]:
    """Call get_shopping_list_items, converting exceptions to HTTPExceptions."""
    try:
        return get_shopping_list_items()
    except AmazonCookiesNotConfiguredError:
        raise _shopping_error(AmazonCookiesNotConfiguredError())
    except Exception as exc:
        raise _shopping_error(exc)


def _legacy_find_by_name(items: List[Dict[str, Any]], name: str) -> Optional[Dict[str, Any]]:
    for item in items:
        if (item.get("value") or "").lower() == name.lower():
            return item
    return None


@app.get("/items/all", tags=["Legacy-Shopping"])
async def get_all_list_items():
    items = _legacy_get_items()
    return items + [i for i in _legacy_get_all_including_completed() if i.get("completed", False)]


def _legacy_get_all_including_completed() -> List[Dict[str, Any]]:
    try:
        from .alexa_api import _get_all_shopping_items
        _, all_items = _get_all_shopping_items()
        return all_items
    except Exception:
        return []


@app.get("/items/incomplete", tags=["Legacy-Shopping"])
async def get_incomplete_list_items():
    return _legacy_get_items()


@app.get("/items/completed", tags=["Legacy-Shopping"])
async def get_completed_list_items():
    try:
        from .alexa_api import _get_all_shopping_items
        _, all_items = _get_all_shopping_items()
        return [i for i in all_items if i.get("completed", False)]
    except Exception as exc:
        raise _shopping_error(exc)


@app.post("/items", tags=["Legacy-Shopping"], status_code=201)
async def add_new_item(item_data: ItemNameModel):
    try:
        add_shopping_list_item(item_data.item_name)
    except Exception as exc:
        raise _shopping_error(exc)
    return {"message": f"Item '{item_data.item_name}' added successfully"}


@app.delete("/items", tags=["Legacy-Shopping"])
async def remove_item(item_data: ItemNameModel):
    all_items = _legacy_get_all_including_completed() or []
    item = _legacy_find_by_name(all_items, item_data.item_name)
    if not item:
        raise HTTPException(404, f"Item '{item_data.item_name}' not found")
    try:
        delete_shopping_list_item(item["id"])
    except Exception as exc:
        raise _shopping_error(exc)
    return {"message": f"Item '{item_data.item_name}' deleted successfully"}


@app.put("/items/mark_completed", tags=["Legacy-Shopping"])
async def mark_item_complete(item_data: ItemNameModel):
    items = _legacy_get_items()
    item = _legacy_find_by_name(filter_incomplete_items(items), item_data.item_name)
    if not item:
        raise HTTPException(404, f"Incomplete item '{item_data.item_name}' not found")
    if not mark_item_as_completed(item):
        raise HTTPException(500, f"Failed to mark '{item_data.item_name}' completed")
    return {"message": f"Item '{item_data.item_name}' marked as completed"}


@app.put("/items/mark_incomplete", tags=["Legacy-Shopping"])
async def mark_item_incomplete_endpoint(item_data: ItemNameModel):
    raise HTTPException(
        501,
        "Marking items incomplete is not supported by the Amazon shopping list API. "
        "Delete the item and re-add it instead.",
    )


# ===========================================================================
# AUTH — cookie ingestion
# ===========================================================================

@app.post("/auth/cookies", tags=["Authentication"], status_code=200)
async def receive_cookies(body: CookieUploadModel):
    """
    Save alexa_cookies and/or amazon_cookies to persistent storage.
    Only the fields provided are written — omitting a field leaves its
    existing cookie file untouched.
    """
    if body.alexa_cookies is None and body.amazon_cookies is None:
        raise HTTPException(
            400,
            "Provide at least one of: alexa_cookies (for Tasks) or "
            "amazon_cookies (for Shopping List).",
        )

    data_dir = os.path.dirname(api_config.COOKIE_PATH)
    try:
        os.makedirs(data_dir, exist_ok=True)
    except OSError as exc:
        raise HTTPException(500, f"Could not create data directory: {exc}")

    saved: List[str] = []

    if body.alexa_cookies is not None:
        cookies_list = [c.model_dump(exclude_unset=True) for c in body.alexa_cookies]
        try:
            with open(api_config.COOKIE_PATH, "w", encoding="utf-8") as f:
                json.dump(cookies_list, f, indent=2)
            logger.info("Saved %d alexa_cookies to %s", len(cookies_list), api_config.COOKIE_PATH)
            saved.append(f"{len(cookies_list)} alexa_cookies")
        except Exception as exc:
            raise HTTPException(500, f"Failed to save alexa_cookies: {exc}")

    if body.amazon_cookies is not None:
        cookies_list = [c.model_dump(exclude_unset=True) for c in body.amazon_cookies]
        names = {c["name"] for c in cookies_list}
        if "aws-waf-token" not in names:
            logger.warning(
                "aws-waf-token not found in submitted amazon_cookies. "
                "Shopping list requests will likely fail."
            )
        try:
            with open(api_config.AMAZON_COOKIE_PATH, "w", encoding="utf-8") as f:
                json.dump(cookies_list, f, indent=2)
            logger.info(
                "Saved %d amazon_cookies to %s", len(cookies_list), api_config.AMAZON_COOKIE_PATH
            )
            saved.append(f"{len(cookies_list)} amazon_cookies")
        except Exception as exc:
            raise HTTPException(500, f"Failed to save amazon_cookies: {exc}")

    return {"message": f"Saved: {', '.join(saved)}"}


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
