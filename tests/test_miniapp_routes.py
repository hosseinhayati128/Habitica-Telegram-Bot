from __future__ import annotations

import hashlib
import hmac
import json
import pickle
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlencode

import pytest
from flask import Flask

import miniapp_backend as backend

FAKE_BOT_TOKEN = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"
NOW = 2_000_000_000
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"safe-fake-image"


def auth_header(user_id: int = 424242) -> dict[str, str]:
    fields = {
        "auth_date": str(NOW),
        "query_id": "AAH_fake-query",
        "user": json.dumps({"id": user_id, "first_name": "Test"}, separators=(",", ":")),
    }
    check = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hmac.new(b"WebAppData", FAKE_BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return {"Authorization": f"tma {urlencode(fields)}"}


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", FAKE_BOT_TOKEN)
    monkeypatch.setenv("MINIAPP_AUTH_MAX_AGE_SECONDS", "3600")
    monkeypatch.setenv("MINIAPP_AVATAR_REFRESH_COOLDOWN_SECONDS", "0")
    monkeypatch.delenv("MINIAPP_DEV_MODE", raising=False)
    monkeypatch.delenv("MINIAPP_DEV_TELEGRAM_USER_ID", raising=False)
    application = Flask(
        "miniapp-tests",
        template_folder=str(Path(backend.__file__).resolve().parent / "templates"),
        static_folder=str(Path(backend.__file__).resolve().parent / "static"),
    )
    application.config.update(TESTING=True)
    application.register_blueprint(backend.miniapp_blueprint)
    return application


@pytest.fixture
def account():
    return backend.LinkedHabiticaAccount(
        habitica_user_id="private-habitica-user-id",
        habitica_api_key="private-habitica-api-key",
    )


@pytest.fixture
def frozen_time(monkeypatch):
    monkeypatch.setattr("miniapp_auth.time.time", lambda: NOW)


def habitica_user() -> dict:
    return {
        "profile": {"name": "h128", "bio": "must stay private"},
        "auth": {"local": {"username": "h128", "email": "private@example.test"}},
        "stats": {
            "lvl": 112,
            "class": "warrior",
            "hp": 37.8,
            "maxHealth": 50,
            "exp": 3401,
            "toNextLevel": 4400,
            "mp": 240,
            "maxMP": 330,
            "gp": 123.4,
            "buffs": {"str": 999},
        },
        "preferences": {"disableClasses": False},
        "flags": {"classSelected": True},
        "items": {"gear": {"equipped": {"weapon": "private-item"}}},
        "apiKey": "must-never-leak",
        "_id": "must-never-leak",
    }


def test_index_is_registered_with_existing_app_and_uses_official_client_script(app):
    client = app.test_client()

    response = client.get("/miniapp/")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "https://telegram.org/js/telegram-web-app.js?63" in body
    assert 'id="profile-card"' in body
    assert 'data-tab="habits"' in body
    assert 'data-tab="dailies"' in body
    assert 'data-tab="todos"' in body
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_missing_authentication_is_401_before_persistence_lookup(app, monkeypatch):
    looked_up = False

    def unexpected_lookup(_telegram_user_id):
        nonlocal looked_up
        looked_up = True
        raise AssertionError("persistence must not be read before authentication")

    monkeypatch.setattr(backend, "load_linked_habitica_account", unexpected_lookup)

    response = app.test_client().get("/miniapp/api/me")

    assert response.status_code == 401
    assert response.json == {
        "ok": False,
        "error": {
            "code": "invalid_telegram_session",
            "message": "Open this app from Telegram and try again.",
        },
    }
    assert looked_up is False
    assert response.headers["Cache-Control"] == "no-store"


def test_unlinked_user_gets_meaningful_conflict(app, monkeypatch, frozen_time):
    seen = []
    monkeypatch.setattr(
        backend,
        "load_linked_habitica_account",
        lambda telegram_user_id: seen.append(telegram_user_id),
    )

    response = app.test_client().get("/miniapp/api/me", headers=auth_header())

    assert response.status_code == 409
    assert response.json["error"]["code"] == "habitica_not_linked"
    assert "/start" in response.json["error"]["message"]
    assert seen == [424242]


def test_profile_response_is_normalized_and_strictly_whitelisted(
    app, monkeypatch, frozen_time, account
):
    monkeypatch.setattr(backend, "load_linked_habitica_account", lambda _user_id: account)
    monkeypatch.setattr(backend, "get_habitica_status", lambda _account: habitica_user())

    response = app.test_client().get("/miniapp/api/me", headers=auth_header())

    assert response.status_code == 200
    assert response.json == {
        "ok": True,
        "profile": {
            "displayName": "h128",
            "username": "h128",
            "level": 112,
            "class": "warrior",
            "classLabel": "Warrior",
            "hasClass": True,
        },
        "stats": {
            "hp": 37.8,
            "maxHp": 50,
            "exp": 3401,
            "maxExp": 4400,
            "mp": 240,
            "maxMp": 330,
            "gold": 123.4,
        },
    }
    serialized = response.get_data(as_text=True)
    for secret in (
        "private-habitica-user-id",
        "private-habitica-api-key",
        "private@example.test",
        "must-never-leak",
        "private-item",
        "buffs",
    ):
        assert secret not in serialized
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Vary"] == "Authorization"


def test_malformed_habitica_fields_are_safe_and_class_defaults_do_not_unlock_mana(
    app, monkeypatch, frozen_time, account
):
    malformed = {
        "profile": {"name": None},
        "auth": {"local": {"username": ["not", "text"]}},
        "stats": {
            "lvl": float("nan"),
            "class": "warrior",
            "hp": -1,
            "maxHealth": float("inf"),
            "exp": True,
            "toNextLevel": "4400",
            "mp": 30,
            "maxMP": 30,
            "gp": 10**1000,
        },
        "preferences": {"disableClasses": False},
        "flags": {"classSelected": False},
    }
    monkeypatch.setattr(backend, "load_linked_habitica_account", lambda _user_id: account)
    monkeypatch.setattr(backend, "get_habitica_status", lambda _account: malformed)

    response = app.test_client().get("/miniapp/api/me", headers=auth_header())

    assert response.status_code == 200
    assert response.json["profile"] == {
        "displayName": "Habitican",
        "username": None,
        "level": 0,
        "class": None,
        "classLabel": "Adventurer",
        "hasClass": False,
    }
    assert response.json["stats"] == {
        "hp": None,
        "maxHp": None,
        "exp": None,
        "maxExp": None,
        "mp": None,
        "maxMp": None,
        "gold": None,
    }


@pytest.mark.parametrize("failure", [None, RuntimeError("internal-private-detail")])
def test_habitica_failure_is_generic_bad_gateway(
    app, monkeypatch, frozen_time, account, failure
):
    monkeypatch.setattr(backend, "load_linked_habitica_account", lambda _user_id: account)

    def failed_status(_account):
        if failure:
            raise failure
        return None

    monkeypatch.setattr(backend, "get_habitica_status", failed_status)

    response = app.test_client().get("/miniapp/api/me", headers=auth_header())

    assert response.status_code == 502
    assert response.json["error"]["code"] == "habitica_unavailable"
    assert "internal-private-detail" not in response.get_data(as_text=True)


def test_persistence_failure_is_generic_service_unavailable(app, monkeypatch, frozen_time):
    def failed_lookup(_user_id):
        raise backend.MiniAppPersistenceError("private-pickle-path")

    monkeypatch.setattr(backend, "load_linked_habitica_account", failed_lookup)

    response = app.test_client().get("/miniapp/api/me", headers=auth_header())

    assert response.status_code == 503
    assert response.json["error"]["code"] == "service_unavailable"
    assert "private-pickle-path" not in response.get_data(as_text=True)


def test_avatar_requires_signed_user_and_honors_only_refresh_flag(
    app, monkeypatch, frozen_time, account, tmp_path
):
    avatar = tmp_path / "private-cache-name.png"
    avatar.write_bytes(PNG_BYTES)
    seen_user_ids = []
    refresh_values = []

    def lookup(user_id):
        seen_user_ids.append(user_id)
        return account

    monkeypatch.setattr(backend, "load_linked_habitica_account", lookup)
    monkeypatch.setattr(
        backend,
        "get_habitica_status",
        lambda _account: {"preferences": {}, "items": {}, "stats": {}},
    )

    def render(_account, *, force_refresh):
        refresh_values.append(force_refresh)
        return avatar

    monkeypatch.setattr(backend, "render_habitica_avatar", render)
    client = app.test_client()

    regular = client.get(
        "/miniapp/api/avatar?telegram_user_id=999&filename=other-user.png",
        headers=auth_header(424242),
    )
    refreshed = client.get("/miniapp/api/avatar?refresh=1", headers=auth_header(424242))

    assert regular.status_code == refreshed.status_code == 200
    assert regular.mimetype == refreshed.mimetype == "image/png"
    assert regular.data == refreshed.data == PNG_BYTES
    assert seen_user_ids == [424242, 424242]
    assert refresh_values == [False, True]
    assert regular.headers["Cache-Control"] == "no-store"
    assert "private-cache-name" not in str(regular.headers)
    assert b"private-cache-name" not in regular.data


def test_avatar_renderer_failure_is_generic(app, monkeypatch, frozen_time, account):
    monkeypatch.setattr(backend, "load_linked_habitica_account", lambda _user_id: account)
    monkeypatch.setattr(
        backend,
        "render_habitica_avatar",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("private-render-path")),
    )
    monkeypatch.setattr(
        backend,
        "get_habitica_status",
        lambda _account: {"preferences": {}, "items": {}, "stats": {}},
    )

    response = app.test_client().get("/miniapp/api/avatar", headers=auth_header())

    assert response.status_code == 502
    assert response.json["error"]["code"] == "avatar_unavailable"
    assert "private-render-path" not in response.get_data(as_text=True)


