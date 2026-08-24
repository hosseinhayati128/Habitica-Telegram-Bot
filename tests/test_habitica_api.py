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
        json_error=None,
        error_message="raw-response-secret",
    ):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}
        self._json_error = json_error
        self._error_message = error_message

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


def test_headers_support_configured_habitica_client_id(monkeypatch):
    monkeypatch.setenv("HABITICA_CLIENT_ID", "configured-client-id")

    assert api._headers(USER_ID, API_KEY)["x-client"] == "configured-client-id"


@pytest.mark.parametrize(
    "configured",
    ["", "   ", "bad\nheader", "non-ascii-💥", "x" * 201],
)
def test_headers_fall_back_for_empty_or_unsafe_client_id(monkeypatch, configured):
    monkeypatch.setenv("HABITICA_CLIENT_ID", configured)

    assert api._headers(USER_ID, API_KEY)["x-client"] == api.CLIENT_ID


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
    assert kwargs["allow_redirects"] is False


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


def test_typed_list_returns_validated_data_and_uses_compact_history(monkeypatch):
    tasks = [{"id": "task-id", "type": "daily"}]
    calls = install_response(
        monkeypatch,
        FakeResponse(payload={"success": True, "data": tasks}),
    )

    result = api.get_tasks_result(USER_ID, API_KEY, "dailys")

    assert result == api.HabiticaResult(data=tasks, status=200)
    assert result.ok is True
    args, kwargs = calls[0]
    assert args == ("GET", f"{api.BASE_URL}/tasks/user")
    assert kwargs["params"] == {"type": "dailys", "history": "false"}
    assert kwargs["headers"] == api._headers(USER_ID, API_KEY)
    assert kwargs["timeout"] == api.REQUEST_TIMEOUT
    assert kwargs["allow_redirects"] is False


@pytest.mark.parametrize("task_type", ["daily", "dailies", "bad", "", None])
def test_typed_list_rejects_non_upstream_task_types_without_request(task_type):
    result = api.get_tasks_result(USER_ID, API_KEY, task_type)

    assert result.ok is False
    assert result.error.kind is api.HabiticaErrorKind.INVALID_INPUT
    assert result.error.outcome_unknown is False


def test_typed_list_rejects_invalid_entries(monkeypatch):
    install_response(
        monkeypatch,
        FakeResponse(payload={"success": True, "data": [{"id": "ok"}, "bad"]}),
    )

    result = api.get_tasks_result(USER_ID, API_KEY, "todos")

    assert result.ok is False
    assert result.status == 200
    assert result.error.kind is api.HabiticaErrorKind.INVALID_RESPONSE
    assert result.error.outcome_unknown is False


def test_typed_create_accepts_201_and_returns_created_task(monkeypatch):
    request_task = {"type": "habit", "text": "Stretch", "up": True, "down": False}
    created_task = {"id": "created-id", **request_task}
    calls = install_response(
        monkeypatch,
        FakeResponse(
            status_code=201,
            payload={"success": True, "data": created_task},
        ),
    )

    result = api.create_task_result(USER_ID, API_KEY, request_task)

    assert result == api.HabiticaResult(data=created_task, status=201)
    assert result.ok is True
    args, kwargs = calls[0]
    assert args == ("POST", f"{api.BASE_URL}/tasks/user")
    assert kwargs["json"] == request_task
    assert kwargs["json"] is not request_task


def test_typed_update_uses_put_and_quotes_task_id(monkeypatch):
    updated_task = {"id": "task/with ?#", "text": "Updated"}
    calls = install_response(
        monkeypatch,
        FakeResponse(payload={"success": True, "data": updated_task}),
    )

    result = api.update_task_result(
        USER_ID,
        API_KEY,
        "task/with ?#",
        {"text": "Updated"},
    )

    assert result.data == updated_task
    args, kwargs = calls[0]
    assert args == (
        "PUT",
        f"{api.BASE_URL}/tasks/task%2Fwith%20%3F%23",
    )
    assert kwargs["json"] == {"text": "Updated"}


