from __future__ import annotations

import hashlib
import hmac
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from flask import Flask

import miniapp_backend as backend
from Habitica_API import HabiticaAPIError, HabiticaErrorKind, HabiticaResult
from miniapp_tasks import normalize_task


TASK_ID = "123e4567-e89b-42d3-a456-426614174000"
ITEM_ID = "223e4567-e89b-42d3-a456-426614174001"
ACCOUNT = backend.LinkedHabiticaAccount("habitica-user", "habitica-key")
FAKE_BOT_TOKEN = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"
NOW = 2_000_000_000


def auth_header(user_id: int = 424242) -> dict[str, str]:
    fields = {
        "auth_date": str(NOW),
        "query_id": "AAH_fake-query",
        "user": json.dumps({"id": user_id}, separators=(",", ":")),
    }
    check = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hmac.new(b"WebAppData", FAKE_BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return {"Authorization": f"tma {urlencode(fields)}"}


@pytest.fixture
def frozen_time(monkeypatch):
    monkeypatch.setattr("miniapp_auth.time.time", lambda: NOW)


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", FAKE_BOT_TOKEN)
    monkeypatch.setenv("MINIAPP_AUTH_MAX_AGE_SECONDS", "3600")
    monkeypatch.delenv("MINIAPP_DEV_MODE", raising=False)
    monkeypatch.delenv("MINIAPP_DEV_TELEGRAM_USER_ID", raising=False)
    application = Flask(
        "miniapp-task-tests",
        template_folder=str(Path(backend.__file__).resolve().parent / "templates"),
        static_folder=str(Path(backend.__file__).resolve().parent / "static"),
    )
    application.config.update(TESTING=True, MAX_CONTENT_LENGTH=backend.MAX_TASK_REQUEST_BYTES)
    application.register_blueprint(backend.miniapp_blueprint)
    monkeypatch.setattr(backend, "load_linked_habitica_account", lambda _user_id: ACCOUNT)
    monkeypatch.setattr(backend, "_ensure_gameplay_ready", lambda _account: None)
    with backend._POTION_PURCHASE_INTENTS_LOCK:
        backend._POTION_PURCHASE_INTENTS.clear()

    @contextmanager
    def unlocked(*_args, **_kwargs):
        yield

    monkeypatch.setattr(backend, "acquire_runtime_lock", unlocked)
    return application


def raw_task(task_type: str, **changes):
    task = {
        "id": TASK_ID,
        "type": task_type,
        "text": "Task <script>alert(1)</script>",
        "notes": "private note displayed as text",
        "priority": 1,
        "completed": False,
        "isDue": task_type == "daily",
        "up": True,
        "down": True,
        "frequency": "weekly",
        "everyX": 1,
        "repeat": {"m": True},
        "checklist": [],
        "owner": "must-not-leak",
        "history": [{"value": 9}],
    }
    task.update(changes)
    return task


def revision_for(raw):
    normalized = normalize_task(raw)
    assert normalized is not None
    return normalized["revision"]


def ok(data, status=200):
    return HabiticaResult(data=data, status=status)


def failure(kind, *, status=None, retry_after=None, outcome_unknown=False):
    return HabiticaResult(
        status=status,
        error=HabiticaAPIError(
            kind=kind,
            status=status,
            retry_after=retry_after,
            outcome_unknown=outcome_unknown,
        ),
    )


class PayloadValue:
    def __init__(self, payload):
        self.payload = payload

    def to_payload(self):
        return self.payload


def gameplay_ok(payload, **attributes):
    value = PayloadValue(payload)
    for name, content in attributes.items():
        setattr(value, name, content)
    return SimpleNamespace(ok=True, data=value, error=None)


def gameplay_failure(code, *, payload=None, **attributes):
    error = SimpleNamespace(
        code=code,
        retry_after=attributes.pop("retry_after", None),
        outcome_unknown=attributes.pop("outcome_unknown", False),
        unresolved_daily_ids=attributes.pop("unresolved_daily_ids", ()),
    )
    value = PayloadValue(payload) if payload is not None else None
    return SimpleNamespace(ok=False, data=value, error=error)


def issued_potion_body(account=ACCOUNT):
    token = backend._issue_potion_purchase_intent(account)
    assert token is not None
    return {"purchaseIntent": token}


@pytest.mark.parametrize(
    ("query", "upstream"),
    [
        ("type=habit", "habits"),
        ("type=daily", "dailys"),
        ("type=todo&completed=false", "todos"),
        ("type=todo&completed=true", "completedTodos"),
    ],
)
def test_authenticated_lists_use_explicit_history_false(
    app, monkeypatch, frozen_time, query, upstream
):
    import Habitica_API

    calls = []

    def list_tasks(user, key, task_type, *, history):
        calls.append((user, key, task_type, history))
        singular = {
            "habits": "habit",
            "dailys": "daily",
            "todos": "todo",
            "completedTodos": "todo",
        }[task_type]
        return ok([raw_task(singular)])

    monkeypatch.setattr(Habitica_API, "get_tasks_result", list_tasks)
    response = app.test_client().get(f"/miniapp/api/tasks?{query}", headers=auth_header())

    assert response.status_code == 200
    assert response.json["tasks"][0]["type"] in {"habit", "daily", "todo"}
    assert "owner" not in response.get_data(as_text=True)
    assert "history" not in response.get_data(as_text=True)
    assert calls == [("habitica-user", "habitica-key", upstream, False)]
    assert response.headers["Cache-Control"] == "no-store"


def test_task_summary_uses_real_private_counts_without_leaking_tasks(
    app, monkeypatch, frozen_time
):
    import Habitica_API

    calls = []
    fixtures = {
        "habits": [raw_task("habit", counterUp=3, counterDown=0)],
        "dailys": [raw_task("daily", completed=True, isDue=True)],
        "todos": [raw_task("todo", completed=False)],
        "completedTodos": [
            raw_task("todo", completed=True, dateCompleted="2026-08-22")
        ],
    }

    def list_tasks(user, key, task_type, *, history):
        calls.append((user, key, task_type, history))
        return ok(fixtures[task_type])

    monkeypatch.setattr(Habitica_API, "get_tasks_result", list_tasks)
    response = app.test_client().get(
        "/miniapp/api/task-summary?today=2026-08-22", headers=auth_header()
    )

    assert response.status_code == 200
    assert response.json == {
        "ok": True,
        "summary": {
            "habits": {"total": 1, "completed": 1},
            "dailies": {"total": 1, "completed": 1},
            "todos": {"total": 2, "completed": 1},
        },
    }
    assert calls == [
        ("habitica-user", "habitica-key", "habits", False),
        ("habitica-user", "habitica-key", "dailys", False),
        ("habitica-user", "habitica-key", "todos", False),
        ("habitica-user", "habitica-key", "completedTodos", False),
    ]
    body = response.get_data(as_text=True)
    assert "private note" not in body
    assert "must-not-leak" not in body
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("today", ["", "2026-8-22", "not-a-date", "2026-02-30"])
def test_task_summary_rejects_invalid_local_dates_without_upstream(
    app, monkeypatch, frozen_time, today
):
    import Habitica_API

    monkeypatch.setattr(
        Habitica_API,
        "get_tasks_result",
        lambda *_args, **_kwargs: pytest.fail("upstream must not be called"),
    )
    response = app.test_client().get(
        f"/miniapp/api/task-summary?today={today}", headers=auth_header()
    )
    assert response.status_code == 400
    assert response.json["error"]["code"] == "invalid_request"


@pytest.mark.parametrize(
    "query",
    ["", "type=reward", "type=daily&completed=true", "type=todo&completed=yes"],
)
def test_invalid_list_filters_are_rejected_without_upstream(app, monkeypatch, frozen_time, query):
    import Habitica_API

    monkeypatch.setattr(
        Habitica_API,
        "get_tasks_result",
        lambda *_args, **_kwargs: pytest.fail("upstream must not be called"),
    )
    response = app.test_client().get(f"/miniapp/api/tasks?{query}", headers=auth_header())
    assert response.status_code == 400
    assert response.json["error"]["code"] == "invalid_request"


def test_authentication_precedes_task_upstream(app, monkeypatch):
    import Habitica_API

    monkeypatch.setattr(
        Habitica_API,
        "get_tasks_result",
        lambda *_args, **_kwargs: pytest.fail("must authenticate first"),
    )
    response = app.test_client().get("/miniapp/api/tasks?type=habit")
    assert response.status_code == 401


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        ("get", "/miniapp/api/day-status", {}),
        ("post", "/miniapp/api/day-refresh", {"json": {"completedDailyIds": []}}),
        ("get", "/miniapp/api/health-potion", {}),
        ("post", "/miniapp/api/health-potion", {"json": {}}),
    ],
)
def test_gameplay_routes_require_telegram_authentication(app, method, path, kwargs):
    response = getattr(app.test_client(), method)(path, **kwargs)
    assert response.status_code == 401
    assert response.json["error"]["code"] == "invalid_telegram_session"
    assert response.headers["Cache-Control"] == "no-store"


