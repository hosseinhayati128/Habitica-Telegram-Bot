from __future__ import annotations

import hashlib
import hmac
import json
from urllib.parse import quote, urlencode

import pytest

from miniapp_auth import (
    MiniAppAuthConfigurationError,
    MiniAppAuthError,
    authenticate_authorization_header,
    validate_init_data,
)

FAKE_BOT_TOKEN = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"
NOW = 2_000_000_000


@pytest.fixture(autouse=True)
def isolated_miniapp_environment(monkeypatch):
    monkeypatch.delenv("MINIAPP_DEV_MODE", raising=False)
    monkeypatch.delenv("MINIAPP_DEV_TELEGRAM_USER_ID", raising=False)
    monkeypatch.delenv("MINIAPP_AUTH_MAX_AGE_SECONDS", raising=False)


def signed_init_data(
    *,
    token: str = FAKE_BOT_TOKEN,
    user: object = None,
    auth_date: int | str = NOW,
    extra: dict[str, str] | None = None,
) -> str:
    if user is None:
        user = {"id": 424242, "first_name": "Test User"}
    fields = {
        "auth_date": str(auth_date),
        "query_id": "AAH_fake-query",
        "user": json.dumps(user, separators=(",", ":"), ensure_ascii=False),
        **(extra or {}),
    }
    data_check = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    return urlencode({**fields, "hash": digest})


def test_valid_init_data_returns_only_signed_identity():
    raw = signed_init_data(extra={"chat_type": "sender", "start_param": "home"})

    identity = validate_init_data(raw, FAKE_BOT_TOKEN, now=NOW)

    assert identity.telegram_user_id == 424242
    assert identity.auth_date == NOW
    assert not hasattr(identity, "first_name")


