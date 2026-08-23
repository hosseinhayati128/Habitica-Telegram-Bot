"""Flask Blueprint for the first Habitica Telegram Mini App milestone."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import math
import os
import secrets
import threading
import time
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from http import HTTPStatus
from pathlib import Path
from typing import Any

from flask import Blueprint, jsonify, render_template, request, send_file

from miniapp_auth import (
    MiniAppAuthConfigurationError,
    MiniAppAuthError,
    authenticate_authorization_header,
)
from runtime_lock import (
    GAMEPLAY_LOCK_SHARDS as GAMEPLAY_LOCK_SHARDS,
    RuntimeLockUnavailable,
    acquire_runtime_lock,
    get_bot_data_path,
    get_gameplay_lock_path,
)
from miniapp_tasks import (
    TaskValidationError,
    checklist_changes,
    normalize_task,
    normalize_tasks,
    optimistic_scored_task,
    quest_log_summary,
    upstream_list_type,
    validate_task_id,
    validate_task_payload,
)

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
MAX_TASK_REQUEST_BYTES = 128 * 1024
MAX_CHECKLIST_MUTATIONS_PER_EDIT = 1
TASK_MUTATION_LOCK_SHARDS = 32
# ``refresh_day`` performs one score plus an immediately preceding day-state
# read per selection. It processes this many per request, then returns an
# explicit continuation without cron while preserving every remaining choice.
MAX_DAY_REFRESH_BATCH_SIZE = 8
POTION_METADATA_TTL_SECONDS = 300.0
POTION_PURCHASE_INTENT_TTL_SECONDS = 300.0
MAX_POTION_PURCHASE_INTENTS = 256
_POTION_METADATA_CACHE_LOCK = threading.Lock()
_POTION_METADATA_CACHE: tuple[float, dict[str, Any]] | None = None
_POTION_PURCHASE_INTENTS_LOCK = threading.Lock()
_POTION_PURCHASE_INTENTS: dict[bytes, _PotionPurchaseIntent] = {}


class MiniAppPersistenceError(RuntimeError):
    """Raised when linked-account state cannot be read safely."""


@dataclass(frozen=True, slots=True, repr=False)
class LinkedHabiticaAccount:
    """A private, request-local copy of one linked account."""

    habitica_user_id: str
    habitica_api_key: str


@dataclass(frozen=True, slots=True, repr=False)
class _PotionResponseReplay:
    """One already-sanitized response safe to replay for the same intent."""

    payload: dict[str, Any]
    status: int
    retry_after: str | None = None


@dataclass(slots=True, repr=False)
class _PotionPurchaseIntent:
    """Server-side state for one opaque, account-bound purchase intent."""

    token_digest: bytes
    account_digest: bytes
    created_at: float
    expires_at: float
    state: str = "fresh"
    replay: _PotionResponseReplay | None = None


def _potion_account_digest(account: LinkedHabiticaAccount) -> bytes:
    """Hash the complete linked-account identity without retaining credentials."""

    material = (
        account.habitica_user_id.encode("utf-8")
        + b"\0"
        + account.habitica_api_key.encode("utf-8")
    )
    return hashlib.sha256(b"miniapp-health-potion-account\0" + material).digest()


def _potion_token_digest(token: Any) -> bytes | None:
    if (
        not isinstance(token, str)
        or not 32 <= len(token) <= 128
        or not token.isascii()
        or any(not (character.isalnum() or character in "-_") for character in token)
    ):
        return None
    return hashlib.sha256(token.encode("ascii")).digest()


def _evict_expired_potion_intents_locked(now: float) -> None:
    expired = [
        digest
        for digest, intent in _POTION_PURCHASE_INTENTS.items()
        if intent.expires_at <= now
    ]
    for digest in expired:
        _POTION_PURCHASE_INTENTS.pop(digest, None)


def _make_potion_intent_room_locked(now: float) -> bool:
    """Bound memory while retaining in-progress and terminal replay records."""

    _evict_expired_potion_intents_locked(now)
    while len(_POTION_PURCHASE_INTENTS) >= MAX_POTION_PURCHASE_INTENTS:
        expendable = [
            (intent.created_at, digest)
            for digest, intent in _POTION_PURCHASE_INTENTS.items()
            if intent.state == "fresh"
        ]
        if not expendable:
            return False
        _, oldest_digest = min(expendable)
        _POTION_PURCHASE_INTENTS.pop(oldest_digest, None)
    return True


def _issue_potion_purchase_intent(account: LinkedHabiticaAccount) -> str | None:
    """Create one opaque five-minute purchase capability for this account."""

    now = time.monotonic()
    account_digest = _potion_account_digest(account)
    with _POTION_PURCHASE_INTENTS_LOCK:
        if not _make_potion_intent_room_locked(now):
            return None
        for _attempt in range(4):
            token = secrets.token_urlsafe(32)
            token_digest = hashlib.sha256(token.encode("ascii")).digest()
            if token_digest in _POTION_PURCHASE_INTENTS:
                continue
            _POTION_PURCHASE_INTENTS[token_digest] = _PotionPurchaseIntent(
                token_digest=token_digest,
                account_digest=account_digest,
                created_at=now,
                expires_at=now + POTION_PURCHASE_INTENT_TTL_SECONDS,
            )
            return token
    return None


def _claim_potion_purchase_intent(
    account: LinkedHabiticaAccount,
    token: Any,
) -> tuple[str, bytes | None, _PotionResponseReplay | None]:
    """Atomically claim a fresh intent, reject overlap, or return its replay."""

    token_digest = _potion_token_digest(token)
    if token_digest is None:
        return "invalid", None, None
    account_digest = _potion_account_digest(account)
    now = time.monotonic()
    with _POTION_PURCHASE_INTENTS_LOCK:
        _evict_expired_potion_intents_locked(now)
        intent = _POTION_PURCHASE_INTENTS.get(token_digest)
        if (
            intent is None
            or not hmac.compare_digest(intent.token_digest, token_digest)
            or not hmac.compare_digest(intent.account_digest, account_digest)
        ):
            return "invalid", None, None
        if intent.state == "terminal" and intent.replay is not None:
            return "replay", token_digest, intent.replay
        if intent.state == "in_progress":
            return "in_progress", token_digest, None
        if intent.state != "fresh":
            return "invalid", None, None
        intent.state = "in_progress"
        # A request claimed near the end of its confirmation window still gets
        # a full bounded interval in which to finish or be safely retried.
        intent.expires_at = now + POTION_PURCHASE_INTENT_TTL_SECONDS
        return "claimed", token_digest, None


def _restore_potion_purchase_intent(
    account: LinkedHabiticaAccount,
    token_digest: bytes,
) -> None:
    """Restore a claim only when no purchase service call was entered."""

    account_digest = _potion_account_digest(account)
    with _POTION_PURCHASE_INTENTS_LOCK:
        intent = _POTION_PURCHASE_INTENTS.get(token_digest)
        if (
            intent is not None
            and intent.state == "in_progress"
            and hmac.compare_digest(intent.account_digest, account_digest)
        ):
            intent.state = "fresh"


def _potion_replay_response(replay: _PotionResponseReplay):
    response = _private_json(replay.payload, HTTPStatus(replay.status))
    if replay.retry_after is not None:
        response.headers["Retry-After"] = replay.retry_after
    return response


def _terminalize_potion_purchase_intent(
    account: LinkedHabiticaAccount,
    token_digest: bytes,
    response: Any,
):
    """Persist one sanitized terminal response before returning it to Flask."""

    payload = response.get_json(silent=True)
    if not isinstance(payload, dict):
        response = _error(
            "internal_error",
            "The request could not be completed.",
            HTTPStatus.INTERNAL_SERVER_ERROR,
        )
        payload = response.get_json()
    replay = _PotionResponseReplay(
        payload=payload,
        status=int(response.status_code),
        retry_after=response.headers.get("Retry-After"),
    )
    account_digest = _potion_account_digest(account)
    now = time.monotonic()
    with _POTION_PURCHASE_INTENTS_LOCK:
        intent = _POTION_PURCHASE_INTENTS.get(token_digest)
        if intent is None:
            if _make_potion_intent_room_locked(now):
                intent = _PotionPurchaseIntent(
                    token_digest=token_digest,
                    account_digest=account_digest,
                    created_at=now,
                    expires_at=now + POTION_PURCHASE_INTENT_TTL_SECONDS,
                    state="terminal",
                    replay=replay,
                )
                _POTION_PURCHASE_INTENTS[token_digest] = intent
        elif hmac.compare_digest(intent.account_digest, account_digest):
            intent.state = "terminal"
            intent.replay = replay
            intent.expires_at = now + POTION_PURCHASE_INTENT_TTL_SECONDS
    return response


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


def normalize_score_profile_patch(stats: Any) -> dict[str, int | float]:
    """Whitelist authoritative profile values returned by a successful score."""

    if not isinstance(stats, Mapping):
        return {}

    patch: dict[str, int | float] = {}
    level = _safe_number(stats.get("lvl"))
    if level is not None:
        patch["level"] = int(level)

    fields = {
        "hp": "hp",
        "maxHealth": "maxHp",
        "exp": "exp",
        "toNextLevel": "maxExp",
        "mp": "mp",
        "maxMP": "maxMp",
        "gp": "gold",
    }
    for upstream_name, public_name in fields.items():
        number = _safe_number(stats.get(upstream_name))
        if number is not None:
            patch[public_name] = number
    return patch


def _private_json(payload: dict[str, Any], status: HTTPStatus):
    response = jsonify(payload)
    response.status_code = status
    return response


def _error(code: str, message: str, status: HTTPStatus):
    return _private_json(
        {"ok": False, "error": {"code": code, "message": message}},
        status,
    )


def _retry_after_seconds(value: float | None) -> int | None:
    if value is None or not math.isfinite(value):
        return None
    return max(0, min(3600, math.ceil(value)))


def _minimal_potion_content(content: Any) -> dict[str, Any] | None:
    """Validate and retain only public Health Potion content fields."""

    if not isinstance(content, Mapping):
        return None
    raw_potion = content.get("potion")
    if not isinstance(raw_potion, Mapping):
        return None
    price = _safe_number(raw_potion.get("value"))
    if price is None:
        return None
    raw_name = raw_potion.get("text")
    name = raw_name.strip()[:128] if isinstance(raw_name, str) and raw_name.strip() else None
    potion: dict[str, Any] = {"value": price}
    if name is not None:
        potion["text"] = name
    for field in ("healing", "heal", "health", "amount"):
        value = _safe_number(raw_potion.get(field))
        if value is not None:
            potion[field] = value
            break
    return {"potion": potion}


def _cached_potion_content_result(user_id: str, api_key: str, *, language: str = "en"):
    """Cache one validated content fragment, never credentials or raw content."""

    from Habitica_API import HabiticaResult, get_content_result

    global _POTION_METADATA_CACHE
    now = time.monotonic()
    if language == "en":
        with _POTION_METADATA_CACHE_LOCK:
            cached = _POTION_METADATA_CACHE
            if cached is not None and cached[0] > now:
                return HabiticaResult(data={"potion": dict(cached[1]["potion"])})

    result = get_content_result(user_id, api_key, language=language)
    if not result.ok:
        return result
    minimal = _minimal_potion_content(result.data)
    if minimal is None:
        return HabiticaResult(data={}, status=result.status)
    if language == "en":
        with _POTION_METADATA_CACHE_LOCK:
            _POTION_METADATA_CACHE = (
                now + POTION_METADATA_TTL_SECONDS,
                {"potion": dict(minimal["potion"])},
            )
    return HabiticaResult(data=minimal, status=result.status)


def _habitica_error_response(result, *, mutation: bool = False):
    """Translate a typed Habitica failure without exposing upstream details."""

    from Habitica_API import HabiticaErrorKind

    failure = result.error
    if failure is None:
        return _error(
            "internal_error",
            "The request could not be completed.",
            HTTPStatus.INTERNAL_SERVER_ERROR,
        )
    if failure.outcome_unknown and mutation:
        response = _private_json(
            {
                "ok": False,
                "reconcileRequired": True,
                "error": {
                    "code": "outcome_unknown",
                    "message": "Habitica may have applied this change. Refresh before trying again.",
                },
            },
            HTTPStatus.BAD_GATEWAY,
        )
        return response
    mapping = {
        HabiticaErrorKind.INVALID_INPUT: (
            "invalid_request",
            "The request was invalid.",
            HTTPStatus.BAD_REQUEST,
        ),
        HabiticaErrorKind.BAD_REQUEST: (
            "invalid_request",
            "Habitica rejected this task change.",
            HTTPStatus.BAD_REQUEST,
        ),
        HabiticaErrorKind.INVALID_CREDENTIALS: (
            "unauthorized",
            "Relink your Habitica account with /start.",
            HTTPStatus.UNAUTHORIZED,
        ),
        HabiticaErrorKind.UNAUTHORIZED: (
            "conflict",
            "This task changed elsewhere. Refresh and try again.",
            HTTPStatus.CONFLICT,
        ),
        HabiticaErrorKind.FORBIDDEN: (
            "conflict",
            "Habitica does not allow this task change.",
            HTTPStatus.CONFLICT,
        ),
        HabiticaErrorKind.NOT_FOUND: (
            "task_not_found",
            "This task is no longer available.",
            HTTPStatus.NOT_FOUND,
        ),
        HabiticaErrorKind.CONFLICT: (
            "conflict",
            "This task changed elsewhere. Refresh and try again.",
            HTTPStatus.CONFLICT,
        ),
        HabiticaErrorKind.RATE_LIMITED: (
            "rate_limited",
            "Habitica is receiving too many requests. Please wait and retry.",
            HTTPStatus.TOO_MANY_REQUESTS,
        ),
    }
    code, message, status = mapping.get(
        failure.kind,
        (
            "habitica_unavailable",
            "Habitica could not complete this request. Please try again.",
            HTTPStatus.BAD_GATEWAY,
        ),
    )
    response = _error(code, message, status)
    retry_after = _retry_after_seconds(failure.retry_after)
    if status == HTTPStatus.TOO_MANY_REQUESTS and retry_after is not None:
        response.headers["Retry-After"] = str(retry_after)
    return response


def _request_json_object():
    from werkzeug.exceptions import RequestEntityTooLarge

    if request.content_length is not None and request.content_length > MAX_TASK_REQUEST_BYTES:
        raise RequestEntityTooLarge
    if not request.is_json:
        raise TaskValidationError("request body must be JSON")
    try:
        raw_body = request.get_data(cache=True, parse_form_data=False)
        if len(raw_body) >= MAX_TASK_REQUEST_BYTES and (
            request.content_length in (None, 0) or len(raw_body) > MAX_TASK_REQUEST_BYTES
        ):
            raise RequestEntityTooLarge
        value = request.get_json(silent=False)
    except RequestEntityTooLarge:
        raise
    except Exception as exc:  # Flask/Werkzeug parser errors contain request detail.
        raise TaskValidationError("request body is invalid") from exc
    if not isinstance(value, Mapping):
        raise TaskValidationError("request body is invalid")
    return value


def _task_account_args(account: LinkedHabiticaAccount) -> tuple[str, str]:
    return account.habitica_user_id, account.habitica_api_key


def _load_task(account: LinkedHabiticaAccount, task_id: str):
    from Habitica_API import get_task_result

    result = get_task_result(*_task_account_args(account), task_id)
    if not result.ok:
        return None, _habitica_error_response(result)
    task = normalize_task(result.data)
    if task is None:
        return None, _error(
            "habitica_unavailable",
            "Habitica returned an unexpected task.",
            HTTPStatus.BAD_GATEWAY,
        )
    return task, None


def _task_mutation_lock_path(account: LinkedHabiticaAccount, task_id: str) -> Path:
    digest = hashlib.sha256(f"{account.habitica_user_id}\0{task_id}".encode("utf-8")).digest()
    shard = int.from_bytes(digest[:2], "big") % TASK_MUTATION_LOCK_SHARDS
    lock_root = (get_bot_data_path().parent / ".miniapp-task-locks").resolve()
    lock_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(lock_root, 0o700)
    except OSError:
        pass
    # A fixed shard set bounds persistent lock files even when a linked client
    # submits arbitrarily many nonexistent UUIDs.  Collisions only serialize
    # unrelated mutations; they cannot mix account state.
    return lock_root / f"task-{shard:02d}.lock"


def _gameplay_lock_path(account: LinkedHabiticaAccount) -> Path:
    """Return a bounded per-account shard shared by gameplay mutations."""

    return get_gameplay_lock_path(account.habitica_user_id)


@contextmanager
def _acquire_gameplay_task_locks(
    account: LinkedHabiticaAccount,
    task_id: str,
):
    """Serialize scoring against both day refresh and same-task mutations."""

    with acquire_runtime_lock(lock_path=_gameplay_lock_path(account), timeout=0):
        with acquire_runtime_lock(
            lock_path=_task_mutation_lock_path(account, task_id), timeout=0
        ):
            yield


def _duplicate_gameplay_response():
    return _error(
        "duplicate_request",
        "Another gameplay action is already in progress. Please wait.",
        HTTPStatus.CONFLICT,
    )


def _safe_daily_ids(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    safe: list[str] = []
    for item in value:
        try:
            safe.append(validate_task_id(item))
        except TaskValidationError:
            continue
    return safe


def _safe_snapshot_payload(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    raw_profile = value.get("profile")
    raw_stats = value.get("stats")
    if not isinstance(raw_profile, Mapping) or not isinstance(raw_stats, Mapping):
        return None
    level = raw_profile.get("level")
    if level is not None:
        level = _safe_level(level)
    character_class = raw_profile.get("class")
    if character_class is not None and not isinstance(character_class, str):
        character_class = None
    return {
        "profile": {
            "level": level,
            "class": character_class[:64] if isinstance(character_class, str) else None,
        },
        "stats": {
            field: _safe_number(raw_stats.get(field))
            for field in ("hp", "maxHp", "exp", "maxExp", "mp", "maxMp", "gold")
        },
    }


def _gameplay_error_response(error: Any):
    """Map a sanitized gameplay-service error to a stable HTTP response."""

    code = getattr(error, "code", None)
    mapping = {
        "already_refreshed": (
            "already_refreshed",
            "Your Habitica day was already refreshed.",
            HTTPStatus.CONFLICT,
        ),
        "invalid_daily_selection": (
            "invalid_daily_selection",
            "The selected Dailies are no longer eligible for yesterday.",
            HTTPStatus.BAD_REQUEST,
        ),
        "batch_incomplete": (
            "batch_incomplete",
            "Some selected Dailies were recorded safely. Wait briefly, then continue with the remaining selections.",
            HTTPStatus.TOO_MANY_REQUESTS,
        ),
        "daily_score_failed": (
            "daily_score_failed",
            "Some selected Dailies could not be recorded. Cron was not run.",
            HTTPStatus.BAD_GATEWAY,
        ),
        "cron_failed": (
            "cron_failed",
            "Habitica could not safely start the new day.",
            HTTPStatus.BAD_GATEWAY,
        ),
        "day_refresh_required": (
            "day_refresh_required",
            "Review yesterday's Dailies before starting the new day.",
            HTTPStatus.CONFLICT,
        ),
        "habitica_unauthorized": (
            "habitica_unauthorized",
            "Relink your Habitica account with /start.",
            HTTPStatus.UNAUTHORIZED,
        ),
        "habitica_unavailable": (
            "habitica_unavailable",
            "Habitica could not complete this request. Please try again.",
            HTTPStatus.BAD_GATEWAY,
        ),
        "rate_limited": (
            "rate_limited",
            "Habitica is receiving too many requests. Please wait and retry.",
            HTTPStatus.TOO_MANY_REQUESTS,
        ),
        "health_already_full": (
            "health_already_full",
            "Health is already full.",
            HTTPStatus.CONFLICT,
        ),
        "not_enough_gold": (
            "not_enough_gold",
            "There is not enough gold to buy a Health Potion.",
            HTTPStatus.CONFLICT,
        ),
        "purchase_failed": (
            "purchase_failed",
            "Habitica could not safely complete the potion purchase.",
            HTTPStatus.BAD_GATEWAY,
        ),
        "invalid_response": (
            "habitica_unavailable",
            "Habitica returned an unexpected response.",
            HTTPStatus.BAD_GATEWAY,
        ),
        "request_timeout": (
            "habitica_unavailable",
            "Habitica took too long to respond. Please try again.",
            HTTPStatus.GATEWAY_TIMEOUT,
        ),
        "network_timeout": (
            "habitica_unavailable",
            "Habitica took too long to respond. Please try again.",
            HTTPStatus.GATEWAY_TIMEOUT,
        ),
    }
    public_code, message, status = mapping.get(
        code,
        (
            "internal_error",
            "The request could not be completed.",
            HTTPStatus.INTERNAL_SERVER_ERROR,
        ),
    )
    payload: dict[str, Any] = {
        "ok": False,
        "error": {"code": public_code, "message": message},
    }
    unresolved = getattr(error, "unresolved_daily_ids", ())
    safe_ids = _safe_daily_ids(unresolved)
    if safe_ids:
        payload["unresolvedDailyIds"] = safe_ids
    if getattr(error, "outcome_unknown", False):
        payload["reconcileRequired"] = True
    response = _private_json(payload, status)
    retry_after = _retry_after_seconds(getattr(error, "retry_after", None))
    if status == HTTPStatus.TOO_MANY_REQUESTS and retry_after is not None:
        response.headers["Retry-After"] = str(retry_after)
    return response


def _day_refresh_error_response(result: Any):
    response = _gameplay_error_response(result.error)
    partial = getattr(result, "data", None)
    if partial is None:
        return response
    raw_payload = partial.to_payload()
    if not isinstance(raw_payload, Mapping):
        return response

    payload = response.get_json()
    status = raw_payload.get("status")
    if status in {"partial_failure", "batch_incomplete", "refresh_failed", "refreshed"}:
        payload["status"] = status
    raw_day = raw_payload.get("day")
    if isinstance(raw_day, Mapping) and isinstance(raw_day.get("refreshRequired"), bool):
        payload["day"] = {"refreshRequired": raw_day["refreshRequired"]}
    profile = _safe_snapshot_payload(raw_payload.get("profile"))
    if profile is not None:
        payload["profile"] = profile
    scored_ids = _safe_daily_ids(raw_payload.get("scoredDailyIds"))
    unresolved_ids = _safe_daily_ids(raw_payload.get("unresolvedDailyIds"))
    if scored_ids:
        payload["scoredDailyIds"] = scored_ids
    if unresolved_ids:
        payload["unresolvedDailyIds"] = unresolved_ids
    if status == "refreshed":
        payload["reconcileRequired"] = True
        payload["invalidate"] = {
            "profile": True,
            "habits": True,
            "dailies": True,
            "todos": False,
            "avatar": False,
        }
    merged = _private_json(payload, HTTPStatus(response.status_code))
    if "Retry-After" in response.headers:
        merged.headers["Retry-After"] = response.headers["Retry-After"]
    return merged


def _ensure_gameplay_ready(account: LinkedHabiticaAccount):
    """Re-check Habitica's authoritative day gate while the gameplay lock is held."""

    from habitica_gameplay import fetch_day_status

    result = fetch_day_status(*_task_account_args(account))
    if not result.ok:
        return _gameplay_error_response(result.error)
    day = result.data
    if day is None:
        return _error(
            "habitica_unavailable",
            "Habitica returned an unexpected response.",
            HTTPStatus.BAD_GATEWAY,
        )
    if getattr(day, "refresh_required", False) is True:
        return _error(
            "day_refresh_required",
            "Review yesterday's Dailies before starting the new day.",
            HTTPStatus.CONFLICT,
        )
    return None


