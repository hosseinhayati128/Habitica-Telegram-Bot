"""Small, synchronous client helpers for the Habitica API.

The historical public functions in this module retain their ``None``/``False``
sentinel contracts.  New Mini App task helpers return :class:`HabiticaResult`
so callers can safely distinguish upstream failures without credentials or raw
response bodies being written to logs.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from enum import Enum
import logging
import math
import os
import re
from typing import Any, Dict, Generic, Optional, TypeVar
from urllib.parse import quote

import requests


BASE_URL = "https://habitica.com/api/v3"
# Retained as a compatibility export for code that imported the historical
# constant. It is not a valid third-party X-Client value and is never sent.
CLIENT_ID = "habitica-telegram-bot"
MAX_RETRY_AFTER_SECONDS = 3600.0
HABITICA_CLIENT_ID_PATTERN = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}-[A-Za-z0-9][A-Za-z0-9._-]{0,99}"
)

# A tuple gives requests separate connection and response-read limits.  These
# helpers remain synchronous, so bounding both phases is particularly
# important when a caller delegates them from an async Telegram handler.
REQUEST_TIMEOUT = (5.0, 30.0)

logger = logging.getLogger(__name__)

T = TypeVar("T")


class HabiticaErrorKind(str, Enum):
    """Stable failure classes for callers that must map API errors to HTTP."""

    INVALID_INPUT = "invalid_input"
    CONFIGURATION_ERROR = "configuration_error"
    BAD_REQUEST = "bad_request"
    INVALID_CREDENTIALS = "invalid_credentials"
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    RATE_LIMITED = "rate_limited"
    REQUEST_TIMEOUT = "request_timeout"
    NETWORK_ERROR = "network_error"
    UPSTREAM_ERROR = "upstream_error"
    HTTP_ERROR = "http_error"
    INVALID_RESPONSE = "invalid_response"


class HabiticaConfigurationError(RuntimeError):
    """Raised internally when the required public X-Client ID is unavailable."""


@dataclass(frozen=True)
class HabiticaAPIError:
    """A sanitized Habitica failure safe to pass between application layers.

    ``outcome_unknown`` is true only when a mutation might have reached
    Habitica but its final state could not be confirmed.  Callers must
    reconcile with a safe read before offering the same mutation again.
    """

    kind: HabiticaErrorKind
    status: Optional[int] = None
    retry_after: Optional[float] = None
    outcome_unknown: bool = False


@dataclass(frozen=True)
class HabiticaResult(Generic[T]):
    """Typed result returned by the Mini App task API helpers."""

    data: Optional[T] = None
    status: Optional[int] = None
    error: Optional[HabiticaAPIError] = None

    @property
    def ok(self) -> bool:
        """Return whether the request produced validated Habitica data."""
        return self.error is None


TASK_LIST_TYPES = frozenset(("habits", "dailys", "todos", "rewards", "completedTodos"))
CONTENT_LANGUAGES = frozenset(
    (
        "bg",
        "cs",
        "da",
        "de",
        "en",
        "en@pirate",
        "en_GB",
        "es",
        "es_419",
        "fr",
        "he",
        "hu",
        "id",
        "it",
        "ja",
        "nl",
        "pl",
        "pt",
        "pt_BR",
        "ro",
        "ru",
        "sk",
        "sr",
        "sv",
        "uk",
        "zh",
        "zh_TW",
    )
)


def _configured_client_id() -> str:
    """Return the required compliant X-Client value without exposing it in errors."""
    configured = os.environ.get("HABITICA_CLIENT_ID", "").strip()
    if not HABITICA_CLIENT_ID_PATTERN.fullmatch(configured):
        raise HabiticaConfigurationError(
            "HABITICA_CLIENT_ID is unavailable or malformed; expected "
            "<Habitica-UUID>-<app-name>"
        )
    return configured


def _headers(user_id: str, api_key: str) -> dict:
    """Return the canonical per-request Habitica headers."""
    return {
        "x-api-user": user_id,
        "x-api-key": api_key,
        "x-client": _configured_client_id(),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _parse_retry_after(response: requests.Response) -> Optional[float]:
    """Parse Habitica's Retry-After header as non-negative seconds."""
    headers = getattr(response, "headers", None) or {}
    value = headers.get("Retry-After")
    if value is None:
        value = headers.get("retry-after")
    if value is None:
        return None

    try:
        seconds = float(value)
    except (TypeError, ValueError):
        try:
            retry_at = parsedate_to_datetime(str(value))
        except (TypeError, ValueError, OverflowError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        seconds = (retry_at - datetime.now(timezone.utc)).total_seconds()

    if not math.isfinite(seconds):
        return None
    return min(max(seconds, 0.0), MAX_RETRY_AFTER_SECONDS)


def _typed_failure(
    method: str,
    kind: HabiticaErrorKind,
    *,
    status: Optional[int] = None,
    retry_after: Optional[float] = None,
    outcome_unknown: bool = False,
) -> HabiticaResult[Any]:
    """Build and safely log a typed failure without response or request data."""
    logger.warning(
        "Habitica task request failed method=%s status=%s kind=%s",
        method,
        status,
        kind.value,
    )
    return HabiticaResult(
        status=status,
        error=HabiticaAPIError(
            kind=kind,
            status=status,
            retry_after=retry_after,
            outcome_unknown=outcome_unknown,
        ),
    )


def _classify_http_error(
    method: str,
    status: int,
    payload: Any,
    *,
    retry_after: Optional[float],
    mutation: bool,
) -> HabiticaResult[Any]:
    """Map an HTTP failure without trusting or exposing its message text."""
    upstream_error = payload.get("error") if isinstance(payload, dict) else None

    if upstream_error == "invalid_credentials":
        kind = HabiticaErrorKind.INVALID_CREDENTIALS
    elif status in (400, 422):
        kind = HabiticaErrorKind.BAD_REQUEST
    elif status == 401:
        kind = HabiticaErrorKind.UNAUTHORIZED
    elif status == 403:
        kind = HabiticaErrorKind.FORBIDDEN
    elif status == 404:
        kind = HabiticaErrorKind.NOT_FOUND
    elif status == 408:
        kind = HabiticaErrorKind.REQUEST_TIMEOUT
    elif status == 409:
        kind = HabiticaErrorKind.CONFLICT
    elif status == 429:
        kind = HabiticaErrorKind.RATE_LIMITED
    elif status >= 500:
        kind = HabiticaErrorKind.UPSTREAM_ERROR
    else:
        kind = HabiticaErrorKind.HTTP_ERROR

    # A server timeout or 5xx can occur after a mutation has been applied.
    outcome_unknown = mutation and (status == 408 or status >= 500)
    return _typed_failure(
        method,
        kind,
        status=status,
        retry_after=retry_after,
        outcome_unknown=outcome_unknown,
    )


def _task_api_request(
    method: str,
    url: str,
    user_id: str,
    api_key: str,
    *,
    expected_data_type: type,
    mutation: bool = False,
    list_items_are_objects: bool = False,
    **kwargs: Any,
) -> HabiticaResult[Any]:
    """Make one typed task request and validate its Habitica envelope.

    This transport never retries.  In particular, retrying a score or
    checklist toggle after a lost response can apply it twice.
    """
    method = method.upper()
    full_url = f"{BASE_URL}{url}"
    try:
        kwargs["headers"] = _headers(user_id, api_key)
    except HabiticaConfigurationError:
        return _typed_failure(method, HabiticaErrorKind.CONFIGURATION_ERROR)
    kwargs["timeout"] = REQUEST_TIMEOUT
    # Never forward Habitica credentials to a redirect target.  Redirects are
    # unexpected for these fixed API endpoints and must be handled as a
    # classified HTTP response rather than followed by requests.
    kwargs["allow_redirects"] = False

    try:
        response = requests.request(method, full_url, **kwargs)
    except requests.exceptions.Timeout:
        return _typed_failure(
            method,
            HabiticaErrorKind.REQUEST_TIMEOUT,
            outcome_unknown=mutation,
        )
    except requests.exceptions.RequestException:
        return _typed_failure(
            method,
            HabiticaErrorKind.NETWORK_ERROR,
            outcome_unknown=mutation,
        )

    status = getattr(response, "status_code", None)
    if not isinstance(status, int):
        return _typed_failure(
            method,
            HabiticaErrorKind.INVALID_RESPONSE,
            outcome_unknown=mutation,
        )

    retry_after = _parse_retry_after(response)
    if not 200 <= status < 300:
        try:
            error_payload = response.json()
        except ValueError:
            error_payload = None
        return _classify_http_error(
            method,
            status,
            error_payload,
            retry_after=retry_after,
            mutation=mutation,
        )

    try:
        payload = response.json()
    except ValueError:
        return _typed_failure(
            method,
            HabiticaErrorKind.INVALID_RESPONSE,
            status=status,
            outcome_unknown=mutation,
        )

    if not isinstance(payload, dict) or payload.get("success") is not True:
        return _typed_failure(
            method,
            HabiticaErrorKind.INVALID_RESPONSE,
            status=status,
            outcome_unknown=mutation,
        )

    data = payload.get("data")
    if not isinstance(data, expected_data_type):
        return _typed_failure(
            method,
            HabiticaErrorKind.INVALID_RESPONSE,
            status=status,
            outcome_unknown=mutation,
        )
    if list_items_are_objects and not all(isinstance(item, dict) for item in data):
        return _typed_failure(
            method,
            HabiticaErrorKind.INVALID_RESPONSE,
            status=status,
            outcome_unknown=mutation,
        )

    return HabiticaResult(data=data, status=status)


def _quoted_identifier(identifier: str) -> Optional[str]:
    """Quote an untrusted identifier as exactly one URL path segment."""
    if (
        not isinstance(identifier, str)
        or not identifier.strip()
        or identifier in (".", "..")
        or any(ord(character) < 32 or ord(character) == 127 for character in identifier)
    ):
        return None
    return quote(identifier, safe="")


def _invalid_input(method: str) -> HabiticaResult[Any]:
    return _typed_failure(method.upper(), HabiticaErrorKind.INVALID_INPUT)


def get_tasks_result(
    user_id: str,
    api_key: str,
    task_type: str,
    *,
    history: bool = False,
) -> HabiticaResult[list[dict]]:
    """Fetch one official plural task type for the Mini App."""
    if task_type not in TASK_LIST_TYPES or not isinstance(history, bool):
        return _invalid_input("GET")
    return _task_api_request(
        "GET",
        "/tasks/user",
        user_id,
        api_key,
        expected_data_type=list,
        list_items_are_objects=True,
        params={"type": task_type, "history": str(history).lower()},
    )


def get_user_result(user_id: str, api_key: str) -> HabiticaResult[dict]:
    """Fetch the authenticated Habitica user through the typed boundary."""
    return _task_api_request(
        "GET",
        "/user",
        user_id,
        api_key,
        expected_data_type=dict,
    )


def get_tags_result(user_id: str, api_key: str) -> HabiticaResult[list[dict]]:
    """Fetch the authenticated user's ordered Habitica tags."""
    return _task_api_request(
        "GET",
        "/tags",
        user_id,
        api_key,
        expected_data_type=list,
        list_items_are_objects=True,
    )


def create_tag_result(
    user_id: str,
    api_key: str,
    name: str,
) -> HabiticaResult[dict]:
    """Create one Habitica tag without retrying the mutation."""
    if not isinstance(name, str) or not name:
        return _invalid_input("POST")
    return _task_api_request(
        "POST",
        "/tags",
        user_id,
        api_key,
        expected_data_type=dict,
        mutation=True,
        json={"name": name},
    )


def update_tag_result(
    user_id: str,
    api_key: str,
    tag_id: str,
    name: str,
) -> HabiticaResult[dict]:
    """Rename one owned Habitica tag."""
    quoted_tag_id = _quoted_identifier(tag_id)
    if quoted_tag_id is None or not isinstance(name, str) or not name:
        return _invalid_input("PUT")
    return _task_api_request(
        "PUT",
        f"/tags/{quoted_tag_id}",
        user_id,
        api_key,
        expected_data_type=dict,
        mutation=True,
        json={"name": name},
    )


def delete_tag_result(
    user_id: str,
    api_key: str,
    tag_id: str,
) -> HabiticaResult[dict]:
    """Delete one Habitica tag; Habitica removes it from tasks atomically."""
    quoted_tag_id = _quoted_identifier(tag_id)
    if quoted_tag_id is None:
        return _invalid_input("DELETE")
    return _task_api_request(
        "DELETE",
        f"/tags/{quoted_tag_id}",
        user_id,
        api_key,
        expected_data_type=dict,
        mutation=True,
    )


def get_content_result(
    user_id: str,
    api_key: str,
    *,
    language: str = "en",
) -> HabiticaResult[dict]:
    """Fetch translated Habitica content used for authoritative item metadata."""
    if language not in CONTENT_LANGUAGES:
        return _invalid_input("GET")
    return _task_api_request(
        "GET",
        "/content",
        user_id,
        api_key,
        expected_data_type=dict,
        params={"language": language},
    )


def buy_health_potion_result(
    user_id: str,
    api_key: str,
) -> HabiticaResult[dict]:
    """Buy and immediately use one Health Potion exactly once.

    A lost or malformed response has an unknown mutation outcome.  Callers
    must reconcile with :func:`get_user_result` instead of retrying the POST.
    """
    return _task_api_request(
        "POST",
        "/user/buy-health-potion",
        user_id,
        api_key,
        expected_data_type=dict,
        mutation=True,
    )


def run_cron_result(user_id: str, api_key: str) -> HabiticaResult[dict]:
    """Run Habitica cron once and return its validated data object."""
    return _task_api_request(
        "POST",
        "/cron",
        user_id,
        api_key,
        expected_data_type=dict,
        mutation=True,
    )


def create_task_result(
    user_id: str,
    api_key: str,
    task: Mapping[str, Any],
) -> HabiticaResult[dict]:
    """Create one task and return the task object from a 2xx response."""
    if not isinstance(task, Mapping):
        return _invalid_input("POST")
    return _task_api_request(
        "POST",
        "/tasks/user",
        user_id,
        api_key,
        expected_data_type=dict,
        mutation=True,
        json=dict(task),
    )


def get_task_result(
    user_id: str,
    api_key: str,
    task_id: str,
) -> HabiticaResult[dict]:
    """Fetch one task by its UUID or Habitica alias."""
    quoted_task_id = _quoted_identifier(task_id)
    if quoted_task_id is None:
        return _invalid_input("GET")
    return _task_api_request(
        "GET",
        f"/tasks/{quoted_task_id}",
        user_id,
        api_key,
        expected_data_type=dict,
    )


def update_task_result(
    user_id: str,
    api_key: str,
    task_id: str,
    updates: Mapping[str, Any],
) -> HabiticaResult[dict]:
    """Partially update a task through Habitica's PUT endpoint."""
    quoted_task_id = _quoted_identifier(task_id)
    if quoted_task_id is None or not isinstance(updates, Mapping):
        return _invalid_input("PUT")
    return _task_api_request(
        "PUT",
        f"/tasks/{quoted_task_id}",
        user_id,
        api_key,
        expected_data_type=dict,
        mutation=True,
        json=dict(updates),
    )


def delete_task_result(
    user_id: str,
    api_key: str,
    task_id: str,
) -> HabiticaResult[dict]:
    """Delete a task and return Habitica's empty data object."""
    quoted_task_id = _quoted_identifier(task_id)
    if quoted_task_id is None:
        return _invalid_input("DELETE")
    return _task_api_request(
        "DELETE",
        f"/tasks/{quoted_task_id}",
        user_id,
        api_key,
        expected_data_type=dict,
        mutation=True,
    )


def score_task_result(
    user_id: str,
    api_key: str,
    task_id: str,
    direction: str,
) -> HabiticaResult[dict]:
    """Score a task and return Habitica's user-statistics object.

    Habitica does not return the updated task from this endpoint.  Callers
    that need authoritative task state must perform a separate safe GET.
    """
    quoted_task_id = _quoted_identifier(task_id)
    if quoted_task_id is None or direction not in ("up", "down"):
        return _invalid_input("POST")
    return _task_api_request(
        "POST",
        f"/tasks/{quoted_task_id}/score/{direction}",
        user_id,
        api_key,
        expected_data_type=dict,
        mutation=True,
    )


def score_checklist_item_result(
    user_id: str,
    api_key: str,
    task_id: str,
    item_id: str,
) -> HabiticaResult[dict]:
    """Toggle a checklist item and return Habitica's updated task object."""
    quoted_task_id = _quoted_identifier(task_id)
    quoted_item_id = _quoted_identifier(item_id)
    if quoted_task_id is None or quoted_item_id is None:
        return _invalid_input("POST")
    return _task_api_request(
        "POST",
        f"/tasks/{quoted_task_id}/checklist/{quoted_item_id}/score",
        user_id,
        api_key,
        expected_data_type=dict,
        mutation=True,
    )


def add_checklist_item_result(
    user_id: str,
    api_key: str,
    task_id: str,
    item: Mapping[str, Any],
) -> HabiticaResult[dict]:
    """Add a Daily/To Do checklist item and return the updated task."""
    quoted_task_id = _quoted_identifier(task_id)
    if quoted_task_id is None or not isinstance(item, Mapping):
        return _invalid_input("POST")
    return _task_api_request(
        "POST",
        f"/tasks/{quoted_task_id}/checklist",
        user_id,
        api_key,
        expected_data_type=dict,
        mutation=True,
        json=dict(item),
    )


def update_checklist_item_result(
    user_id: str,
    api_key: str,
    task_id: str,
    item_id: str,
    updates: Mapping[str, Any],
) -> HabiticaResult[dict]:
    """Update a checklist item and return the updated task."""
    quoted_task_id = _quoted_identifier(task_id)
    quoted_item_id = _quoted_identifier(item_id)
    if quoted_task_id is None or quoted_item_id is None or not isinstance(updates, Mapping):
        return _invalid_input("PUT")
    return _task_api_request(
        "PUT",
        f"/tasks/{quoted_task_id}/checklist/{quoted_item_id}",
        user_id,
        api_key,
        expected_data_type=dict,
        mutation=True,
        json=dict(updates),
    )


def delete_checklist_item_result(
    user_id: str,
    api_key: str,
    task_id: str,
    item_id: str,
) -> HabiticaResult[dict]:
    """Delete a checklist item and return the updated task."""
    quoted_task_id = _quoted_identifier(task_id)
    quoted_item_id = _quoted_identifier(item_id)
    if quoted_task_id is None or quoted_item_id is None:
        return _invalid_input("DELETE")
    return _task_api_request(
        "DELETE",
        f"/tasks/{quoted_task_id}/checklist/{quoted_item_id}",
        user_id,
        api_key,
        expected_data_type=dict,
        mutation=True,
    )


def _make_request(
    method: str,
    url: str,
    user_id: str,
    api_key: str,
    *,
    expected_data_type: Optional[type] = None,
    **kwargs: Any,
) -> Optional[Dict[str, Any]]:
    """Make one JSON API request and validate its Habitica envelope.

    Requests are intentionally attempted exactly once.  Repeating Habitica
    mutations such as scoring or buying after an uncertain result can apply
    the operation twice, so retry policy belongs in a higher-level caller that
    can reconcile state first.
    """
    method = method.upper()
    full_url = f"{BASE_URL}{url}"

    # Authentication headers and timeouts are not caller-overridable.  Keeping
    # credentials per request also avoids cross-user header leakage if callers
    # later execute these helpers concurrently.
    try:
        kwargs["headers"] = _headers(user_id, api_key)
    except HabiticaConfigurationError:
        logger.error(
            "Habitica request blocked method=%s kind=%s",
            method,
            HabiticaErrorKind.CONFIGURATION_ERROR.value,
        )
        return None
    kwargs["timeout"] = REQUEST_TIMEOUT
    kwargs["allow_redirects"] = False

    try:
        response = requests.request(method, full_url, **kwargs)
        response.raise_for_status()
        payload = response.json()
    except requests.exceptions.HTTPError as exc:
        status_code = getattr(exc.response, "status_code", None)
        logger.warning(
            "Habitica HTTP request failed method=%s status=%s",
            method,
            status_code,
        )
        return None
    except requests.exceptions.RequestException as exc:
        # Log the exception class, not its message: requests error strings can
        # contain URLs, proxy details, or invalid header values.
        logger.warning(
            "Habitica request failed method=%s error=%s",
            method,
            type(exc).__name__,
        )
        return None
    except ValueError:
        logger.warning("Habitica returned invalid JSON method=%s", method)
        return None

    if not isinstance(payload, dict):
        logger.warning("Habitica returned a non-object envelope method=%s", method)
        return None

    if payload.get("success") is not True:
        logger.warning("Habitica returned an unsuccessful envelope method=%s", method)
        return None

    if expected_data_type is not None:
        if "data" not in payload or not isinstance(payload["data"], expected_data_type):
            logger.warning(
                "Habitica returned an invalid data shape method=%s expected=%s",
                method,
                expected_data_type.__name__,
            )
            return None

    return payload


def _quoted_task_id(task_id: str) -> Optional[str]:
    """Quote a task id as one URL path segment without logging its value."""
    if not isinstance(task_id, str) or not task_id or task_id in (".", ".."):
        logger.warning("Habitica task operation received an invalid task id")
        return None
    return quote(task_id, safe="")


def get_status(user_id: str, api_key: str) -> Optional[dict]:
    """Fetch the authenticated user's status, or ``None`` on failure."""
    response_data = _make_request(
        "GET",
        "/user",
        user_id,
        api_key,
        expected_data_type=dict,
    )
    return response_data["data"] if response_data is not None else None


def get_tasks(user_id: str, api_key: str, task_type: str) -> Optional[list]:
    """Fetch tasks of a specific type, preserving a successful empty list."""
    response_data = _make_request(
        "GET",
        "/tasks/user",
        user_id,
        api_key,
        expected_data_type=list,
        params={"type": task_type, "history": "false"},
    )
    if response_data is None:
        return None

    tasks = response_data["data"]
    if not all(isinstance(task, dict) for task in tasks):
        logger.warning("Habitica returned a task list containing invalid entries")
        return None
    return tasks


def create_todo_task(
    user_id: str,
    api_key: str,
    title: str,
    priority: float,
) -> Optional[dict]:
    """Create a Habitica todo and return its task data, or ``None``."""
    response_data = _make_request(
        "POST",
        "/tasks/user",
        user_id,
        api_key,
        expected_data_type=dict,
        json={
            "text": title,
            "type": "todo",
            "priority": priority,
        },
    )
    return response_data["data"] if response_data is not None else None


def get_task_by_id(
    user_id: str,
    api_key: str,
    task_id: str,
) -> Optional[dict]:
    """Fetch a single Habitica task by id, or ``None`` on failure."""
    quoted_task_id = _quoted_task_id(task_id)
    if quoted_task_id is None:
        return None

    response_data = _make_request(
        "GET",
        f"/tasks/{quoted_task_id}",
        user_id,
        api_key,
        expected_data_type=dict,
    )
    return response_data["data"] if response_data is not None else None


def score_task(
    user_id: str,
    api_key: str,
    task_id: str,
    direction: str,
) -> Optional[dict]:
    """Score a task and return Habitica's user stats, or ``None``."""
    if direction not in ("up", "down"):
        logger.warning("Habitica score operation received an invalid direction")
        return None

    quoted_task_id = _quoted_task_id(task_id)
    if quoted_task_id is None:
        return None

    response_data = _make_request(
        "POST",
        f"/tasks/{quoted_task_id}/score/{direction}",
        user_id,
        api_key,
        expected_data_type=dict,
    )
    return response_data["data"] if response_data is not None else None


def buy_potion(user_id: str, api_key: str) -> bool:
    """Buy a health potion and return a literal success boolean."""
    response_data = _make_request(
        "POST",
        "/user/buy-health-potion",
        user_id,
        api_key,
    )
    return response_data is not None and response_data.get("success") is True


def buy_reward(user_id: str, api_key: str, task_id: str) -> bool:
    """Buy a custom reward and return a literal success boolean."""
    quoted_task_id = _quoted_task_id(task_id)
    if quoted_task_id is None:
        return False

    response_data = _make_request(
        "POST",
        f"/tasks/{quoted_task_id}/buy",
        user_id,
        api_key,
    )
    return response_data is not None and response_data.get("success") is True


def run_cron(user_id: str, api_key: str) -> bool:
    """Run Habitica's daily cron and require an explicit success response."""
    response_data = _make_request("POST", "/cron", user_id, api_key)
    return response_data is not None and response_data.get("success") is True