def test_day_status_returns_only_normalized_service_payload(
    app, monkeypatch, frozen_time
):
    import habitica_gameplay

    monkeypatch.setattr(
        habitica_gameplay,
        "fetch_day_status",
        lambda *_args: gameplay_ok(
            {
                "refreshRequired": True,
                "daysMissed": 1,
                "reviewLabel": "Yesterday",
                "dailies": [
                    {
                        "id": TASK_ID,
                        "text": "Exercise",
                        "notes": "Useful context",
                        "priority": 1,
                        "checklist": [],
                        "completed": False,
                    }
                ],
            }
        ),
    )

    response = app.test_client().get(
        "/miniapp/api/day-status", headers=auth_header()
    )

    assert response.status_code == 200
    assert response.json["day"]["refreshRequired"] is True
    assert "maxSelections" not in response.json["day"]
    assert response.json["day"]["dailies"][0]["id"] == TASK_ID
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize(
    ("code", "status", "public_code", "retry_after"),
    [
        ("habitica_unauthorized", 401, "habitica_unauthorized", None),
        ("habitica_unavailable", 502, "habitica_unavailable", None),
        ("invalid_response", 502, "habitica_unavailable", None),
        ("request_timeout", 504, "habitica_unavailable", None),
        ("rate_limited", 429, "rate_limited", 3.1),
    ],
)
def test_day_status_errors_are_private_and_stable(
    app, monkeypatch, frozen_time, code, status, public_code, retry_after
):
    import habitica_gameplay

    monkeypatch.setattr(
        habitica_gameplay,
        "fetch_day_status",
        lambda *_args: gameplay_failure(code, retry_after=retry_after),
    )

    response = app.test_client().get(
        "/miniapp/api/day-status", headers=auth_header()
    )

    assert response.status_code == status
    assert response.json["error"]["code"] == public_code
    assert "habitica-user" not in response.get_data(as_text=True)
    assert "habitica-key" not in response.get_data(as_text=True)
    if retry_after is not None:
        assert response.headers["Retry-After"] == "4"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"completedDailyIds": "not-a-list"},
        {"completedDailyIds": ["not-a-uuid"]},
        {"completedDailyIds": [TASK_ID, TASK_ID]},
        {"completedDailyIds": [], "telegramUserId": 424242},
    ],
)
def test_day_refresh_rejects_malformed_or_duplicate_selection(
    app, monkeypatch, frozen_time, payload
):
    import habitica_gameplay

    monkeypatch.setattr(
        habitica_gameplay,
        "refresh_day",
        lambda *_args: pytest.fail("invalid selection reached gameplay service"),
    )
    response = app.test_client().post(
        "/miniapp/api/day-refresh",
        headers=auth_header(),
        json=payload,
    )
    assert response.status_code == 400
    assert response.json["error"]["code"] == "invalid_daily_selection"


def test_day_refresh_route_and_service_share_the_same_batch_size():
    import habitica_gameplay

    assert (
        backend.MAX_DAY_REFRESH_BATCH_SIZE
        == habitica_gameplay.MAX_DAILY_BATCH_SIZE
        == 8
    )


def test_day_refresh_accepts_more_than_one_batch_for_safe_continuation(
    app, monkeypatch, frozen_time
):
    import habitica_gameplay

    selected = [
        f"00000000-0000-4000-8000-{number:012d}"
        for number in range(11)
    ]
    received = []
    monkeypatch.setattr(
        habitica_gameplay,
        "refresh_day",
        lambda _user, _key, task_ids: received.append(task_ids)
        or gameplay_failure(
            "batch_incomplete",
            payload={
                "status": "batch_incomplete",
                "day": {"refreshRequired": True},
                "profile": None,
                "scoredDailyIds": task_ids[:8],
                "unresolvedDailyIds": task_ids[8:],
            },
            retry_after=60,
            unresolved_daily_ids=tuple(task_ids[8:]),
        ),
    )

    response = app.test_client().post(
        "/miniapp/api/day-refresh",
        headers=auth_header(),
        json={"completedDailyIds": selected},
    )

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "60"
    assert response.json["error"]["code"] == "batch_incomplete"
    assert response.json["status"] == "batch_incomplete"
    assert response.json["scoredDailyIds"] == selected[:8]
    assert response.json["unresolvedDailyIds"] == selected[8:]
    assert response.json["day"] == {"refreshRequired": True}
    assert received == [tuple(selected)]


