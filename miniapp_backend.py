"""Flask Blueprint for the first Habitica Telegram Mini App milestone."""

from __future__ import annotations

import asyncio
import logging
import math
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Any

from flask import Blueprint, jsonify, render_template, request, send_file

from miniapp_auth import (
    MiniAppAuthConfigurationError,
    MiniAppAuthError,
    authenticate_authorization_header,
)
from runtime_lock import RuntimeLockUnavailable, acquire_runtime_lock, get_bot_data_path

miniapp_blueprint = Blueprint("miniapp", __name__, url_prefix="/miniapp")
LOGGER = logging.getLogger(__name__)

_CLASS_LABELS = {
    "healer": "Healer",
    "rogue": "Rogue",
    "warrior": "Warrior",
    "wizard": "Mage",
}
DEFAULT_AVATAR_REFRESH_COOLDOWN_SECONDS = 30.0
MAX_AVATAR_REFRESH_COOLDOWN_SECONDS = 300.0


class MiniAppPersistenceError(RuntimeError):
    """Raised when linked-account state cannot be read safely."""


@dataclass(frozen=True, slots=True, repr=False)
class LinkedHabiticaAccount:
    """A private, request-local copy of one linked account."""

    habitica_user_id: str
    habitica_api_key: str


async def _read_all_user_data() -> dict[int, dict[str, Any]]:
    # Imported lazily so the existing webhook tests can replace the Telegram
    # modules with small fakes without needing to emulate telegram.ext.
    from telegram.ext import PicklePersistence

    persistence = PicklePersistence(filepath=get_bot_data_path(), on_flush=True)
    data = await persistence.get_user_data()
    if not isinstance(data, dict):
        raise MiniAppPersistenceError
    return data


def load_linked_habitica_account(telegram_user_id: int) -> LinkedHabiticaAccount | None:
    """Read one account from PTB persistence without modifying the pickle.

    A fresh ``PicklePersistence`` is used for every lookup because instances
    cache loaded state.  The cross-process lock is held only while the trusted
    server-side pickle is read and the selected entry is copied; Habitica HTTP
    calls and avatar rendering happen after release.
    """

    try:
        with acquire_runtime_lock():
            all_user_data = asyncio.run(_read_all_user_data())
            stored = all_user_data.get(telegram_user_id)
            if not isinstance(stored, Mapping):
                return None
            habitica_user_id = stored.get("USER_ID")
            habitica_api_key = stored.get("API_KEY")
            if (
                not isinstance(habitica_user_id, str)
                or not habitica_user_id.strip()
                or not isinstance(habitica_api_key, str)
                or not habitica_api_key.strip()
            ):
                return None
            return LinkedHabiticaAccount(
                habitica_user_id=habitica_user_id,
                habitica_api_key=habitica_api_key,
            )
    except RuntimeLockUnavailable:
        raise
    except MiniAppPersistenceError:
        raise
    except Exception as exc:
        raise MiniAppPersistenceError from exc


def get_habitica_status(account: LinkedHabiticaAccount) -> dict[str, Any] | None:
    """Call the repository's bounded Habitica HTTP boundary."""

    from Habitica_API import get_status

    return get_status(account.habitica_user_id, account.habitica_api_key)


def _avatar_refresh_cooldown() -> float:
    raw_value = os.environ.get("MINIAPP_AVATAR_REFRESH_COOLDOWN_SECONDS")
    if raw_value is None:
        return DEFAULT_AVATAR_REFRESH_COOLDOWN_SECONDS
    try:
        value = float(raw_value)
    except ValueError:
        return DEFAULT_AVATAR_REFRESH_COOLDOWN_SECONDS
    if not math.isfinite(value) or value < 0:
        return DEFAULT_AVATAR_REFRESH_COOLDOWN_SECONDS
    return min(value, MAX_AVATAR_REFRESH_COOLDOWN_SECONDS)


