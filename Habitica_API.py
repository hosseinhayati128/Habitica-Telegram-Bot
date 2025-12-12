# Habitica_API.py
import requests
import logging
from typing import Optional, Dict, Any


BASE_URL = "https://habitica.com/api/v3"


def _headers(user_id: str, api_key: str) -> dict:
    """Create standard headers for API requests."""
    return {
        "x-api-user": user_id,
        "x-api-key": api_key,
        "x-client": "habitica-python-3.0.0",
        "Content-Type": "application/json",
    }


def _make_request(method: str, url: str, user_id: str, api_key: str, **kwargs) -> Optional[Dict[Any, Any]]:
    """
    Make a request to the Habitica API with automatic session refresh.
    This is the core function that all other API calls should use.
    """
    headers = _headers(user_id, api_key)
    full_url = f"{BASE_URL}{url}"

    try:
        response = requests.request(method, full_url, headers=headers, **kwargs)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as e:
        # Check for the specific "session outdated" error
        if e.response.status_code == 401 or (
                e.response.status_code == 401 and "session is outdated" in e.response.text.lower()):
            logging.warning("Session outdated. Attempting to refresh user data.")
            try:
                # Try to refresh by getting user status, which often renews the session
                refresh_response = requests.get(f"{BASE_URL}/user", headers=headers)
                refresh_response.raise_for_status()
                logging.info("Session refreshed successfully. Retrying original request.")
                # Retry the original request
                retry_response = requests.request(method, full_url, headers=headers, **kwargs)
                retry_response.raise_for_status()
                return retry_response.json()
            except requests.exceptions.RequestException as refresh_error:
                logging.error(f"Failed to refresh session: {refresh_error}")
                return None
        else:
            # Handle other HTTP errors
            error_message = "Unknown error"
            if e.response is not None:
                try:
                    error_details = e.response.json()
                    error_message = error_details.get('message', 'No message in error response')
                except ValueError:
                    error_message = e.response.text
            logging.error(f"API Error: {error_message}")
            return None
    except requests.exceptions.RequestException as e:
        logging.error(f"Network error: {e}")
        return None


def get_status(user_id: str, api_key: str) -> Optional[dict]:
    """Fetches user status from Habitica API."""
    response_data = _make_request("GET", "/user", user_id, api_key)
    return response_data.get('data') if response_data else None


def get_tasks(user_id: str, api_key: str, task_type: str) -> Optional[list]:
    """Fetches tasks of a specific type from Habitica API."""
    response_data = _make_request("GET", "/tasks/user", user_id, api_key, params={"type": task_type})
    return response_data.get('data', []) if response_data else None


# HABITICA_API_URL is already defined at the top of your file,
# so we just reuse it here.

def create_todo_task(user_id: str, api_key: str, title: str, priority: float) -> Optional[dict]:
    """
    Create a new Habitica todo with the given title & priority (difficulty).

    Habitica priority values:
      0.1 = Trivial
      1   = Easy
      1.5 = Medium
      2   = Hard
    """
    headers = {
        "x-api-user": user_id,
        "x-api-key": api_key,
        "x-client": "habitica-telegram-bot",  # optional but nice & consistent
        "Content-Type": "application/json",
    }
    url = f"{BASE_URL}/tasks/user"
    payload = {
        "text": title,
        "type": "todo",
        "priority": priority,
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("data")
    except requests.RequestException as e:
        logging.error("Failed to create todo: %s", e)
        return None



def get_task_by_id(user_id: str, api_key: str, task_id: str) -> Optional[dict]:
    """Fetches a single task by its ID from the Habitica API."""
    response_data = _make_request("GET", f"/tasks/{task_id}", user_id, api_key)
    return response_data.get('data') if response_data else None


def score_task(user_id: str, api_key: str, task_id: str, direction: str) -> Optional[dict]:
    """Scores a task in Habitica and returns the updated task data."""
    if direction not in ['up', 'down']:
        logging.error(f"Invalid direction '{direction}' for scoring task {task_id}.")
        return None

    response_data = _make_request("POST", f"/tasks/{task_id}/score/{direction}", user_id, api_key)
    return response_data.get('data') if response_data else None


def export_avatar_png(user_id: str, api_key: str) -> bytes | None:
    """
    Returns the current user's avatar PNG bytes, or None on failure.
    Uses Habitica's export endpoint for the *authenticated* user.
    NOTE: This endpoint is known to be flaky / broken in Habitica's API.
    """
    try:
        base = BASE_URL.split("/api/")[0]  # "https://habitica.com"
        url = f"{base}/export/avatar-plain.png"
        headers = {
            "x-api-user": user_id,
            "x-api-key": api_key,
            "x-client": "habitica-telegram-bot",  # match your other calls
        }
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.content
    except requests.RequestException as e:
        logging.error(f"Failed to download avatar: {e}")
        return None




def buy_potion(user_id: str, api_key: str) -> bool:
    """Buy a health potion. Returns True/False."""
    response_data = _make_request("POST", "/user/buy-health-potion", user_id, api_key)
    return response_data.get("success", False) if response_data else False


def buy_reward(user_id: str, api_key: str, task_id: str) -> bool:
    """Buy a custom reward by its task id."""
    response_data = _make_request("POST", f"/tasks/{task_id}/buy", user_id, api_key)
    return response_data.get("success", False) if response_data else False
