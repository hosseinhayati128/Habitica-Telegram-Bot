"""Validated, normalized task service for the Telegram Mini App.

This module is deliberately independent of Flask.  It converts the small
browser-facing task model into the exact Habitica API payloads and whitelists
the fields returned to the browser.  Habitica credentials never leave the
server-side account object.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from datetime import date
from typing import Any


TASK_TYPES = frozenset({"habit", "daily", "todo"})
UPSTREAM_LIST_TYPES = {
    ("habit", False): "habits",
    ("daily", False): "dailys",
    ("todo", False): "todos",
    ("todo", True): "completedTodos",
}
PRIORITIES = frozenset({0.1, 1.0, 1.5, 2.0})
REPEAT_DAYS = ("su", "m", "t", "w", "th", "f", "s")
TASK_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
MAX_TITLE_LENGTH = 500
MAX_NOTES_LENGTH = 10_000
MAX_CHECKLIST_ITEMS = 100
MAX_CHECKLIST_TEXT_LENGTH = 500
MAX_SAFE_COUNTER = 9_007_199_254_740_991
COUNTER_FREQUENCIES = frozenset({"daily", "weekly", "monthly"})


class TaskValidationError(ValueError):
    """Raised when a browser task request is invalid."""


def validate_task_id(value: Any) -> str:
    if not isinstance(value, str) or TASK_ID_RE.fullmatch(value) is None:
        raise TaskValidationError("invalid task id")
    return value


def validate_task_type(value: Any) -> str:
    if not isinstance(value, str) or value not in TASK_TYPES:
        raise TaskValidationError("invalid task type")
    return value


def upstream_list_type(task_type: str, *, completed: bool = False) -> str:
    validate_task_type(task_type)
    key = (task_type, completed)
    try:
        return UPSTREAM_LIST_TYPES[key]
    except KeyError as exc:
        raise TaskValidationError("completed filter is unsupported") from exc


def _string(
    value: Any,
    *,
    field: str,
    maximum: int,
    required: bool,
) -> str:
    if not isinstance(value, str):
        raise TaskValidationError(f"{field} must be text")
    normalized = value.strip() if required else value
    if required and not normalized:
        raise TaskValidationError(f"{field} is required")
    if len(normalized) > maximum:
        raise TaskValidationError(f"{field} is too long")
    return normalized


def _priority(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TaskValidationError("priority is invalid")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric not in PRIORITIES:
        raise TaskValidationError("priority is invalid")
    return numeric


def _calendar_date(value: Any, *, field: str, optional: bool) -> str | None:
    if value is None or value == "":
        if optional:
            return None
        raise TaskValidationError(f"{field} is required")
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise TaskValidationError(f"{field} is invalid")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise TaskValidationError(f"{field} is invalid") from exc
    if parsed.isoformat() != value:
        raise TaskValidationError(f"{field} is invalid")
    return value


def _checklist(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_CHECKLIST_ITEMS:
        raise TaskValidationError("checklist is invalid")
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            raise TaskValidationError("checklist is invalid")
        unknown = set(item) - {"id", "text", "completed"}
        if unknown:
            raise TaskValidationError("checklist is invalid")
        text = _string(
            item.get("text"),
            field="checklist item",
            maximum=MAX_CHECKLIST_TEXT_LENGTH,
            required=True,
        )
        completed = item.get("completed", False)
        if not isinstance(completed, bool):
            raise TaskValidationError("checklist is invalid")
        entry: dict[str, Any] = {"text": text, "completed": completed}
        item_id = item.get("id")
        if item_id is not None:
            item_id = validate_task_id(item_id)
            if item_id in seen_ids:
                raise TaskValidationError("checklist is invalid")
            seen_ids.add(item_id)
            entry["id"] = item_id
        normalized.append(entry)
    return normalized


def validate_task_payload(
    value: Any,
    *,
    creating: bool,
    existing_type: str | None = None,
) -> tuple[str, dict[str, Any], list[dict[str, Any]] | None]:
    """Validate a create/update body and return type, task fields, checklist.

    Checklist edits are reconciled through the dedicated checklist endpoints,
    so the returned checklist remains separate from the ordinary PUT payload.
    """

    if not isinstance(value, Mapping):
        raise TaskValidationError("request body is invalid")
    common_allowed = {
        "type",
        "text",
        "notes",
        "priority",
        "revision",
    }
    allowed_by_type = {
        "habit": common_allowed | {"up", "down"},
        "daily": common_allowed | {"repeatDays", "startDate", "checklist"},
        "todo": common_allowed | {"date", "checklist"},
    }
    if set(value) - set().union(*allowed_by_type.values()):
        raise TaskValidationError("request body has unsupported fields")

    raw_type = value.get("type", existing_type)
    task_type = validate_task_type(raw_type)
    if existing_type is not None and task_type != existing_type:
        raise TaskValidationError("task type cannot be changed")
    if set(value) - allowed_by_type[task_type]:
        raise TaskValidationError("request body has fields for another task type")
    if creating and "revision" in value:
        raise TaskValidationError("new tasks do not have revisions")
    if not creating:
        revision = value.get("revision")
        if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{32}", revision):
            raise TaskValidationError("task revision is required")
    required_common = creating
    fields: dict[str, Any] = {}
    for field, maximum in (
        ("text", MAX_TITLE_LENGTH),
        ("notes", MAX_NOTES_LENGTH),
    ):
        if field in value or required_common:
            default = "" if field == "notes" else None
            fields[field] = _string(
                value.get(field, default),
                field="title" if field == "text" else field,
                maximum=maximum,
                required=field == "text",
            )
    if "priority" in value or required_common:
        fields["priority"] = _priority(value.get("priority", 1))

    checklist = _checklist(value.get("checklist")) if "checklist" in value else None
    if task_type == "habit":
        up = value.get("up", True)
        down = value.get("down", True)
        if not isinstance(up, bool) or not isinstance(down, bool) or not (up or down):
            raise TaskValidationError("a Habit needs a scoring direction")
        if creating or "up" in value:
            fields["up"] = up
        if creating or "down" in value:
            fields["down"] = down
    elif task_type == "daily":
        if "date" in value:
            raise TaskValidationError("Dailies do not support due dates")
        if "repeatDays" in value or creating:
            repeat_days = value.get("repeatDays", list(REPEAT_DAYS))
            if (
                not isinstance(repeat_days, list)
                or not repeat_days
                or any(day not in REPEAT_DAYS for day in repeat_days)
                or len(set(repeat_days)) != len(repeat_days)
            ):
                raise TaskValidationError("repeat days are invalid")
            selected = set(repeat_days)
            fields.update(
                {
                    "frequency": "weekly",
                    "everyX": 1,
                    "repeat": {day: day in selected for day in REPEAT_DAYS},
                }
            )
        if "startDate" in value:
            fields["startDate"] = _calendar_date(
                value.get("startDate"), field="start date", optional=False
            )
    else:
        if "repeatDays" in value or "startDate" in value:
            raise TaskValidationError("Todos do not support repeat fields")
        if "date" in value:
            fields["date"] = _calendar_date(value.get("date"), field="due date", optional=True)

    if creating:
        fields["type"] = task_type
        if checklist is not None:
            fields["checklist"] = [
                {"text": item["text"], "completed": item["completed"]} for item in checklist
            ]
    elif not fields and checklist is None:
        raise TaskValidationError("no task changes were provided")
    return task_type, fields, checklist


def _safe_number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        finite = math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        return None
    if not finite:
        return None
    return value


def _safe_habit_counter(value: Any) -> int:
    """Return one non-negative, browser-safe Habit counter.

    Habitica models counters as integers.  Missing legacy fields and malformed
    upstream values are presentation-safe zeroes; negative, fractional, and
    values outside JavaScript's exact integer range are never exposed.
    """

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    try:
        finite = math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        return 0
    if not finite or value < 0 or value > MAX_SAFE_COUNTER:
        return 0
    integer = int(value)
    return integer if integer == value else 0


def _habit_counter_frequency(value: Any) -> str:
    # Habitica's model default is daily.  Preserve that semantic for legacy
    # responses that omit the field while refusing arbitrary labels.
    return value if isinstance(value, str) and value in COUNTER_FREQUENCIES else "daily"


def task_color_token(value: Any) -> str:
    """Return Habitica's task-value color name, with a safe neutral fallback."""

    numeric = _safe_number(value)
    if numeric is None:
        return "neutral"
    if numeric < -20:
        return "worst"
    if numeric < -10:
        return "worse"
    if numeric < -1:
        return "bad"
    if numeric < 1:
        return "neutral"
    if numeric < 5:
        return "good"
    if numeric < 10:
        return "better"
    return "best"