def test_day_refresh_uses_zero_wait_gameplay_lock_and_returns_invalidation(
    app, monkeypatch, frozen_time
):
    import habitica_gameplay

    lock_calls = []

    @contextmanager
    def tracked_lock(*, lock_path, timeout):
        lock_calls.append((lock_path, timeout))
        yield

    received = []
    monkeypatch.setattr(backend, "acquire_runtime_lock", tracked_lock)
    monkeypatch.setattr(
        habitica_gameplay,
        "refresh_day",
        lambda user, key, selected: received.append((user, key, selected))
        or gameplay_ok(
            {
                "status": "refreshed",
                "day": {"refreshRequired": False},
                "profile": {
                    "profile": {"level": 20, "class": "warrior"},
                    "stats": {
                        "hp": 45,
                        "maxHp": 50,
                        "exp": 100,
                        "maxExp": 200,
                        "mp": 30,
                        "maxMp": 40,
                        "gold": 75,
                    },
                },
                "scoredDailyIds": [TASK_ID],
                "unresolvedDailyIds": [],
            }
        ),
    )

    response = app.test_client().post(
        "/miniapp/api/day-refresh",
        headers=auth_header(),
        json={"completedDailyIds": [TASK_ID]},
    )

    assert response.status_code == 200
    assert response.json["profile"]["stats"]["hp"] == 45
    assert response.json["day"] == {"refreshRequired": False}
    assert response.json["invalidate"] == {
        "profile": True,
        "habits": True,
        "dailies": True,
        "todos": False,
        "avatar": False,
    }
    assert received == [("habitica-user", "habitica-key", (TASK_ID,))]
    assert lock_calls[0][1] == 0
    assert lock_calls[0][0].parent.name == ".miniapp-gameplay-locks"


def test_partial_day_refresh_exposes_applied_and_unresolved_ids(
    app, monkeypatch, frozen_time
):
    import habitica_gameplay

    monkeypatch.setattr(
        habitica_gameplay,
        "refresh_day",
        lambda *_args: gameplay_failure(
            "daily_score_failed",
            payload={
                "status": "partial_failure",
                "day": {"refreshRequired": True},
                "profile": None,
                "scoredDailyIds": [TASK_ID],
                "unresolvedDailyIds": [ITEM_ID],
            },
            unresolved_daily_ids=(ITEM_ID,),
        ),
    )
    response = app.test_client().post(
        "/miniapp/api/day-refresh",
        headers=auth_header(),
        json={"completedDailyIds": [TASK_ID]},
    )
    assert response.status_code == 502
    assert response.json["error"]["code"] == "daily_score_failed"
    assert response.json["status"] == "partial_failure"
    assert response.json["scoredDailyIds"] == [TASK_ID]
    assert response.json["unresolvedDailyIds"] == [ITEM_ID]
    assert response.json["day"] == {"refreshRequired": True}


def test_confirmed_cron_with_failed_profile_read_resolves_gate_and_requires_reload(
    app, monkeypatch, frozen_time
):
    import habitica_gameplay

    monkeypatch.setattr(
        habitica_gameplay,
        "refresh_day",
        lambda *_args: gameplay_ok(
            {
                "status": "refreshed",
                "day": {"refreshRequired": False},
                "profile": None,
                "scoredDailyIds": [TASK_ID],
                "unresolvedDailyIds": [],
            },
        ),
    )
    response = app.test_client().post(
        "/miniapp/api/day-refresh",
        headers=auth_header(),
        json={"completedDailyIds": [TASK_ID]},
    )
    assert response.status_code == 200
    assert response.json["ok"] is True
    assert response.json["status"] == "refreshed"
    assert response.json["day"] == {"refreshRequired": False}
    assert response.json["invalidate"]["habits"] is True
    assert response.json["invalidate"]["profile"] is True


def test_day_refresh_duplicate_submit_is_rejected_before_service(
    app, monkeypatch, frozen_time
):
    import habitica_gameplay

    def busy_lock(**_kwargs):
        raise backend.RuntimeLockUnavailable

    monkeypatch.setattr(backend, "acquire_runtime_lock", busy_lock)
    monkeypatch.setattr(
        habitica_gameplay,
        "refresh_day",
        lambda *_args: pytest.fail("busy request must not reach gameplay service"),
    )
    response = app.test_client().post(
        "/miniapp/api/day-refresh",
        headers=auth_header(),
        json={"completedDailyIds": []},
    )
    assert response.status_code == 409
    assert response.json["error"]["code"] == "duplicate_request"


def test_health_potion_status_returns_only_confirmation_fields(
    app, monkeypatch, frozen_time
):
    import habitica_gameplay

    calls = []

    def potion_status(user, key, *, fetch_content):
        calls.append((user, key, fetch_content))
        return gameplay_ok(
            {
                "potion": {
                    "name": "Health Potion",
                    "price": 25,
                    "healing": 15,
                },
                "stats": {"hp": 20, "maxHp": 50, "gold": 80},
                "healthFull": False,
                "canAfford": True,
            }
        )

    monkeypatch.setattr(habitica_gameplay, "get_health_potion_status", potion_status)

    response = app.test_client().get(
        "/miniapp/api/health-potion", headers=auth_header()
    )

    assert response.status_code == 200
    payload = response.get_json()
    purchase_intent = payload.pop("purchaseIntent")
    assert isinstance(purchase_intent, str)
    assert backend._potion_token_digest(purchase_intent) is not None
    assert payload == {
        "ok": True,
        "potion": {"name": "Health Potion", "price": 25, "healing": 15},
        "stats": {"hp": 20, "maxHp": 50, "gold": 80},
        "healthFull": False,
        "canAfford": True,
    }
    assert calls == [
        ("habitica-user", "habitica-key", backend._cached_potion_content_result)
    ]
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Vary"] == "Authorization"


def test_health_potion_purchase_is_locked_and_returns_profile_without_avatar_work(
    app, monkeypatch, frozen_time
):
    import habitica_gameplay

    lock_calls = []

    @contextmanager
    def tracked_lock(*, lock_path, timeout):
        lock_calls.append((lock_path, timeout))
        yield

    service_calls = []

    def purchase(user, key, *, fetch_content):
        service_calls.append((user, key, fetch_content))
        return gameplay_ok(
            {
                "profile": {
                    "profile": {"level": 20, "class": "warrior"},
                    "stats": {
                        "hp": 35,
                        "maxHp": 50,
                        "exp": 100,
                        "maxExp": 200,
                        "mp": 30,
                        "maxMp": 40,
                        "gold": 55,
                    },
                },
                "potion": {
                    "name": "Health Potion",
                    "price": 25,
                    "healing": 15,
                },
            }
        )

    monkeypatch.setattr(backend, "acquire_runtime_lock", tracked_lock)
    monkeypatch.setattr(habitica_gameplay, "purchase_health_potion", purchase)
    monkeypatch.setattr(
        backend,
        "render_habitica_avatar",
        lambda *_args, **_kwargs: pytest.fail("potion use must not regenerate the avatar"),
    )

    body = issued_potion_body()
    response = app.test_client().post(
        "/miniapp/api/health-potion", headers=auth_header(), json=body
    )
    replay = app.test_client().post(
        "/miniapp/api/health-potion", headers=auth_header(), json=body
    )

    assert response.status_code == 200
    assert replay.status_code == response.status_code
    assert replay.get_json() == response.get_json()
    assert response.json["profile"]["stats"]["hp"] == 35
    assert response.json["profile"]["stats"]["gold"] == 55
    assert response.json["invalidate"] == {"profile": True, "avatar": False}
    assert service_calls == [
        ("habitica-user", "habitica-key", backend._cached_potion_content_result)
    ]
    assert lock_calls[0][1] == 0
    assert lock_calls[0][0].parent.name == ".miniapp-gameplay-locks"


