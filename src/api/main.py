"""FastAPI server — Alexa Tasks + Named Lists (Alexa+).

New endpoints:
  Tasks:  GET/POST /tasks, GET/PUT/DELETE /tasks/{id}, POST /tasks/{id}/complete
  Lists:  GET /lists, GET/POST /lists/{name}/items, DELETE /lists/{name}/items/{item_id}

Legacy endpoints retained for backward compat:
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

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------
try:
    from . import config as api_config
    from .alexa_api import (
        # Tasks
        get_tasks,
        create_task,
        update_task,
        complete_task,
        delete_task,
        # Lists
        get_lists,
        get_list_items,
        add_list_item,
        remove_list_item,
        # Backward-compat shopping helpers
        get_shopping_list_items,
        add_shopping_list_item,
        delete_shopping_list_item,
        mark_item_as_completed,
        unmark_item_as_completed,
        filter_incomplete_items,
        _probe_list_endpoints,
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
# Scheduler — keep-alive + list endpoint probe
# ---------------------------------------------------------------------------
scheduler = AsyncIOScheduler()


async def _keep_alive():
    """Periodic keep-alive: fetch tasks to maintain session."""
    if not os.path.exists(api_config.COOKIE_PATH):
        logger.info("Keep-alive skipped: no cookie file")
        return
    try:
        tasks = get_tasks()
        if tasks is not None:
            logger.info("Keep-alive OK: %d tasks", len(tasks))
        else:
            logger.warning("Keep-alive failed: cookies may be expired")
    except Exception as exc:
        logger.error("Keep-alive error: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load persisted device config
    api_config.load_device_config()

    # Attempt to discover list endpoint at startup (non-blocking — just logs)
    if os.path.exists(api_config.COOKIE_PATH):
        logger.info("Probing list endpoints at startup...")
        _probe_list_endpoints()

    scheduler.add_job(_keep_alive, "interval", seconds=60, id="keep_alive")
    scheduler.start()
    yield
    scheduler.shutdown()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Alexa API (Alexa+)",
    description="Tasks and Named Lists via the new alexa.amazon.com interface.",
    version="2.0.0",
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


class ListItemModel(BaseModel):
    value: str = Field(..., description="Item text")


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


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def find_item_by_name(
    items: List[Dict[str, Any]], name: str
) -> Optional[Dict[str, Any]]:
    if not items:
        return None
    for item in items:
        if (item.get("value") or "").lower() == name.lower():
            return item
    return None


def _task_id(task: Dict[str, Any]) -> str:
    """Extract the canonical task identifier."""
    return task.get("notificationIndex") or task.get("id") or ""


# ===========================================================================
# ROOT
# ===========================================================================

@app.get("/", tags=["Status"])
async def read_root():
    return {"status": "Alexa API (Alexa+) is running", "version": "2.0.0"}


# ===========================================================================
# TASKS
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
    """Update a task's title (fetches current task, patches reminderLabel)."""
    tasks = get_tasks()
    if tasks is None:
        raise HTTPException(503, "Could not retrieve tasks")
    task = next((t for t in tasks if _task_id(t) == task_id), None)
    if task is None:
        raise HTTPException(404, f"Task '{task_id}' not found")
    patched = dict(task)
    patched["reminderLabel"] = body.title
    success = update_task(task_id, patched)
    if not success:
        raise HTTPException(500, f"Failed to update task '{task_id}'")
    return {"message": f"Task '{task_id}' updated"}


@app.delete("/tasks/{task_id}", tags=["Tasks"])
async def delete_task_endpoint(task_id: str):
    """Delete a task entirely."""
    success = delete_task(task_id)
    if not success:
        raise HTTPException(500, f"Failed to delete task '{task_id}'")
    return {"message": f"Task '{task_id}' deleted"}


@app.post("/tasks/{task_id}/complete", tags=["Tasks"])
async def complete_task_endpoint(task_id: str):
    """Mark a task as complete (sets status='OFF')."""
    success = complete_task(task_id)
    if not success:
        raise HTTPException(500, f"Failed to complete task '{task_id}'")
    return {"message": f"Task '{task_id}' marked complete"}


# ===========================================================================
# LISTS
# ===========================================================================

@app.get("/lists", tags=["Lists"])
async def list_all_lists():
    """Return all named lists with their item counts."""
    lists = get_lists()
    if lists is None:
        raise HTTPException(503, "Could not retrieve lists from Alexa. Cookies may be missing or expired.")
    return lists


@app.get("/lists/{list_name}/items", tags=["Lists"])
async def get_items_for_list(list_name: str):
    """Return all items in the named list."""
    items = get_list_items(list_name)
    if items is None:
        raise HTTPException(404, f"List '{list_name}' not found or could not retrieve items")
    return items


