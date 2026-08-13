"""Strict Telegram Mini App init-data authentication.

The browser sends the untouched ``Telegram.WebApp.initData`` value in an
``Authorization: tma ...`` header.  This module validates Telegram's bot-token
HMAC before it reads the signed user object or timestamp.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import parse_qsl

AUTHORIZATION_SCHEME = "tma"
DEFAULT_AUTH_MAX_AGE_SECONDS = 3_600
MAX_AUTH_MAX_AGE_SECONDS = 86_400
AUTH_FUTURE_SKEW_SECONDS = 60
MAX_INIT_DATA_BYTES = 16_384
MAX_INIT_DATA_FIELDS = 32

_INVALID_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_DECIMAL_TIMESTAMP = re.compile(r"[0-9]{1,12}\Z")
_HEX_DIGEST = re.compile(r"[0-9A-Fa-f]{64}\Z")


class MiniAppAuthError(ValueError):
    """Raised when the request cannot be authenticated as a Telegram user."""


class MiniAppAuthConfigurationError(RuntimeError):
    """Raised when server-side Mini App authentication is not configured."""


@dataclass(frozen=True, slots=True)
class TelegramMiniAppIdentity:
    """The only identity fields API handlers need from signed init data."""

    telegram_user_id: int
    auth_date: int


def _configured_max_age() -> int:
    raw_value = os.environ.get("MINIAPP_AUTH_MAX_AGE_SECONDS")
    if raw_value is None:
        return DEFAULT_AUTH_MAX_AGE_SECONDS
    try:
        value = int(raw_value, 10)
    except ValueError:
        return DEFAULT_AUTH_MAX_AGE_SECONDS
    if value <= 0:
        return DEFAULT_AUTH_MAX_AGE_SECONDS
    return min(value, MAX_AUTH_MAX_AGE_SECONDS)


def _parse_init_data(raw_init_data: str) -> dict[str, str]:
    if not isinstance(raw_init_data, str) or not raw_init_data:
        raise MiniAppAuthError
    try:
        encoded = raw_init_data.encode("ascii")
    except UnicodeEncodeError as exc:
        raise MiniAppAuthError from exc
    if len(encoded) > MAX_INIT_DATA_BYTES:
        raise MiniAppAuthError
    if _INVALID_PERCENT_ESCAPE.search(raw_init_data):
        raise MiniAppAuthError

    try:
        pairs = parse_qsl(
            raw_init_data,
            keep_blank_values=True,
            strict_parsing=True,
            encoding="utf-8",
            errors="strict",
            max_num_fields=MAX_INIT_DATA_FIELDS,
        )
    except (UnicodeDecodeError, ValueError) as exc:
        raise MiniAppAuthError from exc

    fields: dict[str, str] = {}
    for key, value in pairs:
        if not key or key in fields or "\n" in key or "\r" in key:
            raise MiniAppAuthError
        if "\n" in value or "\r" in value:
            raise MiniAppAuthError
        fields[key] = value
    return fields


def validate_init_data(
    raw_init_data: str,
    bot_token: str,
    *,
    now: float | None = None,
    max_age_seconds: int | None = None,
) -> TelegramMiniAppIdentity:
    """Validate one Telegram Mini App credential and return its signed user ID."""

    if not isinstance(bot_token, str) or not bot_token:
        raise MiniAppAuthConfigurationError

    fields = _parse_init_data(raw_init_data)
    received_hash = fields.pop("hash", None)
    if not isinstance(received_hash, str) or not _HEX_DIGEST.fullmatch(received_hash):
        raise MiniAppAuthError

    data_check_string = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret_key = hmac.new(
        b"WebAppData",
        bot_token.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    expected_hash = hmac.new(
        secret_key,
        data_check_string.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    try:
        received_digest = bytes.fromhex(received_hash)
    except ValueError as exc:
        raise MiniAppAuthError from exc
    if not hmac.compare_digest(expected_hash, received_digest):
        raise MiniAppAuthError

    raw_auth_date = fields.get("auth_date")
    if not isinstance(raw_auth_date, str) or not _DECIMAL_TIMESTAMP.fullmatch(raw_auth_date):
        raise MiniAppAuthError
    auth_date = int(raw_auth_date, 10)
    current_time = time.time() if now is None else now
    max_age = _configured_max_age() if max_age_seconds is None else max_age_seconds
    if not isinstance(max_age, int) or isinstance(max_age, bool) or max_age <= 0:
        raise MiniAppAuthConfigurationError
    if auth_date > current_time + AUTH_FUTURE_SKEW_SECONDS:
        raise MiniAppAuthError
    if current_time - auth_date > max_age:
        raise MiniAppAuthError

    raw_user = fields.get("user")
    if not isinstance(raw_user, str) or not raw_user:
        raise MiniAppAuthError
    try:
        user = json.loads(raw_user)
    except (TypeError, ValueError) as exc:
        raise MiniAppAuthError from exc
    if not isinstance(user, dict):
        raise MiniAppAuthError
    telegram_user_id = user.get("id")
    if (
        not isinstance(telegram_user_id, int)
        or isinstance(telegram_user_id, bool)
        or telegram_user_id <= 0
    ):
        raise MiniAppAuthError

    return TelegramMiniAppIdentity(
        telegram_user_id=telegram_user_id,
        auth_date=auth_date,
    )


def _development_identity(getenv: Callable[[str], str | None]) -> TelegramMiniAppIdentity:
    if getenv("MINIAPP_DEV_MODE") != "1":
        raise MiniAppAuthError
    raw_user_id = getenv("MINIAPP_DEV_TELEGRAM_USER_ID")
    if not raw_user_id or not raw_user_id.isascii() or not raw_user_id.isdecimal():
        raise MiniAppAuthConfigurationError
    user_id = int(raw_user_id, 10)
    if user_id <= 0:
        raise MiniAppAuthConfigurationError
    return TelegramMiniAppIdentity(telegram_user_id=user_id, auth_date=0)


def authenticate_authorization_header(
    authorization: str | None,
    *,
    getenv: Callable[[str], str | None] = os.environ.get,
    now: float | None = None,
) -> TelegramMiniAppIdentity:
    """Authenticate the canonical HTTP header, or explicit local dev mode.

    Development mode is considered only when the header is absent.  A caller
    cannot submit a malformed production credential and silently fall back to
    a development identity.
    """

    if authorization is None:
        return _development_identity(getenv)
    prefix, separator, raw_init_data = authorization.partition(" ")
    if separator != " " or prefix.lower() != AUTHORIZATION_SCHEME or not raw_init_data:
        raise MiniAppAuthError
    if raw_init_data.startswith(" "):
        raise MiniAppAuthError

    bot_token = getenv("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        raise MiniAppAuthConfigurationError
    return validate_init_data(raw_init_data, bot_token, now=now)