def _normalized_date(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    prefix = value[:10]
    try:
        return date.fromisoformat(prefix).isoformat()
    except ValueError:
        return None


def _literal_bool(raw: Mapping[str, Any], key: str, *, default: bool) -> bool | None:
    if key not in raw:
        return default
    value = raw.get(key)
    return value if isinstance(value, bool) else None


def task_revision(task: Mapping[str, Any]) -> str:
    """Return a compact ETag-like revision for safe edit preconditions."""

    revision_fields = {
        "id": task.get("id"),
        "type": task.get("type"),
        "text": task.get("text"),
        "notes": task.get("notes"),
        "priority": task.get("priority"),
        "up": task.get("up"),
        "down": task.get("down"),
        "completed": task.get("completed"),
        "date": task.get("date"),
        "startDate": task.get("startDate"),
        "frequency": task.get("frequency"),
        "everyX": task.get("everyX"),
        "repeatDays": task.get("repeatDays"),
        "checklist": task.get("checklist"),
    }
    serialized = json.dumps(
        revision_fields,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()[:32]


def normalize_task(raw: Any) -> dict[str, Any] | None:
    """Whitelist one Habitica task for the browser."""

    if not isinstance(raw, Mapping):
        return None
    task_id = raw.get("id", raw.get("_id"))
    task_type = raw.get("type")
    text = raw.get("text")
    if (
        not isinstance(task_id, str)
        or TASK_ID_RE.fullmatch(task_id) is None
        or task_type not in TASK_TYPES
        or not isinstance(text, str)
    ):
        return None
    notes = raw.get("notes")
    if notes is not None and not isinstance(notes, str):
        return None
    challenge = raw.get("challenge")
    group = raw.get("group")
    challenge_linked = isinstance(challenge, Mapping) and bool(challenge)
    group_task = isinstance(group, Mapping) and bool(group.get("id"))
    up = _literal_bool(raw, "up", default=True) if task_type == "habit" else False
    down = _literal_bool(raw, "down", default=True) if task_type == "habit" else False
    completed = _literal_bool(raw, "completed", default=False) if task_type != "habit" else False
    due_today = _literal_bool(raw, "isDue", default=False) if task_type == "daily" else False
    if up is None or down is None or completed is None or due_today is None:
        return None

    text_truncated = len(text) > MAX_TITLE_LENGTH
    notes_value = notes if isinstance(notes, str) else ""
    notes_truncated = len(notes_value) > MAX_NOTES_LENGTH
    frequency = raw.get("frequency") if task_type == "daily" else None
    every_x = raw.get("everyX") if task_type == "daily" else None
    schedule_editable = (
        task_type == "daily"
        and frequency == "weekly"
        and not isinstance(every_x, bool)
        and every_x == 1
    )
    value = _safe_number(raw.get("value"))

    task = {
        "id": task_id,
        "type": task_type,
        "text": text[:MAX_TITLE_LENGTH],
        "textTruncated": text_truncated,
        "notes": notes_value[:MAX_NOTES_LENGTH],
        "notesTruncated": notes_truncated,
        "priority": _safe_number(raw.get("priority")),
        "value": value,
        "colorToken": task_color_token(value),
        "up": up,
        "down": down,
        "counterUp": _safe_habit_counter(raw.get("counterUp"))
        if task_type == "habit"
        else None,
        "counterDown": _safe_habit_counter(raw.get("counterDown"))
        if task_type == "habit"
        else None,
        "counterFrequency": _habit_counter_frequency(raw.get("frequency"))
        if task_type == "habit"
        else None,
        "completed": completed,
        "dueToday": due_today,
        "streak": _safe_number(raw.get("streak")) if task_type == "daily" else None,
        "date": _normalized_date(raw.get("date")) if task_type == "todo" else None,
        "dateCompleted": _normalized_date(raw.get("dateCompleted"))
        if task_type == "todo"
        else None,
        "startDate": _normalized_date(raw.get("startDate")) if task_type == "daily" else None,
        "repeatDays": [
            day
            for day in REPEAT_DAYS
            if task_type == "daily"
            and isinstance(raw.get("repeat"), Mapping)
            and raw["repeat"].get(day) is True
        ],
        "frequency": frequency if isinstance(frequency, str) else None,
        "everyX": _safe_number(every_x),
        "scheduleEditable": schedule_editable,
        "checklist": [],
        "canEdit": not challenge_linked
        and not group_task
        and not text_truncated
        and not notes_truncated,
        "canDelete": not challenge_linked and not group_task,
    }
    raw_checklist = raw.get("checklist")
    if task_type in {"daily", "todo"} and isinstance(raw_checklist, list):
        for item in raw_checklist[:MAX_CHECKLIST_ITEMS]:
            if not isinstance(item, Mapping):
                continue
            item_id = item.get("id")
            item_text = item.get("text")
            if (
                isinstance(item_id, str)
                and TASK_ID_RE.fullmatch(item_id)
                and isinstance(item_text, str)
            ):
                completed_value = _literal_bool(item, "completed", default=False)
                if completed_value is None:
                    return None
                item_text_truncated = len(item_text) > MAX_CHECKLIST_TEXT_LENGTH
                task["checklist"].append(
                    {
                        "id": item_id,
                        "text": item_text[:MAX_CHECKLIST_TEXT_LENGTH],
                        "textTruncated": item_text_truncated,
                        "completed": completed_value,
                    }
                )
                if item_text_truncated:
                    task["canEdit"] = False
    task["revision"] = task_revision(task)
    return task


def normalize_tasks(raw_tasks: Any) -> list[dict[str, Any]] | None:
    if not isinstance(raw_tasks, list):
        return None
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_tasks:
        task = normalize_task(raw)
        if task is None:
            return None
        if task["id"] not in seen:
            seen.add(task["id"])
            normalized.append(task)
    return normalized


def checklist_changes(
    existing: list[dict[str, Any]], requested: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Return checklist additions, updates, and removals."""

    old = {item["id"]: item for item in existing if isinstance(item.get("id"), str)}
    requested_ids = {item["id"] for item in requested if "id" in item}
    unknown = requested_ids - set(old)
    if unknown:
        raise TaskValidationError("checklist contains an unknown item")
    additions = [item for item in requested if "id" not in item]
    updates = [
        item for item in requested if "id" in item and (item["text"] != old[item["id"]].get("text"))
    ]
    removals = [item_id for item_id in old if item_id not in requested_ids]
    return additions, updates, removals


def optimistic_scored_task(task: Mapping[str, Any], direction: str) -> dict[str, Any] | None:
    """Return the smallest safe post-score model when reconciliation fails.

    Habitica confirms a score with user statistics rather than the task.  For
    Dailies and Todos, a confirmed direction determines completion state.  A
    Habit's value is deliberately left unchanged until a later safe refresh.
    """

    if direction not in {"up", "down"}:
        return None
    normalized = dict(task)
    task_type = normalized.get("type")
    if task_type in {"daily", "todo"}:
        normalized["completed"] = direction == "up"
        if task_type == "todo" and direction == "down":
            normalized["dateCompleted"] = None
    elif task_type == "habit":
        counter_name = "counterUp" if direction == "up" else "counterDown"
        current_counter = _safe_habit_counter(normalized.get(counter_name))
        normalized[counter_name] = min(current_counter + 1, MAX_SAFE_COUNTER)
    else:
        return None
    return normalized