def _validate_completed_daily_ids(body: Mapping[str, Any]) -> tuple[str, ...]:
    if set(body) != {"completedDailyIds"}:
        raise TaskValidationError("invalid day-refresh body")
    values = body.get("completedDailyIds")
    if not isinstance(values, list):
        raise TaskValidationError("invalid day-refresh selection")
    validated = tuple(validate_task_id(value) for value in values)
    if len(set(validated)) != len(validated):
        raise TaskValidationError("duplicate day-refresh selection")
    return validated


def _require_editable(task: Mapping[str, Any], *, deleting: bool = False):
    capability = "canDelete" if deleting else "canEdit"
    if task.get(capability) is not True:
        return _error(
            "conflict",
            "This linked task can only be changed in Habitica.",
            HTTPStatus.CONFLICT,
        )
    return None


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


@miniapp_blueprint.after_app_request
def _secure_miniapp_response(response):
    if not request.path.startswith("/miniapp/"):
        return response
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Vary"] = "Authorization"
    return response


@miniapp_blueprint.errorhandler(413)
def _miniapp_request_too_large(_error_value):
    return _error(
        "invalid_request",
        "The request body is too large.",
        HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
    )


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


@miniapp_blueprint.get("/api/day-status")
def miniapp_day_status():
    account, error_response = _authenticated_account()
    if error_response is not None:
        return error_response
    assert account is not None

    from habitica_gameplay import fetch_day_status

    result = fetch_day_status(*_task_account_args(account))
    if not result.ok:
        return _gameplay_error_response(result.error)
    day = result.data
    if day is None:
        return _error(
            "habitica_unavailable",
            "Habitica returned an unexpected response.",
            HTTPStatus.BAD_GATEWAY,
        )
    payload = day.to_payload()
    if not isinstance(payload, Mapping):
        return _error(
            "habitica_unavailable",
            "Habitica returned an unexpected response.",
            HTTPStatus.BAD_GATEWAY,
        )
    return _private_json({"ok": True, "day": dict(payload)}, HTTPStatus.OK)