def test_typed_delete_returns_empty_data_object(monkeypatch):
    calls = install_response(
        monkeypatch,
        FakeResponse(payload={"success": True, "data": {}}),
    )

    result = api.delete_task_result(USER_ID, API_KEY, "task-id")

    assert result == api.HabiticaResult(data={}, status=200)
    assert calls[0][0] == ("DELETE", f"{api.BASE_URL}/tasks/task-id")


def test_typed_score_returns_stats_not_an_updated_task(monkeypatch):
    stats = {"delta": 0.9747, "hp": 50, "exp": 20, "gp": 4, "_tmp": {}}
    calls = install_response(
        monkeypatch,
        FakeResponse(payload={"success": True, "data": stats}),
    )

    result = api.score_task_result(USER_ID, API_KEY, "task-id", "up")

    assert result.data == stats
    assert result.status == 200
    assert calls[0][0] == (
        "POST",
        f"{api.BASE_URL}/tasks/task-id/score/up",
    )


def test_typed_score_accepts_other_successful_2xx_status(monkeypatch):
    stats = {"requiresApproval": True}
    install_response(
        monkeypatch,
        FakeResponse(
            status_code=202,
            payload={"success": True, "data": stats},
        ),
    )

    result = api.score_task_result(USER_ID, API_KEY, "task-id", "up")

    assert result == api.HabiticaResult(data=stats, status=202)


def test_typed_checklist_score_quotes_both_ids_and_returns_task(monkeypatch):
    task = {
        "id": "task/id",
        "checklist": [{"id": "item/id", "text": "Part", "completed": True}],
    }
    calls = install_response(
        monkeypatch,
        FakeResponse(payload={"success": True, "data": task}),
    )

    result = api.score_checklist_item_result(
        USER_ID,
        API_KEY,
        "task/id",
        "item/id",
    )

    assert result.data == task
    assert calls[0][0] == (
        "POST",
        f"{api.BASE_URL}/tasks/task%2Fid/checklist/item%2Fid/score",
    )


def test_typed_checklist_crud_uses_dedicated_endpoints(monkeypatch):
    task = {"id": "task/id", "checklist": []}
    calls = install_response(
        monkeypatch,
        FakeResponse(payload={"success": True, "data": task}),
    )

    added = api.add_checklist_item_result(
        USER_ID,
        API_KEY,
        "task/id",
        {"text": "New item", "completed": False},
    )
    updated = api.update_checklist_item_result(
        USER_ID,
        API_KEY,
        "task/id",
        "item/id",
        {"text": "Changed", "completed": True},
    )
    deleted = api.delete_checklist_item_result(
        USER_ID,
        API_KEY,
        "task/id",
        "item/id",
    )

    assert added.data == updated.data == deleted.data == task
    assert calls[0][0] == (
        "POST",
        f"{api.BASE_URL}/tasks/task%2Fid/checklist",
    )
    assert calls[0][1]["json"] == {"text": "New item", "completed": False}
    assert calls[1][0] == (
        "PUT",
        f"{api.BASE_URL}/tasks/task%2Fid/checklist/item%2Fid",
    )
    assert calls[1][1]["json"] == {"text": "Changed", "completed": True}
    assert calls[2][0] == (
        "DELETE",
        f"{api.BASE_URL}/tasks/task%2Fid/checklist/item%2Fid",
    )


@pytest.mark.parametrize(
    ("operation", "expected_method"),
    [
        (lambda: api.get_task_result(USER_ID, API_KEY, ""), "GET"),
        (lambda: api.update_task_result(USER_ID, API_KEY, ".", {}), "PUT"),
        (lambda: api.delete_task_result(USER_ID, API_KEY, "\x00"), "DELETE"),
        (lambda: api.score_task_result(USER_ID, API_KEY, "task-id", "sideways"), "POST"),
        (
            lambda: api.score_checklist_item_result(
                USER_ID,
                API_KEY,
                "task-id",
                "..",
            ),
            "POST",
        ),
        (
            lambda: api.add_checklist_item_result(USER_ID, API_KEY, "..", {}),
            "POST",
        ),
        (
            lambda: api.update_checklist_item_result(
                USER_ID,
                API_KEY,
                "task-id",
                "\x00",
                {},
            ),
            "PUT",
        ),
        (
            lambda: api.delete_checklist_item_result(
                USER_ID,
                API_KEY,
                "task-id",
                ".",
            ),
            "DELETE",
        ),
    ],
)
def test_typed_invalid_path_or_direction_does_not_request(operation, expected_method):
    result = operation()

    assert result.ok is False
    assert result.status is None
    assert result.error == api.HabiticaAPIError(
        kind=api.HabiticaErrorKind.INVALID_INPUT,
    )