@app.post("/lists/{list_name}/items", tags=["Lists"], status_code=201)
async def add_item_to_list(list_name: str, body: ListItemModel):
    """Add an item to the named list."""
    success = add_list_item(list_name, body.value)
    if not success:
        raise HTTPException(500, f"Failed to add item to list '{list_name}'")
    return {"message": f"Item '{body.value}' added to list '{list_name}'"}


@app.delete("/lists/{list_name}/items/{item_id}", tags=["Lists"])
async def remove_item_from_list(list_name: str, item_id: str):
    """Remove an item from the named list by item ID."""
    success = remove_list_item(list_name, item_id)
    if not success:
        raise HTTPException(500, f"Failed to remove item '{item_id}' from list '{list_name}'")
    return {"message": f"Item '{item_id}' removed from list '{list_name}'"}


# ===========================================================================
# LEGACY — /items/* (backward compat)
# ===========================================================================

@app.get("/items/all", tags=["Legacy-Shopping"], response_model=List[Dict[str, Any]])
async def get_all_list_items():
    items = get_shopping_list_items()
    if items is None:
        raise HTTPException(503, "Could not retrieve shopping list from Alexa")
    return items


@app.get("/items/incomplete", tags=["Legacy-Shopping"], response_model=List[Dict[str, Any]])
async def get_incomplete_list_items():
    items = get_shopping_list_items()
    if items is None:
        raise HTTPException(503, "Could not retrieve shopping list from Alexa")
    return filter_incomplete_items(items)


@app.get("/items/completed", tags=["Legacy-Shopping"], response_model=List[Dict[str, Any]])
async def get_completed_list_items():
    items = get_shopping_list_items()
    if items is None:
        raise HTTPException(503, "Could not retrieve shopping list from Alexa")
    return [item for item in items if item.get("completed", False)]


@app.post("/items", tags=["Legacy-Shopping"], status_code=201)
async def add_new_item(item_data: ItemNameModel):
    success = add_shopping_list_item(item_data.item_name)
    if not success:
        raise HTTPException(500, f"Failed to add item '{item_data.item_name}'")
    return {"message": f"Item '{item_data.item_name}' added successfully"}


@app.delete("/items", tags=["Legacy-Shopping"])
async def remove_item(item_data: ItemNameModel):
    items = get_shopping_list_items()
    item = find_item_by_name(items or [], item_data.item_name)
    if not item:
        raise HTTPException(404, f"Item '{item_data.item_name}' not found")
    success = delete_shopping_list_item(item)
    if not success:
        raise HTTPException(500, f"Failed to delete item '{item_data.item_name}'")
    return {"message": f"Item '{item_data.item_name}' deleted successfully"}


@app.put("/items/mark_completed", tags=["Legacy-Shopping"])
async def mark_item_complete(item_data: ItemNameModel):
    items = get_shopping_list_items()
    item = find_item_by_name(filter_incomplete_items(items or []), item_data.item_name)
    if not item:
        raise HTTPException(404, f"Incomplete item '{item_data.item_name}' not found")
    success = mark_item_as_completed(item)
    if not success:
        raise HTTPException(500, f"Failed to mark '{item_data.item_name}' completed")
    return {"message": f"Item '{item_data.item_name}' marked as completed"}


@app.put("/items/mark_incomplete", tags=["Legacy-Shopping"])
async def mark_item_incomplete_endpoint(item_data: ItemNameModel):
    items = get_shopping_list_items()
    completed = [i for i in (items or []) if i.get("completed", False)]
    item = find_item_by_name(completed, item_data.item_name)
    if not item:
        raise HTTPException(404, f"Completed item '{item_data.item_name}' not found")
    success = unmark_item_as_completed(item)
    if not success:
        raise HTTPException(500, f"Failed to mark '{item_data.item_name}' incomplete")
    return {"message": f"Item '{item_data.item_name}' marked as incomplete"}


# ===========================================================================
# AUTH — cookie ingestion
# ===========================================================================

@app.post("/auth/cookies", tags=["Authentication"], status_code=200)
async def receive_cookies(cookies_data: List[CookieModel]):
    """Accept cookies as JSON and save them to the persistent data volume."""
    cookie_path = api_config.COOKIE_PATH
    data_dir = os.path.dirname(cookie_path)

    try:
        os.makedirs(data_dir, exist_ok=True)
    except OSError as exc:
        raise HTTPException(500, f"Could not create data directory: {exc}")

    try:
        cookies_list = [c.model_dump(exclude_unset=True) for c in cookies_data]
        with open(cookie_path, "w", encoding="utf-8") as f:
            json.dump(cookies_list, f, indent=2)
        logger.info("Saved %d cookies to %s", len(cookies_list), cookie_path)

        # Trigger list endpoint probe now that cookies are available
        logger.info("Probing list endpoints after cookie update...")
        _probe_list_endpoints()

        return {"message": f"Saved {len(cookies_list)} cookies successfully"}
    except Exception as exc:
        logger.error("Failed to save cookies: %s", exc, exc_info=True)
        raise HTTPException(500, f"Failed to save cookie data: {exc}")


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