def test_persistence_lookup_is_read_only_and_refreshes_between_calls(tmp_path, monkeypatch):
    persistence_path = tmp_path / "botdata.pkl"
    monkeypatch.setenv("BOT_DATA_PATH", str(persistence_path))
    monkeypatch.setenv("RUNTIME_LOCK_TIMEOUT_SECONDS", "0")

    def write_state(user_id: str, api_key: str):
        persistence_path.write_bytes(
            pickle.dumps(
                {
                    "conversations": {},
                    "user_data": {
                        424242: {
                            "USER_ID": user_id,
                            "API_KEY": api_key,
                            "PRIVATE_EXTRA": {"nested": [1, 2, 3]},
                        }
                    },
                    "chat_data": {},
                    "bot_data": {},
                    "callback_data": None,
                }
            )
        )

    write_state("first-user", "first-key")
    original_bytes = persistence_path.read_bytes()
    first = backend.load_linked_habitica_account(424242)
    assert first is not None
    assert persistence_path.read_bytes() == original_bytes

    write_state("second-user", "second-key")
    second = backend.load_linked_habitica_account(424242)
    assert second is not None
    assert second.habitica_user_id == "second-user"
    assert second.habitica_api_key == "second-key"


def test_external_habitica_call_occurs_after_persistence_lock_is_released(
    app, monkeypatch, frozen_time, account
):
    lock_state = {"held": False}

    @contextmanager
    def fake_lock():
        assert lock_state["held"] is False
        lock_state["held"] = True
        try:
            yield
        finally:
            lock_state["held"] = False

    async def fake_read():
        assert lock_state["held"] is True
        return {
            424242: {
                "USER_ID": account.habitica_user_id,
                "API_KEY": account.habitica_api_key,
            }
        }

    def status(_account):
        assert lock_state["held"] is False
        return habitica_user()

    monkeypatch.setattr(backend, "acquire_runtime_lock", fake_lock)
    monkeypatch.setattr(backend, "_read_all_user_data", fake_read)
    monkeypatch.setattr(backend, "get_habitica_status", status)

    response = app.test_client().get("/miniapp/api/me", headers=auth_header())

    assert response.status_code == 200
    assert lock_state["held"] is False