@pytest.mark.parametrize(
    ("upstream_code", "expected_kind"),
    [
        ("invalid_credentials", api.HabiticaErrorKind.INVALID_CREDENTIALS),
        ("NotAuthorized", api.HabiticaErrorKind.UNAUTHORIZED),
        (None, api.HabiticaErrorKind.UNAUTHORIZED),
    ],
)
def test_typed_401_distinguishes_invalid_credentials(
    monkeypatch,
    upstream_code,
    expected_kind,
):
    payload = {
        "success": False,
        "error": upstream_code,
        "message": f"{USER_ID} {API_KEY} raw-response-secret",
    }
    install_response(
        monkeypatch,
        FakeResponse(status_code=401, payload=payload),
    )

    result = api.score_task_result(USER_ID, API_KEY, "task-id", "up")

    assert result.ok is False
    assert result.status == 401
    assert result.error.kind is expected_kind
    assert result.error.status == 401
    assert result.error.outcome_unknown is False


def test_typed_explicit_invalid_credentials_wins_over_http_status(monkeypatch):
    install_response(
        monkeypatch,
        FakeResponse(
            status_code=403,
            payload={"success": False, "error": "invalid_credentials"},
        ),
    )

    result = api.get_user_result(USER_ID, API_KEY)

    assert result.error.kind is api.HabiticaErrorKind.INVALID_CREDENTIALS


def test_typed_transport_cannot_be_made_to_follow_redirects(monkeypatch):
    calls = install_response(
        monkeypatch,
        FakeResponse(
            status_code=302,
            payload={"success": False, "error": "Redirect"},
        ),
    )

    result = api._task_api_request(
        "GET",
        "/user",
        USER_ID,
        API_KEY,
        expected_data_type=dict,
        allow_redirects=True,
    )

    assert result.error.kind is api.HabiticaErrorKind.HTTP_ERROR
    assert calls[0][1]["allow_redirects"] is False


def test_typed_404_is_classified_without_unknown_mutation_outcome(monkeypatch):
    install_response(
        monkeypatch,
        FakeResponse(
            status_code=404,
            payload={"success": False, "error": "NotFound", "message": "secret"},
        ),
    )

    result = api.delete_task_result(USER_ID, API_KEY, "missing-task")

    assert result.error == api.HabiticaAPIError(
        kind=api.HabiticaErrorKind.NOT_FOUND,
        status=404,
    )


@pytest.mark.parametrize("header_name", ["Retry-After", "retry-after"])
def test_typed_429_preserves_numeric_retry_after(monkeypatch, header_name):
    install_response(
        monkeypatch,
        FakeResponse(
            status_code=429,
            headers={header_name: "12.5"},
            payload={
                "success": False,
                "error": "TooManyRequests",
                "message": "secret",
            },
        ),
    )

    result = api.get_tasks_result(USER_ID, API_KEY, "habits")

    assert result.error == api.HabiticaAPIError(
        kind=api.HabiticaErrorKind.RATE_LIMITED,
        status=429,
        retry_after=12.5,
    )


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("nan", None),
        ("inf", None),
        ("-4", 0.0),
        ("999999999", api.MAX_RETRY_AFTER_SECONDS),
        ("invalid", None),
    ],
)
def test_typed_retry_after_is_finite_and_bounded(monkeypatch, raw_value, expected):
    install_response(
        monkeypatch,
        FakeResponse(
            status_code=429,
            headers={"Retry-After": raw_value},
            payload={"success": False, "error": "TooManyRequests"},
        ),
    )

    result = api.get_tasks_result(USER_ID, API_KEY, "habits")

    assert result.error.retry_after == expected