@miniapp_blueprint.post("/api/day-refresh")
def miniapp_day_refresh():
    account, error_response = _authenticated_account()
    if error_response is not None:
        return error_response
    assert account is not None
    try:
        completed_daily_ids = _validate_completed_daily_ids(_request_json_object())
    except TaskValidationError:
        return _error(
            "invalid_daily_selection",
            "The selected Dailies are invalid.",
            HTTPStatus.BAD_REQUEST,
        )

    from habitica_gameplay import refresh_day

    try:
        with acquire_runtime_lock(lock_path=_gameplay_lock_path(account), timeout=0):
            result = refresh_day(*_task_account_args(account), completed_daily_ids)
    except RuntimeLockUnavailable:
        return _duplicate_gameplay_response()

    if not result.ok:
        return _day_refresh_error_response(result)
    refreshed = result.data
    if refreshed is None:
        return _error(
            "habitica_unavailable",
            "Habitica returned an unexpected response.",
            HTTPStatus.BAD_GATEWAY,
        )
    payload = refreshed.to_payload()
    if not isinstance(payload, Mapping):
        return _error(
            "habitica_unavailable",
            "Habitica returned an unexpected response.",
            HTTPStatus.BAD_GATEWAY,
        )
    response_payload = {"ok": True, **dict(payload)}
    response_payload["day"] = {"refreshRequired": False}
    response_payload["invalidate"] = {
        "profile": True,
        "habits": True,
        "dailies": True,
        "todos": False,
        "avatar": False,
    }
    return _private_json(response_payload, HTTPStatus.OK)


