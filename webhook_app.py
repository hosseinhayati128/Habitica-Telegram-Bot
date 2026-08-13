"""Synchronous Flask/WSGI boundary for Telegram updates and reminder ticks."""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
from http import HTTPStatus
from typing import Any

from flask import Flask, jsonify, make_response, request
from telegram import Update

from habitica_bot import build_application, run_reminder_tick
from miniapp_backend import miniapp_blueprint
from runtime_lock import RuntimeLockUnavailable, acquire_runtime_lock

LOGGER = logging.getLogger(__name__)

DEFAULT_MAX_WEBHOOK_BODY_BYTES = 1_048_576
RECENT_UPDATE_IDS_KEY = "_telegram_webhook_recent_update_ids_v1"
MAX_RECENT_UPDATE_IDS = 512

flask_app = Flask(__name__)
flask_app.config["MAX_CONTENT_LENGTH"] = DEFAULT_MAX_WEBHOOK_BODY_BYTES
flask_app.register_blueprint(miniapp_blueprint)


class InvalidTelegramUpdate(ValueError):
    """Raised when JSON cannot be decoded into a Telegram Update."""


def _plain_response(
    body: str,
    status: HTTPStatus,
    *,
    no_store: bool = False,
    retry_after: bool = False,
):
    response = make_response(body, status)
    response.mimetype = "text/plain"
    if no_store:
        response.headers["Cache-Control"] = "no-store"
    if retry_after:
        response.headers["Retry-After"] = "1"
    return response


def _secure_equals(provided: str | None, expected: str | None) -> bool:
    if not provided or not expected:
        return False
    return secrets.compare_digest(
        provided.encode("utf-8"),
        expected.encode("utf-8"),
    )


def _webhook_secret_is_valid() -> bool:
    expected = os.environ.get("TELEGRAM_WEBHOOK_SECRET")
    if not expected:
        # Fail closed by default.  The compatibility escape hatch exists only
        # for a short migration window while setWebhook(secret_token=...) is
        # being configured; never enable it on an internet-facing steady state.
        compatibility = os.environ.get(
            "ALLOW_INSECURE_WEBHOOK_WITHOUT_SECRET",
            "false",
        )
        return compatibility.strip().lower() in {"1", "true", "yes", "on"}
    return _secure_equals(
        request.headers.get("X-Telegram-Bot-Api-Secret-Token"),
        expected,
    )