def test_avatar_validates_current_credentials_before_serving_shared_cache(
    monkeypatch, account, tmp_path
):
    import avatar_renderer

    monkeypatch.setattr(backend, "__file__", str(tmp_path / "miniapp_backend.py"))
    avatar_root = tmp_path / "Avatar"
    avatar_root.mkdir()
    cached_avatar = avatar_renderer.get_avatar_cache_path(
        avatar_root,
        account.habitica_user_id,
    )
    cached_avatar.write_bytes(PNG_BYTES)
    status_calls = 0

    def invalid_credentials(_account):
        nonlocal status_calls
        status_calls += 1
        return None

    monkeypatch.setattr(backend, "get_habitica_status", invalid_credentials)

    @contextmanager
    def fake_render_lock(**_kwargs):
        yield

    monkeypatch.setattr(backend, "acquire_runtime_lock", fake_render_lock)
    monkeypatch.setattr(backend, "_avatar_render_lock_path", lambda _account: Path("unused"))

    result = backend.render_habitica_avatar(account, force_refresh=False)

    assert result is None
    assert status_calls == 1
    assert cached_avatar.exists()


def test_avatar_returns_valid_cache_only_after_current_credentials_validate(
    monkeypatch, account, tmp_path
):
    import avatar_renderer

    monkeypatch.setattr(backend, "__file__", str(tmp_path / "miniapp_backend.py"))
    avatar_root = tmp_path / "Avatar"
    avatar_root.mkdir()
    cached_avatar = avatar_renderer.get_avatar_cache_path(
        avatar_root,
        account.habitica_user_id,
    )
    cached_avatar.write_bytes(PNG_BYTES)

    @contextmanager
    def fake_render_lock(**_kwargs):
        yield

    monkeypatch.setattr(backend, "acquire_runtime_lock", fake_render_lock)
    monkeypatch.setattr(backend, "_avatar_render_lock_path", lambda _account: Path("unused"))
    monkeypatch.setattr(
        backend,
        "get_habitica_status",
        lambda _account: {"preferences": {}, "items": {}, "stats": {}},
    )

    assert backend.render_habitica_avatar(account, force_refresh=False) == cached_avatar