@miniapp_blueprint.get("/api/health-potion")
def miniapp_health_potion_status():
    account, error_response = _authenticated_account()
    if error_response is not None:
        return error_response
    assert account is not None

    from habitica_gameplay import get_health_potion_status

    result = get_health_potion_status(
        *_task_account_args(account),
        fetch_content=_cached_potion_content_result,
    )
    if not result.ok:
        return _gameplay_error_response(result.error)
    status = result.data
    if status is None:
        return _error(
            "habitica_unavailable",
            "Habitica returned an unexpected response.",
            HTTPStatus.BAD_GATEWAY,
        )
    payload = status.to_payload()
    if not isinstance(payload, Mapping):
        return _error(
            "habitica_unavailable",
            "Habitica returned an unexpected response.",
            HTTPStatus.BAD_GATEWAY,
        )
    purchase_intent = _issue_potion_purchase_intent(account)
    if purchase_intent is None:
        return _error(
            "intent_unavailable",
            "A purchase confirmation cannot be opened right now. Please try again shortly.",
            HTTPStatus.SERVICE_UNAVAILABLE,
        )
    return _private_json(
        {"ok": True, **dict(payload), "purchaseIntent": purchase_intent},
        HTTPStatus.OK,
    )


@miniapp_blueprint.post("/api/health-potion")
def miniapp_health_potion_purchase():
    account, error_response = _authenticated_account()
    if error_response is not None:
        return error_response
    assert account is not None
    try:
        body = _request_json_object()
        if set(body) != {"purchaseIntent"}:
            raise TaskValidationError("health-potion body must contain one purchase intent")
    except TaskValidationError:
        return _error(
            "invalid_request",
            "The potion request is invalid.",
            HTTPStatus.BAD_REQUEST,
        )

    claim, token_digest, replay = _claim_potion_purchase_intent(
        account,
        body.get("purchaseIntent"),
    )
    if claim == "invalid":
        return _error(
            "invalid_purchase_intent",
            "This purchase confirmation is invalid or expired. Open it again.",
            HTTPStatus.BAD_REQUEST,
        )
    if claim == "in_progress":
        return _duplicate_gameplay_response()
    if claim == "replay":
        assert replay is not None
        return _potion_replay_response(replay)
    assert claim == "claimed" and token_digest is not None

    service_entered = False
    try:
        from habitica_gameplay import purchase_health_potion

        with acquire_runtime_lock(lock_path=_gameplay_lock_path(account), timeout=0):
            service_entered = True
            result = purchase_health_potion(
                *_task_account_args(account),
                fetch_content=_cached_potion_content_result,
            )
    except RuntimeLockUnavailable:
        if not service_entered:
            _restore_potion_purchase_intent(account, token_digest)
            return _duplicate_gameplay_response()
        LOGGER.error("Health Potion purchase service failed unexpectedly")
        response = _error(
            "internal_error",
            "The request could not be completed.",
            HTTPStatus.INTERNAL_SERVER_ERROR,
        )
        return _terminalize_potion_purchase_intent(account, token_digest, response)
    except Exception:
        LOGGER.error("Health Potion purchase service failed unexpectedly")
        if not service_entered:
            _restore_potion_purchase_intent(account, token_digest)
            return _error(
                "internal_error",
                "The request could not be completed.",
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )
        response = _error(
            "internal_error",
            "The request could not be completed.",
            HTTPStatus.INTERNAL_SERVER_ERROR,
        )
        return _terminalize_potion_purchase_intent(account, token_digest, response)

    try:
        if not result.ok:
            response = _gameplay_error_response(result.error)
        else:
            purchased = result.data
            payload = purchased.to_payload() if purchased is not None else None
            if not isinstance(payload, Mapping):
                response = _error(
                    "habitica_unavailable",
                    "Habitica returned an unexpected response.",
                    HTTPStatus.BAD_GATEWAY,
                )
            else:
                response_payload = {"ok": True, **dict(payload)}
                response_payload["invalidate"] = {
                    "profile": True,
                    "avatar": False,
                }
                response = _private_json(response_payload, HTTPStatus.OK)
    except Exception:
        LOGGER.error("Health Potion purchase result could not be serialized")
        response = _error(
            "internal_error",
            "The request could not be completed.",
            HTTPStatus.INTERNAL_SERVER_ERROR,
        )
    return _terminalize_potion_purchase_intent(account, token_digest, response)