@pytest.mark.parametrize(
    ("code", "status", "public_code"),
    [
        ("health_already_full", 409, "health_already_full"),
        ("not_enough_gold", 409, "not_enough_gold"),
        ("habitica_unauthorized", 401, "habitica_unauthorized"),
        ("habitica_unavailable", 502, "habitica_unavailable"),
        ("invalid_response", 502, "habitica_unavailable"),
        ("purchase_failed", 502, "purchase_failed"),
        ("request_timeout", 504, "habitica_unavailable"),
    ],
)
def test_health_potion_errors_are_distinct_and_sanitized(
    app, monkeypatch, frozen_time, code, status, public_code
):
    import habitica_gameplay

    monkeypatch.setattr(
        habitica_gameplay,
        "purchase_health_potion",
        lambda *_args, **_kwargs: gameplay_failure(code),
    )

    response = app.test_client().post(
        "/miniapp/api/health-potion", headers=auth_header(), json=issued_potion_body()
    )

    assert response.status_code == status
    assert response.json["error"]["code"] == public_code
    assert "habitica-user" not in response.get_data(as_text=True)
    assert "habitica-key" not in response.get_data(as_text=True)


def test_health_potion_rate_limit_and_unknown_outcome_keep_retry_metadata(
    app, monkeypatch, frozen_time
):
    import habitica_gameplay

    calls = 0

    def purchase(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return gameplay_failure(
            "rate_limited", retry_after=4.2, outcome_unknown=True
        )

    monkeypatch.setattr(habitica_gameplay, "purchase_health_potion", purchase)

    body = issued_potion_body()
    response = app.test_client().post(
        "/miniapp/api/health-potion", headers=auth_header(), json=body
    )
    replay = app.test_client().post(
        "/miniapp/api/health-potion", headers=auth_header(), json=body
    )

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "5"
    assert response.json["reconcileRequired"] is True
    assert replay.status_code == response.status_code
    assert replay.get_json() == response.get_json()
    assert replay.headers["Retry-After"] == "5"
    assert calls == 1


def test_health_potion_duplicate_tap_never_reaches_purchase(
    app, monkeypatch, frozen_time
):
    import habitica_gameplay

    def busy_lock(**_kwargs):
        raise backend.RuntimeLockUnavailable

    monkeypatch.setattr(backend, "acquire_runtime_lock", busy_lock)
    calls = 0

    def purchase(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return gameplay_ok(
            {
                "profile": {
                    "profile": {"level": 20, "class": "warrior"},
                    "stats": {"hp": 35, "maxHp": 50, "gold": 55},
                },
                "potion": {"name": "Health Potion", "price": 25, "healing": 15},
            }
        )

    monkeypatch.setattr(habitica_gameplay, "purchase_health_potion", purchase)

    body = issued_potion_body()
    response = app.test_client().post(
        "/miniapp/api/health-potion", headers=auth_header(), json=body
    )

    assert response.status_code == 409
    assert response.json["error"]["code"] == "duplicate_request"
    assert calls == 0

    @contextmanager
    def unlocked(**_kwargs):
        yield

    monkeypatch.setattr(backend, "acquire_runtime_lock", unlocked)
    retry = app.test_client().post(
        "/miniapp/api/health-potion", headers=auth_header(), json=body
    )
    assert retry.status_code == 200
    assert calls == 1


def test_health_potion_in_progress_intent_rejects_overlap_before_service(
    app, monkeypatch, frozen_time
):
    import habitica_gameplay

    body = issued_potion_body()
    claim, _digest, _replay = backend._claim_potion_purchase_intent(
        ACCOUNT, body["purchaseIntent"]
    )
    assert claim == "claimed"
    monkeypatch.setattr(
        habitica_gameplay,
        "purchase_health_potion",
        lambda *_args, **_kwargs: pytest.fail("overlap must not reach the service"),
    )

    response = app.test_client().post(
        "/miniapp/api/health-potion", headers=auth_header(), json=body
    )

    assert response.status_code == 409
    assert response.json["error"]["code"] == "duplicate_request"


def test_health_potion_intent_is_account_bound_and_expires_before_service(
    app, monkeypatch, frozen_time
):
    import habitica_gameplay

    clock = [100.0]
    monkeypatch.setattr(backend.time, "monotonic", lambda: clock[0])
    foreign_body = issued_potion_body()
    other_account = backend.LinkedHabiticaAccount("habitica-user", "different-key")
    monkeypatch.setattr(
        backend, "load_linked_habitica_account", lambda _user_id: other_account
    )
    monkeypatch.setattr(
        habitica_gameplay,
        "purchase_health_potion",
        lambda *_args, **_kwargs: pytest.fail("foreign intent must not reach service"),
    )

    foreign = app.test_client().post(
        "/miniapp/api/health-potion", headers=auth_header(), json=foreign_body
    )
    assert foreign.status_code == 400
    assert foreign.json["error"]["code"] == "invalid_purchase_intent"

    monkeypatch.setattr(
        backend, "load_linked_habitica_account", lambda _user_id: ACCOUNT
    )
    expired_body = issued_potion_body()
    clock[0] += backend.POTION_PURCHASE_INTENT_TTL_SECONDS + 1
    expired = app.test_client().post(
        "/miniapp/api/health-potion", headers=auth_header(), json=expired_body
    )
    assert expired.status_code == 400
    assert expired.json["error"]["code"] == "invalid_purchase_intent"


def test_health_potion_service_exception_is_terminal_and_replayed_once(
    app, monkeypatch, frozen_time
):
    import habitica_gameplay

    calls = 0

    def purchase(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError("simulated private upstream failure")

    monkeypatch.setattr(habitica_gameplay, "purchase_health_potion", purchase)
    body = issued_potion_body()

    first = app.test_client().post(
        "/miniapp/api/health-potion", headers=auth_header(), json=body
    )
    second = app.test_client().post(
        "/miniapp/api/health-potion", headers=auth_header(), json=body
    )

    assert first.status_code == 500
    assert second.status_code == first.status_code
    assert second.get_json() == first.get_json()
    assert calls == 1
    assert "simulated private" not in first.get_data(as_text=True)


def test_health_potion_confirmed_sync_required_success_is_terminal(
    app, monkeypatch, frozen_time
):
    import habitica_gameplay

    calls = 0

    def purchase(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return gameplay_ok(
            {
                "profile": None,
                "syncRequired": True,
                "potion": {"name": "Health Potion", "price": 25, "healing": None},
            }
        )

    monkeypatch.setattr(habitica_gameplay, "purchase_health_potion", purchase)
    body = issued_potion_body()
    first = app.test_client().post(
        "/miniapp/api/health-potion", headers=auth_header(), json=body
    )
    second = app.test_client().post(
        "/miniapp/api/health-potion", headers=auth_header(), json=body
    )

    assert first.status_code == 200
    assert first.json["profile"] is None
    assert first.json["syncRequired"] is True
    assert second.get_json() == first.get_json()
    assert calls == 1


@pytest.mark.parametrize(
    ("method", "service_name"),
    [
        ("get", "get_health_potion_status"),
        ("post", "purchase_health_potion"),
    ],
)
def test_health_potion_service_day_gate_is_returned_consistently(
    app, monkeypatch, frozen_time, method, service_name
):
    import habitica_gameplay

    monkeypatch.setattr(
        habitica_gameplay,
        service_name,
        lambda *_args, **_kwargs: gameplay_failure("day_refresh_required"),
    )
    kwargs = {"json": issued_potion_body()} if method == "post" else {}

    response = getattr(app.test_client(), method)(
        "/miniapp/api/health-potion", headers=auth_header(), **kwargs
    )

    assert response.status_code == 409
    assert response.json["error"]["code"] == "day_refresh_required"


@pytest.mark.parametrize(
    "body",
    [
        None,
        {},
        {"quantity": 2},
        {"telegramUserId": 424242},
        {"purchaseIntent": "A" * 43, "quantity": 2},
    ],
)
def test_health_potion_purchase_rejects_missing_or_extra_fields(
    app, monkeypatch, frozen_time, body
):
    import habitica_gameplay

    monkeypatch.setattr(
        habitica_gameplay,
        "purchase_health_potion",
        lambda *_args, **_kwargs: pytest.fail("invalid body must not purchase"),
    )
    kwargs = {"json": body} if body is not None else {}
    response = app.test_client().post(
        "/miniapp/api/health-potion", headers=auth_header(), **kwargs
    )

    assert response.status_code == 400
    assert response.json["error"]["code"] == "invalid_request"


@pytest.mark.parametrize("token", [None, 4, "short", "x" * 129, "!" * 43])
def test_health_potion_purchase_rejects_malformed_intent_before_service(
    app, monkeypatch, frozen_time, token
):
    import habitica_gameplay

    monkeypatch.setattr(
        habitica_gameplay,
        "purchase_health_potion",
        lambda *_args, **_kwargs: pytest.fail("malformed intent must not purchase"),
    )
    response = app.test_client().post(
        "/miniapp/api/health-potion",
        headers=auth_header(),
        json={"purchaseIntent": token},
    )

    assert response.status_code == 400
    assert response.json["error"]["code"] == "invalid_purchase_intent"


def test_day_gate_blocks_task_and_checklist_scores_before_task_lookup(
    app, monkeypatch, frozen_time
):
    import Habitica_API

    monkeypatch.setattr(
        backend,
        "_ensure_gameplay_ready",
        lambda _account: backend._error(
            "day_refresh_required",
            "Review yesterday's Dailies before starting the new day.",
            backend.HTTPStatus.CONFLICT,
        ),
    )
    monkeypatch.setattr(
        Habitica_API,
        "get_task_result",
        lambda *_args: pytest.fail("day-gated score must not load or mutate a task"),
    )

    score = app.test_client().post(
        f"/miniapp/api/tasks/{TASK_ID}/score",
        headers=auth_header(),
        json={"direction": "up"},
    )
    checklist = app.test_client().post(
        f"/miniapp/api/tasks/{TASK_ID}/checklist/{ITEM_ID}/score",
        headers=auth_header(),
        json={"completed": True},
    )

    assert score.status_code == 409
    assert checklist.status_code == 409
    assert score.json["error"]["code"] == "day_refresh_required"
    assert checklist.json["error"]["code"] == "day_refresh_required"


@pytest.mark.parametrize(
    ("payload", "task_type"),
    [
        ({"type": "habit", "text": "Read", "up": True, "down": False}, "habit"),
        ({"type": "daily", "text": "Walk", "repeatDays": ["m"]}, "daily"),
        ({"type": "todo", "text": "Ship", "date": "2026-08-31"}, "todo"),
    ],
)
def test_create_supported_task_types(app, monkeypatch, frozen_time, payload, task_type):
    import Habitica_API

    sent = []

    def create(_user, _key, body):
        sent.append(body)
        return ok(raw_task(task_type, **body), 201)

    monkeypatch.setattr(Habitica_API, "create_task_result", create)
    response = app.test_client().post("/miniapp/api/tasks", headers=auth_header(), json=payload)
    assert response.status_code == 201
    assert response.json["task"]["type"] == task_type
    assert sent[0]["type"] == task_type


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "habit", "text": "", "up": True},
        {"type": "habit", "text": "x", "priority": 4},
        {"type": "daily", "text": "x", "repeatDays": []},
        {"type": "todo", "text": "x", "date": "bad"},
        {"type": "todo", "text": "x", "checklist": "bad"},
    ],
)
def test_invalid_create_never_calls_habitica(app, monkeypatch, frozen_time, payload):
    import Habitica_API

    monkeypatch.setattr(
        Habitica_API,
        "create_task_result",
        lambda *_args: pytest.fail("invalid body reached Habitica"),
    )
    response = app.test_client().post("/miniapp/api/tasks", headers=auth_header(), json=payload)
    assert response.status_code == 400


def test_edit_gets_authoritative_task_then_updates(app, monkeypatch, frozen_time):
    import Habitica_API

    current = raw_task("todo")
    calls = []
    monkeypatch.setattr(Habitica_API, "get_task_result", lambda *_args: ok(current))

    def update(_user, _key, task_id, fields):
        calls.append((task_id, fields))
        return ok({**current, **fields})

    monkeypatch.setattr(Habitica_API, "update_task_result", update)
    response = app.test_client().patch(
        f"/miniapp/api/tasks/{TASK_ID}",
        headers=auth_header(),
        json={"revision": revision_for(current), "text": "Updated", "date": None},
    )
    assert response.status_code == 200
    assert response.json["task"]["text"] == "Updated"
    assert calls == [(TASK_ID, {"text": "Updated"})]


def test_edit_rejects_stale_revision_before_mutating(app, monkeypatch, frozen_time):
    import Habitica_API

    current = raw_task("todo")
    monkeypatch.setattr(Habitica_API, "get_task_result", lambda *_args: ok(current))
    monkeypatch.setattr(
        Habitica_API,
        "update_task_result",
        lambda *_args: pytest.fail("stale edit must not mutate"),
    )
    response = app.test_client().patch(
        f"/miniapp/api/tasks/{TASK_ID}",
        headers=auth_header(),
        json={"revision": "0" * 32, "text": "stale"},
    )
    assert response.status_code == 409
    assert response.json["error"]["code"] == "conflict"


def test_unchanged_simple_daily_does_not_issue_put(app, monkeypatch, frozen_time):
    import Habitica_API

    current = raw_task("daily")
    monkeypatch.setattr(Habitica_API, "get_task_result", lambda *_args: ok(current))
    monkeypatch.setattr(
        Habitica_API,
        "update_task_result",
        lambda *_args: pytest.fail("unchanged daily must not be written"),
    )
    response = app.test_client().patch(
        f"/miniapp/api/tasks/{TASK_ID}",
        headers=auth_header(),
        json={
            "revision": revision_for(current),
            "text": current["text"],
            "notes": current["notes"],
            "priority": current["priority"],
            "repeatDays": ["m"],
            "checklist": [],
        },
    )
    assert response.status_code == 200
    assert response.json["task"]["revision"] == revision_for(current)


def test_edit_reconciles_checklist_operations(app, monkeypatch, frozen_time):
    import Habitica_API

    old = raw_task(
        "todo",
        checklist=[{"id": ITEM_ID, "text": "old", "completed": False}],
    )
    monkeypatch.setattr(Habitica_API, "get_task_result", lambda *_args: ok(old))
    monkeypatch.setattr(
        Habitica_API,
        "update_checklist_item_result",
        lambda *_args: ok(
            raw_task(
                "todo",
                checklist=[{"id": ITEM_ID, "text": "new", "completed": False}],
            )
        ),
    )
    response = app.test_client().patch(
        f"/miniapp/api/tasks/{TASK_ID}",
        headers=auth_header(),
        json={
            "revision": revision_for(old),
            "checklist": [
                {"id": ITEM_ID, "text": "new", "completed": False},
            ],
        },
    )
    assert response.status_code == 200
    assert response.json["task"]["checklist"][0]["text"] == "new"


def test_delete_checks_capability_and_returns_minimal_tombstone(app, monkeypatch, frozen_time):
    import Habitica_API

    monkeypatch.setattr(Habitica_API, "get_task_result", lambda *_args: ok(raw_task("todo")))
    monkeypatch.setattr(Habitica_API, "delete_task_result", lambda *_args: ok({}))
    response = app.test_client().delete(f"/miniapp/api/tasks/{TASK_ID}", headers=auth_header())
    assert response.status_code == 200
    assert response.json == {
        "ok": True,
        "deleted": {"id": TASK_ID, "type": "todo"},
    }


@pytest.mark.parametrize(
    ("direction", "counter_name"),
    [("up", "counterUp"), ("down", "counterDown")],
)
def test_habit_scoring_reconciles_with_follow_up_get(
    app, monkeypatch, frozen_time, direction, counter_name
):
    import Habitica_API

    calls = 0

    def get_task(*_args):
        nonlocal calls
        calls += 1
        return ok(
            raw_task(
                "habit",
                counterUp=calls if direction == "up" else 0,
                counterDown=calls if direction == "down" else 0,
            )
        )

    monkeypatch.setattr(Habitica_API, "get_task_result", get_task)
    monkeypatch.setattr(
        Habitica_API,
        "score_task_result",
        lambda *_args: ok(
            {
                "delta": 1,
                "_tmp": {"scoreSecret": "score-secret-marker"},
                "hp": 49.5,
                "maxHealth": 50,
                "exp": 101,
                "toNextLevel": 200,
                "mp": 37.25,
                "maxMP": 80,
                "gp": 77.5,
                "lvl": 19,
                "class": "rogue",
                "points": 4,
            }
        ),
    )
    response = app.test_client().post(
        f"/miniapp/api/tasks/{TASK_ID}/score",
        headers=auth_header(),
        json={"direction": direction},
    )
    assert response.status_code == 200
    assert response.json["task"][counter_name] == 2
    assert response.json["profilePatch"] == {
        "level": 19,
        "hp": 49.5,
        "maxHp": 50,
        "exp": 101,
        "maxExp": 200,
        "mp": 37.25,
        "maxMp": 80,
        "gold": 77.5,
    }
    assert "score-secret-marker" not in response.get_data(as_text=True)
    assert "class" not in response.json["profilePatch"]
    assert "points" not in response.json["profilePatch"]
    assert calls == 2


@pytest.mark.parametrize(("completed", "direction"), [(False, "up"), (True, "down")])
def test_daily_and_todo_completion_direction_is_state_checked(
    app, monkeypatch, frozen_time, completed, direction
):
    import Habitica_API

    calls = 0

    def get_task(*_args):
        nonlocal calls
        calls += 1
        return ok(raw_task("todo", completed=completed if calls == 1 else not completed))

    monkeypatch.setattr(Habitica_API, "get_task_result", get_task)
    monkeypatch.setattr(
        Habitica_API,
        "score_task_result",
        lambda *_args: ok({"delta": 1, "hp": 48, "gp": 12.5, "lvl": 3}),
    )
    response = app.test_client().post(
        f"/miniapp/api/tasks/{TASK_ID}/score",
        headers=auth_header(),
        json={"direction": direction},
    )
    assert response.status_code == 200
    assert response.json["task"]["completed"] is (not completed)


def test_score_confirmed_but_reconcile_failed_reports_applied(app, monkeypatch, frozen_time):
    import Habitica_API

    calls = 0

    def get_task(*_args):
        nonlocal calls
        calls += 1
        return (
            ok(raw_task("todo"))
            if calls == 1
            else failure(HabiticaErrorKind.UPSTREAM_ERROR, status=503)
        )

    monkeypatch.setattr(Habitica_API, "get_task_result", get_task)
    monkeypatch.setattr(
        Habitica_API,
        "score_task_result",
        lambda *_args: ok({"delta": 1, "hp": 48, "gp": 12.5, "lvl": 3}),
    )
    response = app.test_client().post(
        f"/miniapp/api/tasks/{TASK_ID}/score",
        headers=auth_header(),
        json={"direction": "up"},
    )
    assert response.status_code == 200
    assert response.json["reloadRequired"] is True
    assert response.json["task"]["completed"] is True
    assert response.json["profilePatch"] == {"level": 3, "hp": 48, "gold": 12.5}


@pytest.mark.parametrize(
    ("direction", "counter_name"),
    [("up", "counterUp"), ("down", "counterDown")],
)
def test_stale_habit_reconciliation_cannot_restore_an_older_counter(
    app, monkeypatch, frozen_time, direction, counter_name
):
    import Habitica_API

    stale = raw_task("habit", counterUp=4, counterDown=7, frequency="monthly")
    monkeypatch.setattr(Habitica_API, "get_task_result", lambda *_args: ok(stale))
    monkeypatch.setattr(Habitica_API, "score_task_result", lambda *_args: ok({"hp": 49}))

    response = app.test_client().post(
        f"/miniapp/api/tasks/{TASK_ID}/score",
        headers=auth_header(),
        json={"direction": direction},
    )

    assert response.status_code == 200
    assert response.json["reloadRequired"] is True
    assert response.json["task"][counter_name] == stale[counter_name] + 1
    assert response.json["task"]["counterFrequency"] == "monthly"


def test_score_profile_patch_omits_invalid_and_unapproved_fields(
    app, monkeypatch, frozen_time
):
    import Habitica_API

    calls = 0

    def get_task(*_args):
        nonlocal calls
        calls += 1
        return ok(raw_task("habit", counterUp=calls))

    monkeypatch.setattr(Habitica_API, "get_task_result", get_task)
    monkeypatch.setattr(
        Habitica_API,
        "score_task_result",
        lambda *_args: ok(
            {
                "lvl": True,
                "hp": float("nan"),
                "maxHealth": float("inf"),
                "exp": "12",
                "toNextLevel": -1,
                "mp": None,
                "maxMP": {},
                "gp": -0.1,
                "apiKey": "must-not-leak",
                "auth": {"local": {"username": "score-private-marker"}},
            }
        ),
    )
    response = app.test_client().post(
        f"/miniapp/api/tasks/{TASK_ID}/score",
        headers=auth_header(),
        json={"direction": "up"},
    )
    assert response.status_code == 200
    assert response.json["profilePatch"] == {}
    assert "must-not-leak" not in response.get_data(as_text=True)
    assert "score-private-marker" not in response.get_data(as_text=True)


def test_score_reconciliation_checks_requested_completion_postcondition(
    app, monkeypatch, frozen_time
):
    import Habitica_API

    calls = 0

    def get_task(*_args):
        nonlocal calls
        calls += 1
        return ok(raw_task("todo", completed=False))

    monkeypatch.setattr(Habitica_API, "get_task_result", get_task)
    monkeypatch.setattr(Habitica_API, "score_task_result", lambda *_args: ok({"delta": 1}))
    response = app.test_client().post(
        f"/miniapp/api/tasks/{TASK_ID}/score",
        headers=auth_header(),
        json={"direction": "up"},
    )
    assert response.status_code == 409
    assert response.json["error"]["code"] == "conflict"
    assert response.json["reconcileRequired"] is True


def test_ambiguous_score_is_not_reported_as_safe_retry(app, monkeypatch, frozen_time):
    import Habitica_API

    monkeypatch.setattr(Habitica_API, "get_task_result", lambda *_args: ok(raw_task("habit")))
    monkeypatch.setattr(
        Habitica_API,
        "score_task_result",
        lambda *_args: failure(HabiticaErrorKind.REQUEST_TIMEOUT, outcome_unknown=True),
    )
    response = app.test_client().post(
        f"/miniapp/api/tasks/{TASK_ID}/score",
        headers=auth_header(),
        json={"direction": "up"},
    )
    assert response.status_code == 502
    assert response.json["error"]["code"] == "outcome_unknown"
    assert response.json["reconcileRequired"] is True
    assert "profilePatch" not in response.json


def test_checklist_desired_state_makes_repeated_request_safe(app, monkeypatch, frozen_time):
    import Habitica_API

    score_calls = 0

    def get_task(*_args):
        return ok(
            raw_task(
                "todo",
                checklist=[{"id": ITEM_ID, "text": "Step", "completed": True}],
            )
        )

    def score(*_args):
        nonlocal score_calls
        score_calls += 1
        return ok({})

    monkeypatch.setattr(Habitica_API, "get_task_result", get_task)
    monkeypatch.setattr(Habitica_API, "score_checklist_item_result", score)
    response = app.test_client().post(
        f"/miniapp/api/tasks/{TASK_ID}/checklist/{ITEM_ID}/score",
        headers=auth_header(),
        json={"completed": True},
    )
    assert response.status_code == 200
    assert score_calls == 0


def test_checklist_change_uses_idempotent_completed_only_put(app, monkeypatch, frozen_time):
    import Habitica_API

    current = raw_task(
        "todo",
        checklist=[{"id": ITEM_ID, "text": "keep this text", "completed": False}],
    )
    sent = []
    monkeypatch.setattr(Habitica_API, "get_task_result", lambda *_args: ok(current))

    def update(_user, _key, _task_id, _item_id, body):
        sent.append(body)
        return ok(
            raw_task(
                "todo",
                checklist=[{"id": ITEM_ID, "text": "externally changed", "completed": True}],
            )
        )

    monkeypatch.setattr(Habitica_API, "update_checklist_item_result", update)
    response = app.test_client().post(
        f"/miniapp/api/tasks/{TASK_ID}/checklist/{ITEM_ID}/score",
        headers=auth_header(),
        json={"completed": True},
    )
    assert response.status_code == 200
    assert sent == [{"completed": True}]
    assert response.json["task"]["checklist"][0]["text"] == "externally changed"


@pytest.mark.parametrize(
    ("result", "status", "code"),
    [
        (failure(HabiticaErrorKind.INVALID_CREDENTIALS, status=401), 401, "unauthorized"),
        (failure(HabiticaErrorKind.NOT_FOUND, status=404), 404, "task_not_found"),
        (failure(HabiticaErrorKind.RATE_LIMITED, status=429, retry_after=4.2), 429, "rate_limited"),
        (failure(HabiticaErrorKind.UPSTREAM_ERROR, status=503), 502, "habitica_unavailable"),
    ],
)
def test_upstream_errors_are_stable_and_sanitized(
    app, monkeypatch, frozen_time, result, status, code
):
    import Habitica_API

    monkeypatch.setattr(Habitica_API, "get_tasks_result", lambda *_args, **_kwargs: result)
    response = app.test_client().get("/miniapp/api/tasks?type=habit", headers=auth_header())
    assert response.status_code == status
    assert response.json["error"]["code"] == code
    assert "habitica-user" not in response.get_data(as_text=True)
    assert "habitica-key" not in response.get_data(as_text=True)
    if status == 429:
        assert response.headers["Retry-After"] == "5"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/miniapp/api/tasks/" + TASK_ID + "/score"),
        ("get", "/miniapp/api/tasks/" + TASK_ID),
        ("post", "/miniapp/api/tasks/" + TASK_ID),
        ("get", "/miniapp/api/day-refresh"),
        ("post", "/miniapp/api/day-status"),
        ("delete", "/miniapp/api/health-potion"),
    ],
)
def test_wrong_methods_do_not_mutate_and_keep_private_headers(app, method, path):
    response = getattr(app.test_client(), method)(path, headers=auth_header())
    assert response.status_code in {404, 405}
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Vary"] == "Authorization"