def test_field_order_and_percent_encoded_values_do_not_change_validation():
    user_json = r'{"id":424242,"first_name":"A + B","photo_url":"https:\/\/example.test\/a?x=1&y=2"}'
    fields = {
        "user": user_json,
        "auth_date": str(NOW),
        "signature": "fake-signature==",
    }
    check = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hmac.new(b"WebAppData", FAKE_BOT_TOKEN.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    raw = "&".join(
        [
            f"signature={quote(fields['signature'], safe='')}",
            f"user={quote(user_json, safe='')}",
            f"hash={digest}",
            f"auth_date={NOW}",
        ]
    )

    assert validate_init_data(raw, FAKE_BOT_TOKEN, now=NOW).telegram_user_id == 424242


def test_query_id_is_not_required():
    raw = signed_init_data()
    without_query_id = "&".join(part for part in raw.split("&") if not part.startswith("query_id="))
    fields = dict(part.split("=", 1) for part in without_query_id.split("&"))
    # Re-sign after removing the optional field.
    decoded_user = json.dumps({"id": 424242, "first_name": "Test User"}, separators=(",", ":"))
    check = f"auth_date={NOW}\nuser={decoded_user}"
    secret = hmac.new(b"WebAppData", FAKE_BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    no_query = urlencode({"auth_date": str(NOW), "user": decoded_user, "hash": fields["hash"]})

    assert validate_init_data(no_query, FAKE_BOT_TOKEN, now=NOW).telegram_user_id == 424242


@pytest.mark.parametrize(
    "mutation",
    [
        lambda raw: raw.replace("424242", "424243"),
        lambda raw: raw.replace("auth_date=2000000000", "auth_date=2000000001"),
        lambda raw: raw[:-1] + ("0" if raw[-1] != "0" else "1"),
    ],
)
def test_tampering_is_rejected(mutation):
    with pytest.raises(MiniAppAuthError):
        validate_init_data(mutation(signed_init_data()), FAKE_BOT_TOKEN, now=NOW)


def test_expired_and_implausibly_future_data_are_rejected():
    with pytest.raises(MiniAppAuthError):
        validate_init_data(
            signed_init_data(auth_date=NOW - 3_601),
            FAKE_BOT_TOKEN,
            now=NOW,
            max_age_seconds=3_600,
        )
    with pytest.raises(MiniAppAuthError):
        validate_init_data(signed_init_data(auth_date=NOW + 61), FAKE_BOT_TOKEN, now=NOW)


@pytest.mark.parametrize(
    "user",
    [
        [],
        "not-an-object",
        {},
        {"id": True},
        {"id": "424242"},
        {"id": 0},
        {"id": -4},
    ],
)
def test_malformed_signed_user_is_rejected(user):
    with pytest.raises(MiniAppAuthError):
        validate_init_data(signed_init_data(user=user), FAKE_BOT_TOKEN, now=NOW)


def test_invalid_user_json_is_rejected_after_valid_hmac():
    fields = {"auth_date": str(NOW), "user": "{"}
    check = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hmac.new(b"WebAppData", FAKE_BOT_TOKEN.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    raw = urlencode({**fields, "hash": digest})

    with pytest.raises(MiniAppAuthError):
        validate_init_data(raw, FAKE_BOT_TOKEN, now=NOW)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "auth_date",
        "auth_date=1&user=%ZZ&hash=" + "0" * 64,
        "auth_date=1&auth_date=2&user=%7B%22id%22%3A1%7D&hash=" + "0" * 64,
        "auth_date=1&user=%7B%22id%22%3A1%7D&hash=" + "0" * 64 + "&hash=" + "0" * 64,
        "auth_date=1&user=%FF&hash=" + "0" * 64,
        "auth_date=1&user=%7B%22id%22%3A1%7D&hash=not-hex",
    ],
)
def test_malformed_query_data_is_rejected(raw):
    with pytest.raises(MiniAppAuthError):
        validate_init_data(raw, FAKE_BOT_TOKEN, now=NOW)


def test_header_authentication_uses_only_tma_authorization(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", FAKE_BOT_TOKEN)
    raw = signed_init_data()

    identity = authenticate_authorization_header(f"tma {raw}", now=NOW)

    assert identity.telegram_user_id == 424242
    for header in (None, "", raw, f"Bearer {raw}", f"tma  {raw}"):
        with pytest.raises(MiniAppAuthError):
            authenticate_authorization_header(header, now=NOW)


def test_development_identity_requires_both_explicit_settings(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("MINIAPP_DEV_TELEGRAM_USER_ID", "9001")
    with pytest.raises(MiniAppAuthError):
        authenticate_authorization_header(None)

    monkeypatch.setenv("MINIAPP_DEV_MODE", "1")
    assert authenticate_authorization_header(None).telegram_user_id == 9001

    monkeypatch.setenv("MINIAPP_DEV_TELEGRAM_USER_ID", "caller-supplied")
    with pytest.raises(MiniAppAuthConfigurationError):
        authenticate_authorization_header(None)


def test_present_invalid_header_never_falls_back_to_development_mode(monkeypatch):
    monkeypatch.setenv("MINIAPP_DEV_MODE", "1")
    monkeypatch.setenv("MINIAPP_DEV_TELEGRAM_USER_ID", "9001")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", FAKE_BOT_TOKEN)

    with pytest.raises(MiniAppAuthError):
        authenticate_authorization_header("tma invalid")


def test_mini_app_derivation_is_not_login_widget_derivation():
    raw = signed_init_data()
    fields = dict(parse_part.split("=", 1) for parse_part in raw.split("&"))
    wrong_secret = hashlib.sha256(FAKE_BOT_TOKEN.encode()).digest()
    decoded_user = json.dumps({"id": 424242, "first_name": "Test User"}, separators=(",", ":"))
    check = f"auth_date={NOW}\nquery_id=AAH_fake-query\nuser={decoded_user}"
    fields["hash"] = hmac.new(wrong_secret, check.encode(), hashlib.sha256).hexdigest()
    wrongly_signed = urlencode(
        {
            "auth_date": str(NOW),
            "query_id": "AAH_fake-query",
            "user": decoded_user,
            "hash": fields["hash"],
        }
    )

    with pytest.raises(MiniAppAuthError):
        validate_init_data(wrongly_signed, FAKE_BOT_TOKEN, now=NOW)