def _legacy_tick_query_auth_enabled() -> bool:
    """Whether the temporary ``?token=`` scheduler compatibility path is on.

    Header authentication is preferred because query strings commonly appear
    in access logs. Existing jobs keep working by default; after migrating a
    scheduler to ``Authorization`` or ``X-Tick-Token``, set
    ``ALLOW_LEGACY_TICK_QUERY_TOKEN=false`` and rotate ``TICK_TOKEN``.
    """

    value = os.environ.get("ALLOW_LEGACY_TICK_QUERY_TOKEN", "true")
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _provided_tick_token() -> str | None:
    # A present header always wins, including when malformed/empty. This keeps
    # an invalid header from silently falling back to a token-bearing URL.
    header_token = request.headers.get("X-Tick-Token")
    if header_token is not None:
        return header_token

    authorization = request.headers.get("Authorization")
    if authorization is not None:
        parts = authorization.split(None, 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return None
        return parts[1].strip()

    if _legacy_tick_query_auth_enabled():
        return request.args.get("token")
    return None


def _tick_auth_is_valid() -> bool:
    return _secure_equals(
        _provided_tick_token(),
        os.environ.get("TICK_TOKEN"),
    )


def _recent_update_ids(bot_data: dict[str, Any]) -> list[int]:
    stored = bot_data.get(RECENT_UPDATE_IDS_KEY)
    if not isinstance(stored, (list, tuple)):
        return []
    recent = [value for value in stored if isinstance(value, int) and not isinstance(value, bool)]
    return recent[-MAX_RECENT_UPDATE_IDS:]


def _remember_update_id(bot_data: dict[str, Any], update_id: int) -> None:
    recent = [value for value in _recent_update_ids(bot_data) if value != update_id]
    recent.append(update_id)
    bot_data[RECENT_UPDATE_IDS_KEY] = recent[-MAX_RECENT_UPDATE_IDS:]


async def _run_tick() -> dict[str, Any]:
    application = build_application(
        register_commands=False,
        persistence_on_flush=True,
    )
    async with application:
        return await run_reminder_tick(application)


async def _handle_update(update_json: dict[str, Any]) -> str:
    """Process one update with a deliberate at-most-once acknowledgement.

    The surrounding WSGI route serializes this complete transaction across
    workers. Once dispatch begins, failures are logged without their message
    text and the HTTP request is still acknowledged. Replaying an update after
    an ambiguous score/purchase failure is less safe than dropping it.
    """

    application = build_application(
        register_commands=False,
        persistence_on_flush=True,
    )

    try:
        update = Update.de_json(update_json, application.bot)
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidTelegramUpdate from exc

    update_id = getattr(update, "update_id", None)
    if (
        not isinstance(update_id, int)
        or isinstance(update_id, bool)
        or update_id != update_json["update_id"]
    ):
        raise InvalidTelegramUpdate

    acknowledge = False
    try:
        async with application:
            if update_id in _recent_update_ids(application.bot_data):
                acknowledge = True
                return "duplicate"

            acknowledge = True
            try:
                await application.process_update(update)
            except Exception as exc:  # noqa: BLE001
                # PTB normally routes handler failures to its on_error hook.
                LOGGER.error(
                    "Telegram update dispatch failed (%s)",
                    type(exc).__name__,
                )
            finally:
                _remember_update_id(application.bot_data, update_id)
                try:
                    # bot_data is always part of PTB's persistence update. The
                    # context-manager shutdown performs the final on-flush save.
                    await application.update_persistence()
                except Exception as exc:  # noqa: BLE001
                    LOGGER.error(
                        "Telegram update persistence failed after dispatch (%s)",
                        type(exc).__name__,
                    )
    except Exception as exc:
        if acknowledge:
            LOGGER.error(
                "Telegram application cleanup failed after dispatch (%s)",
                type(exc).__name__,
            )
            return "acknowledged_with_error"
        raise

    return "processed"


def _safe_nonnegative_int(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return 0


def _aggregate_tick_result(result: dict[str, Any]) -> dict[str, Any]:
    """Whitelist aggregate fields so future tick details cannot leak users."""

    errors = _safe_nonnegative_int(result.get("errors"))
    persistence_errors = _safe_nonnegative_int(result.get("persistence_errors"))
    reported_ok = result.get("ok") is not False
    if "persistence_updated" in result:
        persistence_ok = result.get("persistence_updated") is not False
    else:
        persistence_ok = result.get("persistence_ok") is not False
    return {
        "ok": reported_ok and errors == 0 and persistence_errors == 0 and persistence_ok,
        "sent": _safe_nonnegative_int(result.get("sent")),
        "users_checked": _safe_nonnegative_int(result.get("users_checked")),
        "errors": errors,
        "persistence_errors": persistence_errors,
        "persistence_ok": persistence_ok,
        "window_seconds": _safe_nonnegative_int(result.get("window_seconds")),
    }


def _tick_json_response(payload: dict[str, Any], status: HTTPStatus):
    response = make_response(jsonify(payload), status)
    response.headers["Cache-Control"] = "no-store"
    return response


@flask_app.errorhandler(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
def request_too_large(_error):
    return _plain_response("Payload Too Large", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)


@flask_app.get("/tick")
def tick():
    if not _tick_auth_is_valid():
        return _plain_response(
            "Forbidden",
            HTTPStatus.FORBIDDEN,
            no_store=True,
        )

    try:
        with acquire_runtime_lock():
            result = asyncio.run(_run_tick())
    except RuntimeLockUnavailable:
        return _tick_json_response({"ok": False}, HTTPStatus.SERVICE_UNAVAILABLE)
    except Exception as exc:  # noqa: BLE001
        LOGGER.error("Reminder tick transaction failed (%s)", type(exc).__name__)
        return _tick_json_response({"ok": False}, HTTPStatus.SERVICE_UNAVAILABLE)

    return _tick_json_response(_aggregate_tick_result(result), HTTPStatus.OK)


@flask_app.post("/telegram-webhook")
def telegram_webhook():
    if not _webhook_secret_is_valid():
        return _plain_response("Forbidden", HTTPStatus.FORBIDDEN)

    if not request.is_json:
        return _plain_response(
            "Unsupported Media Type",
            HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
        )

    update_json = request.get_json(silent=True)
    if not isinstance(update_json, dict):
        return _plain_response("Bad Request", HTTPStatus.BAD_REQUEST)

    update_id = update_json.get("update_id")
    if not isinstance(update_id, int) or isinstance(update_id, bool):
        return _plain_response("Bad Request", HTTPStatus.BAD_REQUEST)

    try:
        with acquire_runtime_lock():
            asyncio.run(_handle_update(update_json))
    except RuntimeLockUnavailable:
        return _plain_response(
            "Service Unavailable",
            HTTPStatus.SERVICE_UNAVAILABLE,
            retry_after=True,
        )
    except InvalidTelegramUpdate:
        return _plain_response("Bad Request", HTTPStatus.BAD_REQUEST)
    except Exception as exc:  # noqa: BLE001
        LOGGER.error("Telegram webhook transaction failed (%s)", type(exc).__name__)
        return _plain_response(
            "Service Unavailable",
            HTTPStatus.SERVICE_UNAVAILABLE,
            retry_after=True,
        )

    return _plain_response("OK", HTTPStatus.OK)
