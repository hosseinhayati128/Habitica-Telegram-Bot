import logging

import pytest
import requests

import Habitica_API as api


USER_ID = "habitica-user-secret"
API_KEY = "habitica-api-key-secret"


class FakeResponse:
    def __init__(
        self,
        *,
        payload=None,
        status_code=200,
        headers=None,
        chunks=None,
        json_error=None,
        error_message="raw-response-secret",
    ):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}
        self._chunks = chunks or []
        self._json_error = json_error
        self._error_message = error_message
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(
                self._error_message,
                response=self,
            )

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._payload

    def iter_content(self, chunk_size):
        assert chunk_size == api.AVATAR_CHUNK_SIZE
        yield from self._chunks

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def block_unmocked_requests(monkeypatch):
    """Fail rather than contact Habitica if a test forgot its request mock."""

    def unexpected_request(*args, **kwargs):
        raise AssertionError("unexpected network request")

    monkeypatch.setattr(api.requests, "request", unexpected_request)


def install_response(monkeypatch, response):
    calls = []

    def fake_request(*args, **kwargs):
        calls.append((args, kwargs))
        return response

    monkeypatch.setattr(api.requests, "request", fake_request)
    return calls


def test_get_status_uses_canonical_headers_and_connect_read_timeout(monkeypatch):
    response = FakeResponse(payload={"success": True, "data": {"stats": {"hp": 50}}})
    calls = install_response(monkeypatch, response)

    assert api.get_status(USER_ID, API_KEY) == {"stats": {"hp": 50}}
    assert len(calls) == 1

    args, kwargs = calls[0]
    assert args == ("GET", f"{api.BASE_URL}/user")
    assert kwargs["timeout"] == api.REQUEST_TIMEOUT == (5.0, 30.0)
    assert kwargs["headers"] == {
        "x-api-user": USER_ID,
        "x-api-key": API_KEY,
        "x-client": api.CLIENT_ID,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def test_make_request_does_not_allow_header_or_timeout_override(monkeypatch):
    response = FakeResponse(payload={"success": True, "data": {}})
    calls = install_response(monkeypatch, response)

    result = api._make_request(
        "get",
        "/user",
        USER_ID,
        API_KEY,
        expected_data_type=dict,
        headers={"x-api-key": "wrong"},
        timeout=None,
    )

    assert result == {"success": True, "data": {}}
    _, kwargs = calls[0]
    assert kwargs["headers"] == api._headers(USER_ID, API_KEY)
    assert kwargs["timeout"] == api.REQUEST_TIMEOUT


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"success": False, "data": {}},
        {"success": "true", "data": {}},
        {"success": 1, "data": {}},
        {"data": {}},
        {"success": True},
        {"success": True, "data": []},
    ],
)
def test_get_status_rejects_invalid_envelopes_and_data_shapes(monkeypatch, payload):
    calls = install_response(monkeypatch, FakeResponse(payload=payload))

    assert api.get_status(USER_ID, API_KEY) is None
    assert len(calls) == 1


def test_invalid_json_returns_none_without_raising(monkeypatch):
    response = FakeResponse(json_error=ValueError("raw-response-secret"))
    install_response(monkeypatch, response)

    assert api.get_status(USER_ID, API_KEY) is None


def test_get_tasks_preserves_successful_empty_list(monkeypatch):
    calls = install_response(
        monkeypatch,
        FakeResponse(payload={"success": True, "data": []}),
    )

    assert api.get_tasks(USER_ID, API_KEY, "todos") == []
    args, kwargs = calls[0]
    assert args == ("GET", f"{api.BASE_URL}/tasks/user")
    assert kwargs["params"] == {"type": "todos"}


def test_get_tasks_rejects_non_dict_entries(monkeypatch):
    install_response(
        monkeypatch,
        FakeResponse(payload={"success": True, "data": [{"id": "ok"}, "bad"]}),
    )

    assert api.get_tasks(USER_ID, API_KEY, "todos") is None