def test_oversized_body_is_rejected_with_private_headers(app, frozen_time):
    response = app.test_client().post(
        "/miniapp/api/tasks",
        headers={**auth_header(), "Content-Type": "application/json"},
        data=b'{"text":"' + b"x" * (backend.MAX_TASK_REQUEST_BYTES + 1) + b'"}',
    )
    assert response.status_code == 413
    assert response.json["error"]["code"] == "invalid_request"
    assert response.headers["Cache-Control"] == "no-store"


def test_chunked_style_oversized_body_cannot_bypass_limit(app, frozen_time):
    response = app.test_client().open(
        "/miniapp/api/tasks",
        method="POST",
        headers={**auth_header(), "Content-Type": "application/json"},
        data=b" " * backend.MAX_TASK_REQUEST_BYTES + b'{"type":"todo","text":"x"}',
        environ_overrides={"CONTENT_LENGTH": "", "wsgi.input_terminated": True},
    )
    assert response.status_code == 413
    assert response.json["error"]["code"] == "invalid_request"
    assert response.headers["Cache-Control"] == "no-store"


def test_production_wsgi_keeps_miniapp_413_json_envelope(frozen_time, monkeypatch):
    import webhook_app

    monkeypatch.setattr(backend, "_authenticated_account", lambda: (ACCOUNT, None))
    response = webhook_app.flask_app.test_client().post(
        "/miniapp/api/tasks",
        headers={**auth_header(), "Content-Type": "application/json"},
        data=b"x" * (webhook_app.flask_app.config["MAX_CONTENT_LENGTH"] + 1),
    )
    assert response.status_code == 413
    assert response.is_json
    assert response.json["error"]["code"] == "invalid_request"
    assert response.headers["Cache-Control"] == "no-store"