def test_typed_5xx_marks_mutation_unknown_but_read_known(monkeypatch):
    response = FakeResponse(
        status_code=503,
        payload={"success": False, "error": "InternalServerError"},
    )
    calls = install_response(monkeypatch, response)

    mutation = api.create_task_result(USER_ID, API_KEY, {"type": "todo", "text": "x"})
    read = api.get_task_result(USER_ID, API_KEY, "task-id")

    assert mutation.error.kind is api.HabiticaErrorKind.UPSTREAM_ERROR
    assert mutation.error.status == 503
    assert mutation.error.outcome_unknown is True
    assert read.error.kind is api.HabiticaErrorKind.UPSTREAM_ERROR
    assert read.error.outcome_unknown is False
    assert len(calls) == 2


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(json_error=ValueError("raw-response-secret")),
        FakeResponse(payload=[]),
        FakeResponse(payload={"success": False, "data": {}}),
        FakeResponse(payload={"success": "true", "data": {}}),
        FakeResponse(payload={"success": True}),
        FakeResponse(payload={"success": True, "data": []}),
    ],
)
def test_typed_success_rejects_invalid_json_envelope_or_shape(monkeypatch, response):
    install_response(monkeypatch, response)

    result = api.get_task_result(USER_ID, API_KEY, "task-id")

    assert result.error.kind is api.HabiticaErrorKind.INVALID_RESPONSE
    assert result.error.status == 200
    assert result.error.outcome_unknown is False


def test_typed_tag_helpers_use_official_routes_and_never_retry(monkeypatch):
    tag = {"id": "123e4567-e89b-42d3-a456-426614174010", "name": "list:Work"}
    responses = iter(
        [
            FakeResponse(payload={"success": True, "data": [tag]}),
            FakeResponse(status_code=201, payload={"success": True, "data": tag}),
            FakeResponse(payload={"success": True, "data": tag}),
            FakeResponse(payload={"success": True, "data": {}}),
        ]
    )
    calls = []

    def request(*args, **kwargs):
        calls.append((args, kwargs))
        return next(responses)

    monkeypatch.setattr(api.requests, "request", request)

    assert api.get_tags_result(USER_ID, API_KEY).data == [tag]
    assert api.create_tag_result(USER_ID, API_KEY, "list:Work").data == tag
    assert api.update_tag_result(USER_ID, API_KEY, tag["id"], "list:Career").data == tag
    assert api.delete_tag_result(USER_ID, API_KEY, tag["id"]).data == {}
    assert [call[0] for call in calls] == [
        ("GET", f"{api.BASE_URL}/tags"),
        ("POST", f"{api.BASE_URL}/tags"),
        ("PUT", f"{api.BASE_URL}/tags/{tag['id']}"),
        ("DELETE", f"{api.BASE_URL}/tags/{tag['id']}"),
    ]
    assert calls[1][1]["json"] == {"name": "list:Work"}
    assert calls[2][1]["json"] == {"name": "list:Career"}


def test_typed_invalid_success_response_marks_mutation_outcome_unknown(monkeypatch):
    install_response(
        monkeypatch,
        FakeResponse(payload={"success": True, "data": []}),
    )

    result = api.update_task_result(USER_ID, API_KEY, "task-id", {"text": "x"})

    assert result.error.kind is api.HabiticaErrorKind.INVALID_RESPONSE
    assert result.error.status == 200
    assert result.error.outcome_unknown is True