def test_avatar_render_lock_contention_is_transient_service_unavailable(
    app, monkeypatch, frozen_time, account
):
    monkeypatch.setattr(backend, "load_linked_habitica_account", lambda _user_id: account)
    monkeypatch.setattr(
        backend,
        "render_habitica_avatar",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(backend.RuntimeLockUnavailable()),
    )

    response = app.test_client().get("/miniapp/api/avatar", headers=auth_header())

    assert response.status_code == 503
    assert response.json["error"]["code"] == "service_unavailable"


def test_corrupt_pickle_is_not_rewritten_or_disclosed(app, monkeypatch, frozen_time, tmp_path):
    persistence_path = tmp_path / "botdata.pkl"
    corrupt = b"not-a-trusted-valid-pickle"
    persistence_path.write_bytes(corrupt)
    monkeypatch.setenv("BOT_DATA_PATH", str(persistence_path))
    monkeypatch.setenv("RUNTIME_LOCK_TIMEOUT_SECONDS", "0")

    response = app.test_client().get("/miniapp/api/me", headers=auth_header())

    assert response.status_code == 503
    assert response.json["error"]["code"] == "service_unavailable"
    assert persistence_path.read_bytes() == corrupt
    assert str(persistence_path) not in response.get_data(as_text=True)


def test_existing_wsgi_app_keeps_all_five_routes():
    import webhook_app

    rules = {rule.rule for rule in webhook_app.flask_app.url_map.iter_rules()}

    assert {
        "/telegram-webhook",
        "/tick",
        "/miniapp/",
        "/miniapp/api/me",
        "/miniapp/api/avatar",
    } <= rules