def test_invalid_task_id_never_reaches_habitica(app, monkeypatch, frozen_time):
    import Habitica_API

    monkeypatch.setattr(
        Habitica_API,
        "get_task_result",
        lambda *_args: pytest.fail("invalid ID reached Habitica"),
    )
    response = app.test_client().delete("/miniapp/api/tasks/not-a-uuid", headers=auth_header())
    assert response.status_code == 400


def test_mutation_lock_paths_use_a_bounded_shard_set(monkeypatch, tmp_path):
    monkeypatch.setenv("BOT_DATA_PATH", str(tmp_path / "state" / "botdata.pkl"))
    paths = {
        backend._task_mutation_lock_path(
            ACCOUNT,
            f"{number:08x}-e89b-42d3-a456-426614174000",
        )
        for number in range(200)
    }
    assert len(paths) <= backend.TASK_MUTATION_LOCK_SHARDS
    assert all(path.parent.name == ".miniapp-task-locks" for path in paths)


def test_gameplay_lock_paths_use_a_bounded_fixed_shard_set(monkeypatch, tmp_path):
    monkeypatch.setenv("BOT_DATA_PATH", str(tmp_path / "state" / "botdata.pkl"))
    paths = {
        backend._gameplay_lock_path(
            backend.LinkedHabiticaAccount(f"habitica-user-{number}", "private-key")
        )
        for number in range(200)
    }
    assert len(paths) <= backend.GAMEPLAY_LOCK_SHARDS
    assert all(path.parent.name == ".miniapp-gameplay-locks" for path in paths)
    assert all(path.name.startswith("gameplay-") for path in paths)