@pytest.mark.parametrize(
    ("error", "expected_kind"),
    [
        (requests.exceptions.Timeout, api.HabiticaErrorKind.REQUEST_TIMEOUT),
        (requests.exceptions.ConnectionError, api.HabiticaErrorKind.NETWORK_ERROR),
    ],
)
def test_typed_network_failure_is_once_unknown_and_logs_no_secrets(
    monkeypatch,
    caplog,
    error,
    expected_kind,
):
    calls = []

    def fail_request(*args, **kwargs):
        calls.append((args, kwargs))
        raise error(f"{USER_ID} {API_KEY} raw-response-secret")

    monkeypatch.setattr(api.requests, "request", fail_request)
    caplog.set_level(logging.WARNING, logger=api.__name__)

    result = api.score_checklist_item_result(
        USER_ID,
        API_KEY,
        "task-id",
        "item-id",
    )

    assert len(calls) == 1
    assert result.error.kind is expected_kind
    assert result.error.outcome_unknown is True
    assert USER_ID not in caplog.text
    assert API_KEY not in caplog.text
    assert "task-id" not in caplog.text
    assert "item-id" not in caplog.text
    assert "raw-response-secret" not in caplog.text


def test_typed_http_failure_logs_only_status_and_classification(monkeypatch, caplog):
    install_response(
        monkeypatch,
        FakeResponse(
            status_code=400,
            payload={
                "success": False,
                "error": "BadRequest",
                "message": f"{USER_ID} {API_KEY} raw-response-secret",
                "errors": [{"value": "task-body-secret"}],
            },
        ),
    )
    caplog.set_level(logging.WARNING, logger=api.__name__)

    result = api.create_task_result(
        USER_ID,
        API_KEY,
        {"type": "todo", "text": "task-body-secret"},
    )

    assert result.error.kind is api.HabiticaErrorKind.BAD_REQUEST
    assert "status=400" in caplog.text
    assert "kind=bad_request" in caplog.text
    assert USER_ID not in caplog.text
    assert API_KEY not in caplog.text
    assert "raw-response-secret" not in caplog.text
    assert "task-body-secret" not in caplog.text


def test_typed_full_user_uses_user_endpoint(monkeypatch):
    user = {"needsCron": True, "stats": {"hp": 12}}
    calls = install_response(
        monkeypatch,
        FakeResponse(payload={"success": True, "data": user}),
    )

    result = api.get_user_result(USER_ID, API_KEY)

    assert result == api.HabiticaResult(data=user, status=200)
    assert calls[0][0] == ("GET", f"{api.BASE_URL}/user")


def test_typed_content_uses_supported_language(monkeypatch):
    content = {"potion": {"text": "Health Potion", "value": 25}}
    calls = install_response(
        monkeypatch,
        FakeResponse(payload={"success": True, "data": content}),
    )

    result = api.get_content_result(USER_ID, API_KEY, language="en")

    assert result.data == content
    assert calls[0][0] == ("GET", f"{api.BASE_URL}/content")
    assert calls[0][1]["params"] == {"language": "en"}


def test_typed_content_rejects_untrusted_language_without_request():
    result = api.get_content_result(USER_ID, API_KEY, language="bad\nheader")

    assert result.error.kind is api.HabiticaErrorKind.INVALID_INPUT


@pytest.mark.parametrize(
    ("operation", "path"),
    [
        (api.buy_health_potion_result, "/user/buy-health-potion"),
        (api.run_cron_result, "/cron"),
    ],
)
def test_typed_gameplay_mutations_are_one_shot_and_return_data(
    monkeypatch,
    operation,
    path,
):
    data = {"stats": {"hp": 27}}
    calls = install_response(
        monkeypatch,
        FakeResponse(payload={"success": True, "data": data}),
    )

    result = operation(USER_ID, API_KEY)

    assert result == api.HabiticaResult(data=data, status=200)
    assert calls[0][0] == ("POST", f"{api.BASE_URL}{path}")
    assert len(calls) == 1


@pytest.mark.parametrize(
    "operation",
    [api.buy_health_potion_result, api.run_cron_result],
)
def test_typed_gameplay_timeout_is_unknown_and_never_retried(monkeypatch, operation):
    calls = []

    def timeout(*args, **kwargs):
        calls.append((args, kwargs))
        raise requests.exceptions.Timeout("private response")

    monkeypatch.setattr(api.requests, "request", timeout)

    result = operation(USER_ID, API_KEY)

    assert result.error.kind is api.HabiticaErrorKind.REQUEST_TIMEOUT
    assert result.error.outcome_unknown is True
    assert len(calls) == 1