@miniapp_blueprint.get("/api/tasks")
def miniapp_tasks_list():
    account, error_response = _authenticated_account()
    if error_response is not None:
        return error_response
    assert account is not None

    task_type = request.args.get("type")
    completed_raw = request.args.get("completed")
    if completed_raw not in (None, "false", "true"):
        return _error(
            "invalid_request",
            "The completed filter is invalid.",
            HTTPStatus.BAD_REQUEST,
        )
    completed = completed_raw == "true"
    try:
        list_type = upstream_list_type(task_type, completed=completed)
    except TaskValidationError:
        return _error(
            "invalid_request",
            "The task type is invalid.",
            HTTPStatus.BAD_REQUEST,
        )

    from Habitica_API import get_tasks_result

    result = get_tasks_result(*_task_account_args(account), list_type, history=False)
    if not result.ok:
        return _habitica_error_response(result)
    tasks = normalize_tasks(result.data)
    if tasks is None:
        return _error(
            "habitica_unavailable",
            "Habitica returned an unexpected task list.",
            HTTPStatus.BAD_GATEWAY,
        )
    return _private_json({"ok": True, "tasks": tasks}, HTTPStatus.OK)


@miniapp_blueprint.get("/api/task-summary")
def miniapp_task_summary():
    account, error_response = _authenticated_account()
    if error_response is not None:
        return error_response
    assert account is not None

    today_raw = request.args.get("today")
    try:
        local_today = date.fromisoformat(today_raw) if isinstance(today_raw, str) else None
    except ValueError:
        local_today = None
    if local_today is None or local_today.isoformat() != today_raw:
        return _error(
            "invalid_request",
            "The local date is invalid.",
            HTTPStatus.BAD_REQUEST,
        )

    from Habitica_API import get_tasks_result

    normalized: dict[str, list[dict[str, Any]]] = {}
    for key, list_type in (
        ("habits", "habits"),
        ("dailies", "dailys"),
        ("active_todos", "todos"),
        ("completed_todos", "completedTodos"),
    ):
        result = get_tasks_result(*_task_account_args(account), list_type, history=False)
        if not result.ok:
            return _habitica_error_response(result)
        tasks = normalize_tasks(result.data)
        if tasks is None:
            return _error(
                "habitica_unavailable",
                "Habitica returned an unexpected task list.",
                HTTPStatus.BAD_GATEWAY,
            )
        normalized[key] = tasks

    summary = quest_log_summary(
        normalized["habits"],
        normalized["dailies"],
        normalized["active_todos"],
        normalized["completed_todos"],
        today=local_today,
    )
    return _private_json({"ok": True, "summary": summary}, HTTPStatus.OK)


