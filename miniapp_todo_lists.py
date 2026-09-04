"""Habitica-tag-backed Todo list rules for the Telegram Mini App.

Lists are metadata only: every user-created list is one ordinary Habitica tag
whose name starts with :data:`LIST_TAG_PREFIX`.  This module contains no HTTP
or Flask code so ownership checks and task-tag preservation are easy to test.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from miniapp_tasks import TASK_ID_RE


LIST_TAG_PREFIX = "list:"
MAX_LIST_NAME_LENGTH = 80


class TodoListValidationError(ValueError):
    """Raised when list metadata from the browser is invalid."""


def validate_list_name(value: Any) -> str:
    """Return one trimmed friendly list name suitable for a Habitica tag."""

    if not isinstance(value, str):
        raise TodoListValidationError("list name must be text")
    name = value.strip()
    if (
        not name
        or len(name) > MAX_LIST_NAME_LENGTH
        or any(ord(character) < 32 or ord(character) == 127 for character in name)
    ):
        raise TodoListValidationError("list name is invalid")
    return name


def validate_list_id(value: Any, *, optional: bool = True) -> str | None:
    """Validate a Habitica tag UUID, allowing ``None`` for Inbox."""

    if value is None and optional:
        return None
    if not isinstance(value, str) or TASK_ID_RE.fullmatch(value) is None:
        raise TodoListValidationError("list id is invalid")
    return value


def list_tag_name(name: Any) -> str:
    return f"{LIST_TAG_PREFIX}{validate_list_name(name)}"


def friendly_list_name(tag_name: Any) -> str | None:
    """Return the friendly portion of one valid list tag name."""

    if not isinstance(tag_name, str) or not tag_name.startswith(LIST_TAG_PREFIX):
        return None
    try:
        return validate_list_name(tag_name[len(LIST_TAG_PREFIX) :])
    except TodoListValidationError:
        return None


def normalize_user_tags(raw_tags: Any) -> list[dict[str, Any]] | None:
    """Whitelist ordered user tags returned by Habitica.

    Challenge/group tags remain visible as ordinary tags so a task update can
    preserve their IDs, but they are never accepted as Mini App Todo lists.
    """

    if not isinstance(raw_tags, list):
        return None
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for order, raw in enumerate(raw_tags):
        if not isinstance(raw, Mapping):
            return None
        tag_id = raw.get("id", raw.get("_id"))
        name = raw.get("name")
        if (
            not isinstance(tag_id, str)
            or TASK_ID_RE.fullmatch(tag_id) is None
            or not isinstance(name, str)
            or not name
            or tag_id in seen
        ):
            return None
        seen.add(tag_id)
        challenge = raw.get("challenge") is True
        group = isinstance(raw.get("group"), str) and bool(raw.get("group"))
        normalized.append(
            {
                "id": tag_id,
                "name": name,
                "order": order,
                "challenge": challenge,
                "group": group,
            }
        )
    return normalized


def todo_lists_from_tags(tags: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return valid user-created lists in Habitica's tag order."""

    lists: list[dict[str, Any]] = []
    for tag in tags:
        friendly = friendly_list_name(tag.get("name"))
        if friendly is None or tag.get("challenge") is True or tag.get("group") is True:
            continue
        lists.append(
            {
                "id": tag["id"],
                "name": friendly,
                "order": tag.get("order", len(lists)),
            }
        )
    return lists


def ordinary_tags_from_tags(tags: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return non-list tag metadata for read-only editor chips."""

    ordinary: list[dict[str, Any]] = []
    for tag in tags:
        if friendly_list_name(tag.get("name")) is not None and not (
            tag.get("challenge") is True or tag.get("group") is True
        ):
            continue
        ordinary.append({"id": tag["id"], "name": tag["name"], "order": tag.get("order", 0)})
    return ordinary


def find_owned_list(lists: Iterable[Mapping[str, Any]], list_id: Any) -> dict[str, Any] | None:
    """Return a copy of the owned list with ``list_id``, if present."""

    try:
        wanted = validate_list_id(list_id, optional=False)
    except TodoListValidationError:
        return None
    for todo_list in lists:
        if todo_list.get("id") == wanted:
            return dict(todo_list)
    return None


def duplicate_list_name(
    lists: Iterable[Mapping[str, Any]],
    name: Any,
    *,
    excluding_id: str | None = None,
) -> bool:
    """Compare friendly names case-insensitively after trimming."""

    wanted = validate_list_name(name).casefold()
    return any(
        todo_list.get("id") != excluding_id
        and isinstance(todo_list.get("name"), str)
        and todo_list["name"].casefold() == wanted
        for todo_list in lists
    )


def primary_list_id(task_tag_ids: Any, lists: Iterable[Mapping[str, Any]]) -> str | None:
    """Return the sole valid list assignment, otherwise Inbox (``None``)."""

    if not isinstance(task_tag_ids, list):
        return None
    valid_ids = {todo_list.get("id") for todo_list in lists}
    matches = []
    seen: set[str] = set()
    for tag_id in task_tag_ids:
        if tag_id in valid_ids and tag_id not in seen:
            seen.add(tag_id)
            matches.append(tag_id)
    return matches[0] if len(matches) == 1 else None


def tags_for_list_assignment(
    task_tag_ids: Any,
    lists: Iterable[Mapping[str, Any]],
    selected_list_id: str | None,
) -> list[str]:
    """Preserve ordinary IDs while replacing every valid list tag at save."""

    list_ids = {todo_list.get("id") for todo_list in lists}
    preserved: list[str] = []
    seen: set[str] = set()
    if isinstance(task_tag_ids, list):
        for tag_id in task_tag_ids:
            if (
                isinstance(tag_id, str)
                and TASK_ID_RE.fullmatch(tag_id)
                and tag_id not in list_ids
                and tag_id not in seen
            ):
                seen.add(tag_id)
                preserved.append(tag_id)
    if selected_list_id is not None:
        selected = find_owned_list(lists, selected_list_id)
        if selected is None:
            raise TodoListValidationError("list does not belong to user")
        preserved.append(selected_list_id)
    return preserved
