from __future__ import annotations

import importlib
import json
import stat
import sys
from copy import deepcopy
from types import ModuleType, SimpleNamespace

import pytest


class FakeState:
    def __init__(self) -> None:
        self.persisted_bot_data: dict = {}
        self.build_calls: list[dict] = []
        self.apps: list = []
        self.process_attempts: list = []
        self.persistence_calls = 0
        self.tick_calls = 0
        self.enter_error: Exception | None = None
        self.exit_error: Exception | None = None
        self.process_error: Exception | None = None
        self.persistence_error: Exception | None = None
        self.decode_error: Exception | None = None
        self.decoded_update_id: int | None = None
        self.tick_error: Exception | None = None
        self.tick_result = {
            "sent": 0,
            "users_checked": 0,
            "errors": 0,
            "window_seconds": 60,
        }


@pytest.fixture
def webhook_module(monkeypatch, tmp_path):
    state = FakeState()

    class FakeApplication:
        def __init__(self) -> None:
            self.bot = object()
            self.bot_data: dict = {}
            state.apps.append(self)

        async def __aenter__(self):
            if state.enter_error is not None:
                raise state.enter_error
            self.bot_data = deepcopy(state.persisted_bot_data)
            return self

        async def __aexit__(self, _exc_type, _exc_value, _traceback):
            if state.exit_error is not None:
                raise state.exit_error
            return False

        async def process_update(self, update):
            state.process_attempts.append(update)
            if state.process_error is not None:
                raise state.process_error

        async def update_persistence(self):
            state.persistence_calls += 1
            if state.persistence_error is not None:
                raise state.persistence_error
            state.persisted_bot_data = deepcopy(self.bot_data)

    def build_application(**kwargs):
        state.build_calls.append(kwargs)
        return FakeApplication()

    async def run_reminder_tick(_application):
        state.tick_calls += 1
        if state.tick_error is not None:
            raise state.tick_error
        return deepcopy(state.tick_result)

    class FakeUpdate:
        @staticmethod
        def de_json(data, _bot):
            if state.decode_error is not None:
                raise state.decode_error
            update_id = (
                state.decoded_update_id
                if state.decoded_update_id is not None
                else data["update_id"]
            )
            return SimpleNamespace(update_id=update_id, payload=deepcopy(data))

    fake_habitica_bot = ModuleType("habitica_bot")
    fake_habitica_bot.build_application = build_application
    fake_habitica_bot.run_reminder_tick = run_reminder_tick

    fake_telegram = ModuleType("telegram")
    fake_telegram.Update = FakeUpdate

    monkeypatch.setitem(sys.modules, "habitica_bot", fake_habitica_bot)
    monkeypatch.setitem(sys.modules, "telegram", fake_telegram)
    monkeypatch.setenv("BOT_DATA_PATH", str(tmp_path / "botdata.pkl"))
    monkeypatch.setenv("RUNTIME_LOCK_TIMEOUT_SECONDS", "0")
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("TICK_TOKEN", raising=False)
    monkeypatch.delenv("ALLOW_LEGACY_TICK_QUERY_TOKEN", raising=False)

    sys.modules.pop("webhook_app", None)
    module = importlib.import_module("webhook_app")
    module.flask_app.config.update(TESTING=True)

    yield module, state

    sys.modules.pop("webhook_app", None)


def _post_update(client, update_id: int, **kwargs):
    return client.post(
        "/telegram-webhook",
        json={"update_id": update_id, "message": {"text": "/status"}},
        **kwargs,
    )


def test_webhook_accepts_json_with_charset_and_persists_update_id(webhook_module):
    module, state = webhook_module
    client = module.flask_app.test_client()

    response = client.post(
        "/telegram-webhook",
        data=json.dumps({"update_id": 101}),
        content_type="application/json; charset=utf-8",
    )

    assert response.status_code == 200
    assert response.get_data(as_text=True) == "OK"
    assert len(state.process_attempts) == 1
    assert state.persistence_calls == 1
    assert state.persisted_bot_data[module.RECENT_UPDATE_IDS_KEY] == [101]
    assert state.build_calls == [{"register_commands": False, "persistence_on_flush": True}]


def test_webhook_secret_is_optional_but_enforced_when_configured(webhook_module, monkeypatch):
    module, state = webhook_module
    client = module.flask_app.test_client()
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "expected-secret")

    assert _post_update(client, 1).status_code == 403
    assert (
        _post_update(
            client,
            1,
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"},
        ).status_code
        == 403
    )
    assert state.build_calls == []

    response = _post_update(
        client,
        1,
        headers={"X-Telegram-Bot-Api-Secret-Token": "expected-secret"},
    )
    assert response.status_code == 200
    assert len(state.process_attempts) == 1