@miniapp_blueprint.post("/api/tasks")
def miniapp_task_create():
    account, error_response = _authenticated_account()
    if error_response is not None:
        return error_response
    assert account is not None
    try:
        _task_type, payload, _checklist = validate_task_payload(
            _request_json_object(), creating=True
        )
    except TaskValidationError:
        return _error(
            "invalid_request",
            "The task details are invalid.",
            HTTPStatus.BAD_REQUEST,
        )

    from Habitica_API import create_task_result

    result = create_task_result(*_task_account_args(account), payload)
    if not result.ok:
        return _habitica_error_response(result, mutation=True)
    task = normalize_task(result.data)
    if task is None:
        return _private_json(
            {"ok": True, "task": None, "reloadRequired": True},
            HTTPStatus.CREATED,
        )
    return _private_json({"ok": True, "task": task}, HTTPStatus.CREATED)


@miniapp_blueprint.patch("/api/tasks/<task_id>")
def miniapp_task_update(task_id: str):
    account, error_response = _authenticated_account()
    if error_response is not None:
        return error_response
    assert account is not None
    try:
        task_id = validate_task_id(task_id)
        body = _request_json_object()
    except TaskValidationError:
        return _error("invalid_request", "The task request is invalid.", HTTPStatus.BAD_REQUEST)

    try:
        with acquire_runtime_lock(lock_path=_task_mutation_lock_path(account, task_id)):
            existing, load_error = _load_task(account, task_id)
            if load_error is not None:
                return load_error
            assert existing is not None
            capability_error = _require_editable(existing)
            if capability_error is not None:
                return capability_error
            try:
                if body.get("revision") != existing.get("revision"):
                    return _error(
                        "conflict",
                        "This task changed elsewhere. Refresh before saving.",
                        HTTPStatus.CONFLICT,
                    )
                _task_type, fields, requested_checklist = validate_task_payload(
                    body,
                    creating=False,
                    existing_type=existing["type"],
                )
                if existing["type"] == "daily" and ("repeatDays" in body or "startDate" in body):
                    if existing.get("scheduleEditable") is not True:
                        raise TaskValidationError("advanced schedules are read-only")
                existing_comparison = dict(existing)
                if existing["type"] == "daily":
                    selected_repeat_days = set(existing.get("repeatDays", []))
                    existing_comparison["repeat"] = {
                        day: day in selected_repeat_days
                        for day in ("su", "m", "t", "w", "th", "f", "s")
                    }
                fields = {
                    key: value
                    for key, value in fields.items()
                    if value != existing_comparison.get(key)
                }
                additions: list[dict[str, Any]] = []
                updates: list[dict[str, Any]] = []
                removals: list[str] = []
                if requested_checklist is not None:
                    additions, updates, removals = checklist_changes(
                        existing["checklist"], requested_checklist
                    )
                    if (
                        len(additions) + len(updates) + len(removals)
                        > MAX_CHECKLIST_MUTATIONS_PER_EDIT
                    ):
                        raise TaskValidationError("too many checklist changes")
            except TaskValidationError:
                return _error(
                    "invalid_request",
                    "The task details are invalid.",
                    HTTPStatus.BAD_REQUEST,
                )

            from Habitica_API import (
                add_checklist_item_result,
                delete_checklist_item_result,
                update_checklist_item_result,
                update_task_result,
            )

            current_task = existing
            mutation_applied = False
            # Apply checklist edits first.  If they exhaust a quota or fail,
            # ordinary task fields have not also been changed.  A later field
            # failure still reports partial_update and requires reconciliation.
            checklist_operations = (
                [
                    (
                        add_checklist_item_result,
                        ({"text": item["text"], "completed": item["completed"]},),
                    )
                    for item in additions
                ]
                + [
                    (
                        update_checklist_item_result,
                        (item["id"], {"text": item["text"]}),
                    )
                    for item in updates
                ]
                + [(delete_checklist_item_result, (item_id,)) for item_id in removals]
            )
            for operation, arguments in checklist_operations:
                result = operation(*_task_account_args(account), task_id, *arguments)
                if not result.ok:
                    response = _habitica_error_response(result, mutation=True)
                    if mutation_applied:
                        return _private_json(
                            {
                                "ok": False,
                                "reconcileRequired": True,
                                "error": {
                                    "code": "partial_update",
                                    "message": "Some changes were saved. Refresh before editing again.",
                                },
                            },
                            HTTPStatus.CONFLICT,
                        )
                    return response
                normalized = normalize_task(result.data)
                if normalized is None:
                    return _private_json(
                        {
                            "ok": False,
                            "reconcileRequired": True,
                            "error": {
                                "code": "partial_update",
                                "message": "Some changes were saved. Refresh before editing again.",
                            },
                        },
                        HTTPStatus.CONFLICT,
                    )
                current_task = normalized
                mutation_applied = True

            if fields:
                result = update_task_result(*_task_account_args(account), task_id, fields)
                if not result.ok:
                    if mutation_applied:
                        return _private_json(
                            {
                                "ok": False,
                                "reconcileRequired": True,
                                "error": {
                                    "code": "partial_update",
                                    "message": "Some changes were saved. Refresh before editing again.",
                                },
                            },
                            HTTPStatus.CONFLICT,
                        )
                    return _habitica_error_response(result, mutation=True)
                normalized = normalize_task(result.data)
                if normalized is None:
                    return _private_json(
                        {
                            "ok": False,
                            "reconcileRequired": True,
                            "error": {
                                "code": "partial_update",
                                "message": "The change was saved. Refresh before editing again.",
                            },
                        },
                        HTTPStatus.CONFLICT,
                    )
                current_task = normalized
                mutation_applied = True
            return _private_json({"ok": True, "task": current_task}, HTTPStatus.OK)
    except RuntimeLockUnavailable:
        return _error(
            "conflict",
            "This task is already being changed. Please wait and refresh.",
            HTTPStatus.CONFLICT,
        )