def test_potion_intent_registry_is_bounded_and_keeps_only_digests(
    app, monkeypatch, frozen_time
):
    clock = [100.0]
    monkeypatch.setattr(backend.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(backend, "MAX_POTION_PURCHASE_INTENTS", 3)
    tokens = []
    for _index in range(4):
        token = backend._issue_potion_purchase_intent(ACCOUNT)
        assert token is not None
        tokens.append(token)
        clock[0] += 1

    assert len(set(tokens)) == 4
    with backend._POTION_PURCHASE_INTENTS_LOCK:
        assert len(backend._POTION_PURCHASE_INTENTS) == 3
        assert all(
            isinstance(digest, bytes) and len(digest) == 32
            for digest in backend._POTION_PURCHASE_INTENTS
        )
        assert all(token not in backend._POTION_PURCHASE_INTENTS for token in tokens)
        assert all(
            intent.account_digest != ACCOUNT.habitica_api_key.encode()
            for intent in backend._POTION_PURCHASE_INTENTS.values()
        )

    assert backend._claim_potion_purchase_intent(ACCOUNT, tokens[0])[0] == "invalid"
    assert backend._claim_potion_purchase_intent(ACCOUNT, tokens[-1])[0] == "claimed"


def test_score_lock_nests_gameplay_before_same_task_lock(monkeypatch, tmp_path):
    monkeypatch.setenv("BOT_DATA_PATH", str(tmp_path / "state" / "botdata.pkl"))
    acquired = []

    @contextmanager
    def tracked_lock(*, lock_path, timeout):
        acquired.append((lock_path.parent.name, timeout))
        yield

    monkeypatch.setattr(backend, "acquire_runtime_lock", tracked_lock)

    with backend._acquire_gameplay_task_locks(ACCOUNT, TASK_ID):
        pass

    assert acquired == [
        (".miniapp-gameplay-locks", 0),
        (".miniapp-task-locks", 0),
    ]


def test_potion_metadata_cache_reuses_only_validated_public_fragment(monkeypatch):
    import Habitica_API
    import habitica_gameplay as gameplay

    clock = [100.0]
    content_calls = 0
    monkeypatch.setattr(backend, "_POTION_METADATA_CACHE", None)
    monkeypatch.setattr(backend.time, "monotonic", lambda: clock[0])

    def fetch_content(_user, _key, *, language):
        nonlocal content_calls
        content_calls += 1
        assert language == "en"
        return HabiticaResult(
            data={
                "potion": {
                    "text": "Health Potion",
                    "value": 25,
                    "healing": 15,
                    "privateMarker": "must-not-be-cached",
                },
                "quests": {"privateMarker": "must-not-be-cached"},
            }
        )

    monkeypatch.setattr(Habitica_API, "get_content_result", fetch_content)
    user = {
        "needsCron": False,
        "stats": {
            "lvl": 10,
            "class": "warrior",
            "hp": 25,
            "maxHealth": 50,
            "exp": 5,
            "toNextLevel": 100,
            "mp": 20,
            "maxMP": 30,
            "gp": 100,
        }
    }
    fetch_user = lambda *_args: HabiticaResult(data=user)  # noqa: E731

    status = gameplay.get_health_potion_status(
        "habitica-user",
        "habitica-key",
        fetch_user=fetch_user,
        fetch_content=backend._cached_potion_content_result,
    )
    purchase = gameplay.purchase_health_potion(
        "habitica-user",
        "habitica-key",
        fetch_user=fetch_user,
        fetch_content=backend._cached_potion_content_result,
        buy_potion=lambda *_args: HabiticaResult(data={"stats": user["stats"]}),
    )

    assert status.ok is True
    assert purchase.ok is True
    assert content_calls == 1
    assert backend._POTION_METADATA_CACHE is not None
    assert backend._POTION_METADATA_CACHE[1] == {
        "potion": {"text": "Health Potion", "value": 25, "healing": 15}
    }

    clock[0] += backend.POTION_METADATA_TTL_SECONDS + 1
    backend._cached_potion_content_result("other-user", "other-key", language="en")
    assert content_calls == 2


def test_edit_limits_checklist_batch_before_any_mutation(app, monkeypatch, frozen_time):
    import Habitica_API

    old = raw_task("todo", checklist=[])
    monkeypatch.setattr(Habitica_API, "get_task_result", lambda *_args: ok(old))
    monkeypatch.setattr(
        Habitica_API,
        "add_checklist_item_result",
        lambda *_args: pytest.fail("oversized batch must not mutate"),
    )
    checklist = [
        {"text": f"item {number}", "completed": False}
        for number in range(backend.MAX_CHECKLIST_MUTATIONS_PER_EDIT + 1)
    ]
    response = app.test_client().patch(
        f"/miniapp/api/tasks/{TASK_ID}",
        headers=auth_header(),
        json={"revision": revision_for(old), "checklist": checklist},
    )
    assert response.status_code == 400
    assert response.json["error"]["code"] == "invalid_request"