@pytest.mark.parametrize(
    ("body", "content_type", "expected_status"),
    [
        ("{}", "text/plain", 415),
        ("", "application/json", 400),
        ("{", "application/json", 400),
        ("[{'update_id': 1}]", "application/json", 400),
        (json.dumps([{"update_id": 1}]), "application/json", 400),
        (json.dumps({}), "application/json", 400),
        (json.dumps({"update_id": "1"}), "application/json", 400),
        (json.dumps({"update_id": True}), "application/json", 400),
    ],
)
def test_webhook_rejects_invalid_media_and_payload_shapes(
    webhook_module, body, content_type, expected_status
):
    module, state = webhook_module
    client = module.flask_app.test_client()

    response = client.post(
        "/telegram-webhook",
        data=body,
        content_type=content_type,
    )

    assert response.status_code == expected_status
    assert state.build_calls == []


def test_webhook_rejects_oversized_json(webhook_module):
    module, state = webhook_module
    module.flask_app.config["MAX_CONTENT_LENGTH"] = 64
    client = module.flask_app.test_client()

    response = client.post(
        "/telegram-webhook",
        json={"update_id": 1, "message": {"text": "x" * 256}},
    )

    assert response.status_code == 413
    assert response.get_data(as_text=True) == "Payload Too Large"
    assert state.build_calls == []


def test_malformed_telegram_object_is_generic_bad_request(webhook_module):
    module, state = webhook_module
    state.decode_error = ValueError("private-message-sentinel")
    client = module.flask_app.test_client()

    response = _post_update(client, 12)

    assert response.status_code == 400
    assert response.get_data(as_text=True) == "Bad Request"
    assert "private-message-sentinel" not in response.get_data(as_text=True)
    assert state.process_attempts == []


def test_duplicate_update_is_acknowledged_without_redispatch(webhook_module):
    module, state = webhook_module
    client = module.flask_app.test_client()

    first = _post_update(client, 55)
    duplicate = _post_update(client, 55)

    assert first.status_code == duplicate.status_code == 200
    assert len(state.process_attempts) == 1
    assert state.persistence_calls == 1
    assert state.persisted_bot_data[module.RECENT_UPDATE_IDS_KEY] == [55]


def test_recent_update_dedupe_is_bounded(webhook_module, monkeypatch):
    module, state = webhook_module
    monkeypatch.setattr(module, "MAX_RECENT_UPDATE_IDS", 3)
    client = module.flask_app.test_client()

    for update_id in range(1, 6):
        assert _post_update(client, update_id).status_code == 200

    assert state.persisted_bot_data[module.RECENT_UPDATE_IDS_KEY] == [3, 4, 5]

    # An evicted ID is no longer treated as a duplicate.
    assert _post_update(client, 1).status_code == 200
    assert len(state.process_attempts) == 6
    assert state.persisted_bot_data[module.RECENT_UPDATE_IDS_KEY] == [4, 5, 1]


def test_dispatch_error_is_acknowledged_recorded_and_not_logged_verbatim(webhook_module, caplog):
    module, state = webhook_module
    state.process_error = RuntimeError("api-key-private-sentinel")
    client = module.flask_app.test_client()
    caplog.set_level("ERROR", logger=module.LOGGER.name)

    first = _post_update(client, 88)
    second = _post_update(client, 88)

    assert first.status_code == second.status_code == 200
    assert len(state.process_attempts) == 1
    assert state.persisted_bot_data[module.RECENT_UPDATE_IDS_KEY] == [88]
    assert "api-key-private-sentinel" not in caplog.text
    assert "api-key-private-sentinel" not in first.get_data(as_text=True)


def test_failure_before_dispatch_returns_generic_503(webhook_module, caplog):
    module, state = webhook_module
    state.enter_error = RuntimeError("bot-token-private-sentinel")
    client = module.flask_app.test_client()
    caplog.set_level("ERROR", logger=module.LOGGER.name)

    response = _post_update(client, 99)

    assert response.status_code == 503
    assert response.get_data(as_text=True) == "Service Unavailable"
    assert response.headers["Retry-After"] == "1"
    assert "bot-token-private-sentinel" not in caplog.text
    assert "bot-token-private-sentinel" not in response.get_data(as_text=True)
    assert state.process_attempts == []


def test_persistence_error_after_dispatch_is_acknowledged_without_secret_leak(
    webhook_module, caplog
):
    module, state = webhook_module
    state.persistence_error = RuntimeError("persistence-private-sentinel")
    client = module.flask_app.test_client()
    caplog.set_level("ERROR", logger=module.LOGGER.name)

    response = _post_update(client, 100)

    assert response.status_code == 200
    assert response.get_data(as_text=True) == "OK"
    assert len(state.process_attempts) == 1
    assert "persistence-private-sentinel" not in caplog.text
    assert "persistence-private-sentinel" not in response.get_data(as_text=True)


def test_webhook_lock_contention_returns_503_without_building_application(
    webhook_module,
):
    module, state = webhook_module
    client = module.flask_app.test_client()

    with module.acquire_runtime_lock():
        response = _post_update(client, 1)

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "1"
    assert state.build_calls == []