@miniapp_blueprint.delete("/api/tasks/<task_id>")
def miniapp_task_delete(task_id: str):
    account, error_response = _authenticated_account()
    if error_response is not None:
        return error_response
    assert account is not None
    try:
        task_id = validate_task_id(task_id)
    except TaskValidationError:
        return _error("invalid_request", "The task ID is invalid.", HTTPStatus.BAD_REQUEST)

    try:
        with acquire_runtime_lock(lock_path=_task_mutation_lock_path(account, task_id)):
            task, load_error = _load_task(account, task_id)
            if load_error is not None:
                return load_error
            assert task is not None
            capability_error = _require_editable(task, deleting=True)
            if capability_error is not None:
                return capability_error
            from Habitica_API import delete_task_result

            result = delete_task_result(*_task_account_args(account), task_id)
            if not result.ok:
                return _habitica_error_response(result, mutation=True)
            return _private_json(
                {"ok": True, "deleted": {"id": task_id, "type": task["type"]}},
                HTTPStatus.OK,
            )
    except RuntimeLockUnavailable:
        return _error(
            "conflict",
            "This task is already being changed. Please wait and refresh.",
            HTTPStatus.CONFLICT,
        )


@miniapp_blueprint.post("/api/tasks/<task_id>/score")
def miniapp_task_score(task_id: str):
    account, error_response = _authenticated_account()
    if error_response is not None:
        return error_response
    assert account is not None
    try:
        task_id = validate_task_id(task_id)
        body = _request_json_object()
        direction = body.get("direction")
        if set(body) != {"direction"} or direction not in {"up", "down"}:
            raise TaskValidationError("invalid scoring direction")
    except TaskValidationError:
        return _error("invalid_request", "The scoring request is invalid.", HTTPStatus.BAD_REQUEST)

    try:
        with _acquire_gameplay_task_locks(account, task_id):
            gate_error = _ensure_gameplay_ready(account)
            if gate_error is not None:
                return gate_error
            task, load_error = _load_task(account, task_id)
            if load_error is not None:
                return load_error
            assert task is not None
            if task["type"] == "habit":
                if task.get(direction) is not True:
                    return _error(
                        "invalid_request",
                        "This Habit does not support that direction.",
                        HTTPStatus.BAD_REQUEST,
                    )
            else:
                expected_direction = "down" if task.get("completed") is True else "up"
                if direction != expected_direction:
                    return _error(
                        "conflict",
                        "This task changed elsewhere. Refresh and try again.",
                        HTTPStatus.CONFLICT,
                    )

            from Habitica_API import get_task_result, score_task_result

            result = score_task_result(*_task_account_args(account), task_id, direction)
            if not result.ok:
                return _habitica_error_response(result, mutation=True)
            profile_patch = normalize_score_profile_patch(result.data)
            reconciled = get_task_result(*_task_account_args(account), task_id)
            if reconciled.ok:
                normalized = normalize_task(reconciled.data)
                if normalized is not None:
                    if task["type"] == "habit":
                        counter_name = "counterUp" if direction == "up" else "counterDown"
                        expected_counter = task[counter_name] + 1
                        if normalized[counter_name] < expected_counter:
                            return _private_json(
                                {
                                    "ok": True,
                                    "task": optimistic_scored_task(task, direction),
                                    "profilePatch": profile_patch,
                                    "reloadRequired": True,
                                },
                                HTTPStatus.OK,
                            )
                    if task["type"] in {"daily", "todo"} and normalized.get("completed") is not (
                        direction == "up"
                    ):
                        return _private_json(
                            {
                                "ok": False,
                                "reconcileRequired": True,
                                "error": {
                                    "code": "conflict",
                                    "message": "The task changed elsewhere. Refresh before trying again.",
                                },
                            },
                            HTTPStatus.CONFLICT,
                        )
                    return _private_json(
                        {
                            "ok": True,
                            "task": normalized,
                            "profilePatch": profile_patch,
                        },
                        HTTPStatus.OK,
                    )
            return _private_json(
                {
                    "ok": True,
                    "task": optimistic_scored_task(task, direction),
                    "profilePatch": profile_patch,
                    "reloadRequired": True,
                },
                HTTPStatus.OK,
            )
    except RuntimeLockUnavailable:
        return _duplicate_gameplay_response()