def test_create_todo_uses_shared_transport_and_validates_task(monkeypatch):
    task = {"id": "task-id", "text": "Write tests", "type": "todo"}
    calls = install_response(
        monkeypatch,
        FakeResponse(payload={"success": True, "data": task}),
    )

    assert api.create_todo_task(USER_ID, API_KEY, "Write tests", 1.5) == task
    args, kwargs = calls[0]
    assert args == ("POST", f"{api.BASE_URL}/tasks/user")
    assert kwargs["json"] == {
        "text": "Write tests",
        "type": "todo",
        "priority": 1.5,
    }
    assert kwargs["headers"] == api._headers(USER_ID, API_KEY)
    assert kwargs["timeout"] == api.REQUEST_TIMEOUT


def test_create_todo_rejects_non_object_task_data(monkeypatch):
    install_response(
        monkeypatch,
        FakeResponse(payload={"success": True, "data": []}),
    )

    assert api.create_todo_task(USER_ID, API_KEY, "Write tests", 1.5) is None


def test_task_ids_are_quoted_as_one_path_segment(monkeypatch):
    task = {"id": "task/with ?#"}
    calls = install_response(
        monkeypatch,
        FakeResponse(payload={"success": True, "data": task}),
    )

    assert api.get_task_by_id(USER_ID, API_KEY, "task/with ?#") == task
    args, _ = calls[0]
    assert args == (
        "GET",
        f"{api.BASE_URL}/tasks/task%2Fwith%20%3F%23",
    )


@pytest.mark.parametrize("task_id", ["", ".", "..", None])
def test_invalid_task_ids_do_not_make_requests(task_id):
    assert api.get_task_by_id(USER_ID, API_KEY, task_id) is None
    assert api.buy_reward(USER_ID, API_KEY, task_id) is False


def test_score_rejects_invalid_direction_without_request(caplog):
    caplog.set_level(logging.WARNING, logger=api.__name__)

    assert api.score_task(USER_ID, API_KEY, "task-secret", "sideways") is None
    assert USER_ID not in caplog.text
    assert API_KEY not in caplog.text
    assert "task-secret" not in caplog.text


def test_score_requires_object_data(monkeypatch):
    install_response(
        monkeypatch,
        FakeResponse(payload={"success": True, "data": []}),
    )

    assert api.score_task(USER_ID, API_KEY, "task-id", "up") is None


@pytest.mark.parametrize(
    "operation",
    [
        lambda: api.create_todo_task(USER_ID, API_KEY, "Todo", 1.0),
        lambda: api.score_task(USER_ID, API_KEY, "task-id", "up"),
        lambda: api.buy_potion(USER_ID, API_KEY),
        lambda: api.buy_reward(USER_ID, API_KEY, "task-id"),
        lambda: api.run_cron(USER_ID, API_KEY),
    ],
)
def test_mutations_are_attempted_only_once_on_http_error(monkeypatch, operation):
    response = FakeResponse(status_code=401)
    calls = install_response(monkeypatch, response)

    result = operation()

    assert result is None or result is False
    assert len(calls) == 1
    assert calls[0][0][0] == "POST"


@pytest.mark.parametrize(
    "error_type",
    [requests.exceptions.Timeout, requests.exceptions.ConnectionError],
)
def test_network_error_is_not_retried_and_logs_no_sensitive_values(
    monkeypatch,
    caplog,
    error_type,
):
    calls = []

    def fail_request(*args, **kwargs):
        calls.append((args, kwargs))
        raise error_type(f"{USER_ID} {API_KEY} raw-response-secret")

    monkeypatch.setattr(api.requests, "request", fail_request)
    caplog.set_level(logging.WARNING, logger=api.__name__)

    assert api.get_status(USER_ID, API_KEY) is None
    assert len(calls) == 1
    assert error_type.__name__ in caplog.text
    assert USER_ID not in caplog.text
    assert API_KEY not in caplog.text
    assert "raw-response-secret" not in caplog.text