def test_tick_fails_closed_without_configured_token(webhook_module):
    module, state = webhook_module
    client = module.flask_app.test_client()

    response = client.get("/tick", headers={"X-Tick-Token": "anything"})

    assert response.status_code == 403
    assert response.headers["Cache-Control"] == "no-store"
    assert state.tick_calls == 0


def test_tick_accepts_headers_before_legacy_query(webhook_module, monkeypatch):
    module, state = webhook_module
    monkeypatch.setenv("TICK_TOKEN", "tick-secret")
    client = module.flask_app.test_client()

    assert client.get("/tick").status_code == 403
    assert client.get("/tick?token=wrong").status_code == 403
    assert client.get("/tick", headers={"X-Tick-Token": "tick-secret"}).status_code == 200
    assert client.get("/tick", headers={"Authorization": "Bearer tick-secret"}).status_code == 200

    # A supplied bad header cannot fall back to a valid query parameter.
    assert (
        client.get(
            "/tick?token=tick-secret",
            headers={"X-Tick-Token": "wrong"},
        ).status_code
        == 403
    )
    assert state.tick_calls == 2


def test_tick_legacy_query_compatibility_can_be_disabled(webhook_module, monkeypatch):
    module, state = webhook_module
    monkeypatch.setenv("TICK_TOKEN", "tick-secret")
    client = module.flask_app.test_client()

    assert client.get("/tick?token=tick-secret").status_code == 200

    monkeypatch.setenv("ALLOW_LEGACY_TICK_QUERY_TOKEN", "false")
    assert client.get("/tick?token=tick-secret").status_code == 403
    assert state.tick_calls == 1


def test_tick_returns_only_whitelisted_aggregate_fields(webhook_module, monkeypatch):
    module, state = webhook_module
    monkeypatch.setenv("TICK_TOKEN", "tick-secret")
    state.tick_result = {
        "sent": 2,
        "users_checked": 3,
        "errors": 1,
        "window_seconds": 60,
        "private_users": ["habitica-user-private-sentinel"],
    }
    client = module.flask_app.test_client()

    response = client.get("/tick", headers={"X-Tick-Token": "tick-secret"})

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.get_json() == {
        "ok": False,
        "sent": 2,
        "users_checked": 3,
        "errors": 1,
        "persistence_errors": 0,
        "persistence_ok": True,
        "window_seconds": 60,
    }
    assert "habitica-user-private-sentinel" not in response.get_data(as_text=True)


def test_tick_exception_returns_generic_no_store_503(webhook_module, monkeypatch, caplog):
    module, state = webhook_module
    monkeypatch.setenv("TICK_TOKEN", "tick-secret")
    state.tick_error = RuntimeError("habitica-api-key-private-sentinel")
    client = module.flask_app.test_client()
    caplog.set_level("ERROR", logger=module.LOGGER.name)

    response = client.get("/tick", headers={"X-Tick-Token": "tick-secret"})

    assert response.status_code == 503
    assert response.headers["Cache-Control"] == "no-store"
    assert response.get_json() == {"ok": False}
    assert "habitica-api-key-private-sentinel" not in caplog.text
    assert "habitica-api-key-private-sentinel" not in response.get_data(as_text=True)


def test_tick_lock_contention_returns_503_without_running_tick(webhook_module, monkeypatch):
    module, state = webhook_module
    monkeypatch.setenv("TICK_TOKEN", "tick-secret")
    client = module.flask_app.test_client()

    with module.acquire_runtime_lock():
        response = client.get("/tick", headers={"X-Tick-Token": "tick-secret"})

    assert response.status_code == 503
    assert response.headers["Cache-Control"] == "no-store"
    assert response.get_json() == {"ok": False}
    assert state.tick_calls == 0


def test_runtime_lock_path_preserves_legacy_working_directory_semantics(monkeypatch, tmp_path):
    import runtime_lock

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BOT_DATA_PATH", raising=False)
    assert runtime_lock.get_bot_data_path() == (tmp_path / "botdata.pkl").resolve()

    monkeypatch.setenv("BOT_DATA_PATH", "state/custom.pkl")
    assert runtime_lock.get_bot_data_path() == (tmp_path / "state" / "custom.pkl").resolve()


def test_runtime_lock_timeout_is_bounded(monkeypatch):
    import runtime_lock

    monkeypatch.setenv("RUNTIME_LOCK_TIMEOUT_SECONDS", "999999")
    assert runtime_lock._configured_timeout() == runtime_lock.MAX_LOCK_TIMEOUT_SECONDS


def test_runtime_lock_file_is_private(tmp_path):
    import runtime_lock

    lock_path = tmp_path / "runtime.lock"
    with runtime_lock.acquire_runtime_lock(lock_path=lock_path, timeout=0):
        assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600