@miniapp_blueprint.post("/api/tasks/<task_id>/checklist/<item_id>/score")
def miniapp_checklist_score(task_id: str, item_id: str):
    account, error_response = _authenticated_account()
    if error_response is not None:
        return error_response
    assert account is not None
    try:
        task_id = validate_task_id(task_id)
        item_id = validate_task_id(item_id)
        body = _request_json_object()
        completed = body.get("completed")
        if set(body) != {"completed"} or not isinstance(completed, bool):
            raise TaskValidationError("invalid checklist state")
    except TaskValidationError:
        return _error(
            "invalid_request",
            "The checklist request is invalid.",
            HTTPStatus.BAD_REQUEST,
        )

    try:
        with _acquire_gameplay_task_locks(account, task_id):
            gate_error = _ensure_gameplay_ready(account)
            if gate_error is not None:
                return gate_error
            task, load_error = _load_task(account, task_id)
            if load_error is not None:
                return load_error
            assert task is not None
            if task["type"] not in {"daily", "todo"}:
                return _error(
                    "invalid_request",
                    "Habits do not have checklist items.",
                    HTTPStatus.BAD_REQUEST,
                )
            item = next(
                (candidate for candidate in task["checklist"] if candidate["id"] == item_id),
                None,
            )
            if item is None:
                return _error(
                    "task_not_found",
                    "This checklist item is no longer available.",
                    HTTPStatus.NOT_FOUND,
                )
            if item["completed"] is completed:
                return _private_json({"ok": True, "task": task}, HTTPStatus.OK)

            capability_error = _require_editable(task)
            if capability_error is not None:
                return capability_error
            if item.get("textTruncated") is True:
                return _error(
                    "conflict",
                    "This checklist item must be changed in Habitica.",
                    HTTPStatus.CONFLICT,
                )

            from Habitica_API import update_checklist_item_result

            # Habitica's score endpoint toggles, so a concurrent external
            # change can invert the caller's requested state. A direct partial
            # update is idempotent and preserves the item's current text.
            result = update_checklist_item_result(
                *_task_account_args(account),
                task_id,
                item_id,
                {"completed": completed},
            )
            if not result.ok:
                return _habitica_error_response(result, mutation=True)
            normalized = normalize_task(result.data)
            if normalized is None:
                return _private_json(
                    {"ok": True, "task": None, "reloadRequired": True},
                    HTTPStatus.OK,
                )
            updated_item = next(
                (candidate for candidate in normalized["checklist"] if candidate["id"] == item_id),
                None,
            )
            if updated_item is None or updated_item["completed"] is not completed:
                return _private_json(
                    {
                        "ok": False,
                        "reconcileRequired": True,
                        "error": {
                            "code": "conflict",
                            "message": "The checklist changed elsewhere. Refresh before trying again.",
                        },
                    },
                    HTTPStatus.CONFLICT,
                )
            return _private_json({"ok": True, "task": normalized}, HTTPStatus.OK)
    except RuntimeLockUnavailable:
        return _duplicate_gameplay_response()