def _avatar_render_lock_path(account: LinkedHabiticaAccount) -> Path:
    from avatar_renderer import get_avatar_cache_path

    lock_root = (Path(__file__).resolve().parent / "Avatar" / ".locks").resolve()
    lock_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(lock_root, 0o700)
    except OSError:
        pass
    cache_name = get_avatar_cache_path(lock_root, account.habitica_user_id).stem
    return lock_root / f"{cache_name}.lock"


def render_habitica_avatar(
    account: LinkedHabiticaAccount,
    *,
    force_refresh: bool,
) -> Path | None:
    """Reuse the established local Node/Puppeteer renderer and opaque cache."""

    from avatar_renderer import get_avatar_cache_path, is_valid_png

    avatar_root = (Path(__file__).resolve().parent / "Avatar").resolve()
    expected_cache_path = get_avatar_cache_path(avatar_root, account.habitica_user_id)
    expected_name = expected_cache_path.name

    def valid_cached_avatar() -> Path | None:
        try:
            cached_path = expected_cache_path.resolve(strict=True)
        except (OSError, RuntimeError):
            return None
        if (
            cached_path.parent == avatar_root
            and cached_path.name == expected_name
            and is_valid_png(cached_path)
        ):
            return cached_path
        return None

    lock_path = _avatar_render_lock_path(account)
    with acquire_runtime_lock(lock_path=lock_path):
        cached_path = valid_cached_avatar()
        preloaded_user_json = get_habitica_status(account)
        if not isinstance(preloaded_user_json, dict):
            return None
        if not force_refresh and cached_path is not None:
            return cached_path
        if force_refresh and cached_path is not None:
            age_seconds = max(0.0, time.time() - cached_path.stat().st_mtime)
            if age_seconds < _avatar_refresh_cooldown():
                return cached_path

        from habitica_bot import ensure_avatar_png_no_update

        avatar_user_data: dict[str, Any] = {}
        rendered = ensure_avatar_png_no_update(
            habitica_user_id=account.habitica_user_id,
            habitica_api_key=account.habitica_api_key,
            user_data=avatar_user_data,
            force_refresh=force_refresh,
            preloaded_user_json=preloaded_user_json,
        )
        if not rendered:
            return None

        try:
            candidate = Path(rendered).resolve(strict=True)
        except (OSError, RuntimeError):
            return None
        if (
            candidate.parent != avatar_root
            or candidate.name != expected_name
            or not is_valid_png(candidate)
        ):
            return None
        return candidate


