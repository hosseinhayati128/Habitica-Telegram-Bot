"""Small, synchronous client helpers for the Habitica API.

The public functions in this module intentionally retain their historical
``None``/``False`` sentinel contracts.  Callers can therefore distinguish a
successful empty task list (``[]``) from an API or network failure (``None``)
without having credentials or raw response bodies written to logs.
"""

import logging
from typing import Any, Dict, Optional
from urllib.parse import quote

import requests


BASE_URL = "https://habitica.com/api/v3"
CLIENT_ID = "habitica-telegram-bot"

# A tuple gives requests separate connection and response-read limits.  These
# helpers remain synchronous, so bounding both phases is particularly
# important when a caller delegates them from an async Telegram handler.
REQUEST_TIMEOUT = (5.0, 30.0)

MAX_AVATAR_BYTES = 10 * 1024 * 1024
PNG_CONTENT_TYPE = "image/png"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
AVATAR_CHUNK_SIZE = 64 * 1024

logger = logging.getLogger(__name__)


def _headers(user_id: str, api_key: str) -> dict:
    """Return the canonical per-request Habitica headers."""
    return {
        "x-api-user": user_id,
        "x-api-key": api_key,
        "x-client": CLIENT_ID,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


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
    kwargs["headers"] = _headers(user_id, api_key)
    kwargs["timeout"] = REQUEST_TIMEOUT

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
        params={"type": task_type},
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
    """Score a task up or down and return updated data, or ``None``."""
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


def export_avatar_png(user_id: str, api_key: str) -> bytes | None:
    """Return a bounded, validated PNG export for the authenticated user."""
    base = BASE_URL.split("/api/", 1)[0]
    url = f"{base}/export/avatar-plain.png"
    headers = _headers(user_id, api_key)
    headers["Accept"] = PNG_CONTENT_TYPE

    response = None
    try:
        response = requests.request(
            "GET",
            url,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
            stream=True,
            allow_redirects=False,
        )
        response.raise_for_status()

        content_type = response.headers.get("Content-Type", "")
        media_type = content_type.split(";", 1)[0].strip().lower()
        if media_type != PNG_CONTENT_TYPE:
            logger.warning("Habitica avatar export returned an invalid content type")
            return None

        content_length = response.headers.get("Content-Length")
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except (TypeError, ValueError):
                logger.warning("Habitica avatar export returned an invalid content length")
                return None
            if declared_size < 0 or declared_size > MAX_AVATAR_BYTES:
                logger.warning("Habitica avatar export exceeded the size limit")
                return None

        chunks = []
        total_size = 0
        for chunk in response.iter_content(chunk_size=AVATAR_CHUNK_SIZE):
            if not chunk:
                continue
            total_size += len(chunk)
            if total_size > MAX_AVATAR_BYTES:
                logger.warning("Habitica avatar export exceeded the size limit")
                return None
            chunks.append(chunk)

        content = b"".join(chunks)
        if not content.startswith(PNG_SIGNATURE):
            logger.warning("Habitica avatar export returned invalid PNG data")
            return None
        return content
    except requests.exceptions.HTTPError as exc:
        status_code = getattr(exc.response, "status_code", None)
        logger.warning(
            "Habitica avatar export failed status=%s",
            status_code,
        )
        return None
    except requests.exceptions.RequestException as exc:
        logger.warning(
            "Habitica avatar export request failed error=%s",
            type(exc).__name__,
        )
        return None
    finally:
        if response is not None:
            response.close()