def test_http_error_logs_status_but_not_body_or_credentials(monkeypatch, caplog):
    response = FakeResponse(
        status_code=403,
        error_message=f"{USER_ID} {API_KEY} raw-response-secret",
    )
    install_response(monkeypatch, response)
    caplog.set_level(logging.WARNING, logger=api.__name__)

    assert api.get_status(USER_ID, API_KEY) is None
    assert "status=403" in caplog.text
    assert USER_ID not in caplog.text
    assert API_KEY not in caplog.text
    assert "raw-response-secret" not in caplog.text


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"success": True}, True),
        ({"success": False}, False),
        ({"success": "true"}, False),
        ({"success": 1}, False),
        ({}, False),
    ],
)
def test_purchase_helpers_return_literal_bool(monkeypatch, payload, expected):
    install_response(monkeypatch, FakeResponse(payload=payload))

    result = api.buy_potion(USER_ID, API_KEY)

    assert result is expected
    assert type(result) is bool


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"success": True}, True),
        ({"success": False}, False),
        ({"success": "true"}, False),
        ({"data": {}}, False),
        ([], False),
    ],
)
def test_run_cron_requires_explicit_success(monkeypatch, payload, expected):
    calls = install_response(monkeypatch, FakeResponse(payload=payload))

    result = api.run_cron(USER_ID, API_KEY)

    assert result is expected
    assert type(result) is bool
    assert calls[0][0] == ("POST", f"{api.BASE_URL}/cron")


def test_avatar_export_returns_valid_bounded_png(monkeypatch):
    png = api.PNG_SIGNATURE + b"png-data"
    response = FakeResponse(
        headers={
            "Content-Type": "image/png; charset=binary",
            "Content-Length": str(len(png)),
        },
        chunks=[png[:3], b"", png[3:]],
    )
    calls = install_response(monkeypatch, response)

    assert api.export_avatar_png(USER_ID, API_KEY) == png
    assert response.closed is True

    args, kwargs = calls[0]
    assert args == ("GET", "https://habitica.com/export/avatar-plain.png")
    assert kwargs["timeout"] == api.REQUEST_TIMEOUT
    assert kwargs["stream"] is True
    assert kwargs["headers"]["x-api-user"] == USER_ID
    assert kwargs["headers"]["x-api-key"] == API_KEY
    assert kwargs["headers"]["x-client"] == api.CLIENT_ID
    assert kwargs["headers"]["Accept"] == "image/png"


@pytest.mark.parametrize(
    ("headers", "chunks"),
    [
        ({"Content-Type": "text/html"}, [api.PNG_SIGNATURE]),
        ({"Content-Type": "image/png"}, [b"not-a-png"]),
        ({"Content-Type": "image/png", "Content-Length": "invalid"}, [api.PNG_SIGNATURE]),
    ],
)
def test_avatar_export_rejects_invalid_metadata_or_signature(
    monkeypatch,
    headers,
    chunks,
):
    response = FakeResponse(headers=headers, chunks=chunks)
    install_response(monkeypatch, response)

    assert api.export_avatar_png(USER_ID, API_KEY) is None
    assert response.closed is True


def test_avatar_export_rejects_declared_oversize(monkeypatch):
    response = FakeResponse(
        headers={
            "Content-Type": "image/png",
            "Content-Length": str(api.MAX_AVATAR_BYTES + 1),
        },
        chunks=[api.PNG_SIGNATURE],
    )
    install_response(monkeypatch, response)

    assert api.export_avatar_png(USER_ID, API_KEY) is None
    assert response.closed is True


def test_avatar_export_stops_when_stream_exceeds_limit(monkeypatch):
    monkeypatch.setattr(api, "MAX_AVATAR_BYTES", len(api.PNG_SIGNATURE))
    response = FakeResponse(
        headers={"Content-Type": "image/png"},
        chunks=[api.PNG_SIGNATURE, b"x"],
    )
    install_response(monkeypatch, response)

    assert api.export_avatar_png(USER_ID, API_KEY) is None
    assert response.closed is True


def test_avatar_request_error_logs_only_exception_class(monkeypatch, caplog):
    def fail_request(*args, **kwargs):
        raise requests.exceptions.Timeout(f"{USER_ID} {API_KEY} raw-response-secret")

    monkeypatch.setattr(api.requests, "request", fail_request)
    caplog.set_level(logging.WARNING, logger=api.__name__)

    assert api.export_avatar_png(USER_ID, API_KEY) is None
    assert "Timeout" in caplog.text
    assert USER_ID not in caplog.text
    assert API_KEY not in caplog.text
    assert "raw-response-secret" not in caplog.text