def _safe_text(value: Any, *, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized[:limit]


def _safe_number(value: Any, *, minimum: float = 0.0) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        finite = math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        return None
    if not finite or value < minimum:
        return None
    return value


def _safe_level(value: Any) -> int:
    number = _safe_number(value)
    if number is None:
        return 0
    return max(0, int(number))


def normalize_habitica_profile(user: Mapping[str, Any]) -> dict[str, Any]:
    """Return only presentation fields explicitly approved for the browser."""

    profile_value = user.get("profile")
    auth_value = user.get("auth")
    stats_value = user.get("stats")
    preferences_value = user.get("preferences")
    flags_value = user.get("flags")

    profile = profile_value if isinstance(profile_value, Mapping) else {}
    auth = auth_value if isinstance(auth_value, Mapping) else {}
    stats = stats_value if isinstance(stats_value, Mapping) else {}
    preferences = preferences_value if isinstance(preferences_value, Mapping) else {}
    flags = flags_value if isinstance(flags_value, Mapping) else {}
    local_value = auth.get("local")
    local = local_value if isinstance(local_value, Mapping) else {}

    display_name = _safe_text(profile.get("name"), limit=128)
    username = _safe_text(local.get("username"), limit=64)
    level = _safe_level(stats.get("lvl"))
    raw_class = stats.get("class")
    class_key = raw_class if isinstance(raw_class, str) and raw_class in _CLASS_LABELS else None
    has_class = (
        level >= 10
        and preferences.get("disableClasses") is not True
        and flags.get("classSelected") is True
        and class_key is not None
    )
    visible_class = class_key if has_class else None

    return {
        "ok": True,
        "profile": {
            "displayName": display_name or username or "Habitican",
            "username": username,
            "level": level,
            "class": visible_class,
            "classLabel": _CLASS_LABELS[visible_class] if visible_class else "Adventurer",
            "hasClass": has_class,
        },
        "stats": {
            "hp": _safe_number(stats.get("hp")),
            "maxHp": _safe_number(stats.get("maxHealth")),
            "exp": _safe_number(stats.get("exp")),
            "maxExp": _safe_number(stats.get("toNextLevel")),
            "mp": _safe_number(stats.get("mp")) if has_class else None,
            "maxMp": _safe_number(stats.get("maxMP")) if has_class else None,
            "gold": _safe_number(stats.get("gp")),
        },
    }


def _private_json(payload: dict[str, Any], status: HTTPStatus):
    response = jsonify(payload)
    response.status_code = status
    return response


def _error(code: str, message: str, status: HTTPStatus):
    return _private_json(
        {"ok": False, "error": {"code": code, "message": message}},
        status,
    )


def _authenticated_account() -> tuple[LinkedHabiticaAccount | None, Any | None]:
    try:
        identity = authenticate_authorization_header(request.headers.get("Authorization"))
    except MiniAppAuthError:
        return None, _error(
            "invalid_telegram_session",
            "Open this app from Telegram and try again.",
            HTTPStatus.UNAUTHORIZED,
        )
    except MiniAppAuthConfigurationError:
        return None, _error(
            "service_unavailable",
            "The Mini App is temporarily unavailable.",
            HTTPStatus.SERVICE_UNAVAILABLE,
        )

    try:
        account = load_linked_habitica_account(identity.telegram_user_id)
    except (RuntimeLockUnavailable, MiniAppPersistenceError):
        return None, _error(
            "service_unavailable",
            "The Mini App is temporarily busy. Please try again.",
            HTTPStatus.SERVICE_UNAVAILABLE,
        )
    if account is None:
        return None, _error(
            "habitica_not_linked",
            "Link your Habitica account with /start first.",
            HTTPStatus.CONFLICT,
        )
    return account, None


@miniapp_blueprint.after_request
def _secure_miniapp_response(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Vary"] = "Authorization"
    return response


@miniapp_blueprint.get("/")
def miniapp_index():
    return render_template("miniapp/index.html")


@miniapp_blueprint.get("/api/me")
def miniapp_me():
    account, error_response = _authenticated_account()
    if error_response is not None:
        return error_response
    assert account is not None

    try:
        user = get_habitica_status(account)
    except Exception as exc:  # noqa: BLE001
        LOGGER.error("Mini App Habitica profile request failed (%s)", type(exc).__name__)
        user = None
    if not isinstance(user, Mapping):
        return _error(
            "habitica_unavailable",
            "Habitica could not be reached. Please try again.",
            HTTPStatus.BAD_GATEWAY,
        )
    return _private_json(normalize_habitica_profile(user), HTTPStatus.OK)


@miniapp_blueprint.get("/api/avatar")
def miniapp_avatar():
    account, error_response = _authenticated_account()
    if error_response is not None:
        return error_response
    assert account is not None

    try:
        avatar_path = render_habitica_avatar(
            account,
            force_refresh=request.args.get("refresh") == "1",
        )
    except RuntimeLockUnavailable:
        return _error(
            "service_unavailable",
            "Avatar rendering is busy. Please try again.",
            HTTPStatus.SERVICE_UNAVAILABLE,
        )
    except Exception as exc:  # noqa: BLE001
        LOGGER.error("Mini App avatar rendering failed (%s)", type(exc).__name__)
        avatar_path = None
    if avatar_path is None:
        return _error(
            "avatar_unavailable",
            "Your avatar could not be rendered. Please try again.",
            HTTPStatus.BAD_GATEWAY,
        )

    return send_file(
        avatar_path,
        mimetype="image/png",
        as_attachment=False,
        download_name="habitica-avatar.png",
        conditional=False,
        etag=False,
        max_age=0,
    )
