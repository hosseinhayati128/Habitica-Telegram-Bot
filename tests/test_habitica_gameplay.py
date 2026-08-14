from datetime import date, datetime, timezone

import pytest

import Habitica_API as api
import habitica_gameplay as gameplay


USER_ID = "fake-user"
API_KEY = "fake-key"
NOW = datetime(2026, 8, 14, 12, tzinfo=timezone.utc)


def ok(data):
    return api.HabiticaResult(data=data, status=200)


def failed(
    kind=api.HabiticaErrorKind.NETWORK_ERROR,
    *,
    outcome_unknown=False,
    retry_after=None,
):
    return api.HabiticaResult(
        error=api.HabiticaAPIError(
            kind=kind,
            outcome_unknown=outcome_unknown,
            retry_after=retry_after,
        )
    )


def make_user(
    *,
    needs_cron=True,
    last_cron="2026-08-13T12:00:00Z",
    day_start=0,
    timezone_offset=0,
    timezone_offset_at_last_cron=None,
    hp=20,
    max_health=50,
    gold=100,
):
    preferences = {
        "dayStart": day_start,
        "timezoneOffset": timezone_offset,
    }
    if timezone_offset_at_last_cron is not None:
        preferences["timezoneOffsetAtLastCron"] = timezone_offset_at_last_cron
    return {
        "needsCron": needs_cron,
        "lastCron": last_cron,
        "preferences": preferences,
        "auth": {"timestamps": {"loggedIn": last_cron}},
        "stats": {
            "lvl": 113,
            "class": "warrior",
            "hp": hp,
            "maxHealth": max_health,
            "exp": 440,
            "toNextLevel": 4460,
            "mp": 229,
            "maxMP": 230,
            "gp": gold,
        },
    }


def make_ready_user(**kwargs):
    return make_user(needs_cron=False, **kwargs)


def make_daily(
    task_id="daily-1",
    *,
    completed=False,
    yester_daily=True,
    text="Exercise",
):
    return {
        "id": task_id,
        "type": "daily",
        "text": text,
        "notes": "For every 15 min",
        "priority": 1,
        "completed": completed,
        "yesterDaily": yester_daily,
        "frequency": "daily",
        "everyX": 1,
        "startDate": "2020-01-01",
        "checklist": [],
    }


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 8, 14, 3, 59, tzinfo=timezone.utc), 0),
        (datetime(2026, 8, 14, 4, 0, tzinfo=timezone.utc), 1),
        (datetime(2026, 8, 14, 8, 0, tzinfo=timezone.utc), 1),
    ],
)
def test_days_missed_respects_custom_day_start(now, expected):
    user = make_user(
        last_cron="2026-08-13T05:00:00Z",
        day_start=4,
    )

    assert gameplay.compute_days_missed(user, now=now) == expected


@pytest.mark.parametrize(
    ("timezone_offset", "last_cron", "now", "expected"),
    [
        (-330, "2026-08-12T23:00:00Z", "2026-08-13T23:00:00Z", 1),
        (300, "2026-08-13T06:00:00Z", "2026-08-14T06:00:00Z", 1),
    ],
)
def test_days_missed_supports_positive_and_negative_utc_offsets(
    timezone_offset,
    last_cron,
    now,
    expected,
):
    user = make_user(
        last_cron=last_cron,
        timezone_offset=timezone_offset,
    )

    assert gameplay.compute_days_missed(
        user,
        now=datetime.fromisoformat(now.replace("Z", "+00:00")),
    ) == expected


def test_days_missed_uses_earlier_logged_in_timestamp():
    user = make_user(last_cron="2026-08-14T10:00:00Z")
    user["auth"]["timestamps"]["loggedIn"] = "2026-08-12T10:00:00Z"

    assert gameplay.compute_days_missed(user, now=NOW) == 2


def test_timezone_change_in_unsafe_direction_does_not_double_cron():
    user = make_user(
        last_cron="2026-08-14T03:30:00Z",
        day_start=4,
        timezone_offset=0,
        timezone_offset_at_last_cron=-60,
    )
    now = datetime(2026, 8, 14, 4, 15, tzinfo=timezone.utc)

    assert gameplay.compute_days_missed(user, now=now) == 0


def test_timezone_change_in_safe_dst_direction_uses_new_zone():
    user = make_user(
        last_cron="2026-08-13T02:30:00Z",
        day_start=4,
        timezone_offset=-120,
        timezone_offset_at_last_cron=-60,
    )
    now = datetime(2026, 8, 14, 2, 30, tzinfo=timezone.utc)

    assert gameplay.compute_days_missed(user, now=now) == 1


@pytest.mark.parametrize(
    ("value", "expected"),
    [(True, True), (False, False), (1, False), ("true", False), (None, False)],
)
def test_day_refresh_required_is_strict(value, expected):
    assert gameplay.day_refresh_required({"needsCron": value}) is expected


def test_should_do_daily_every_x_and_future_start():
    task = {
        "type": "daily",
        "frequency": "daily",
        "everyX": 2,
        "startDate": "2026-08-10",
    }

    assert gameplay.should_do(date(2026, 8, 12), task) is True
    assert gameplay.should_do(date(2026, 8, 13), task) is False
    assert gameplay.should_do(date(2026, 8, 9), task) is False


def test_should_do_treats_calendar_start_date_as_literal():
    task = {
        "type": "daily",
        "frequency": "weekly",
        "everyX": 1,
        "startDate": date(2026, 8, 14),
        "repeat": {"f": True},
    }

    assert gameplay.should_do(
        date(2026, 8, 14),
        task,
        preferences={"timezoneOffset": 720, "dayStart": 23},
    ) is True


def test_should_do_weekly_repeat_and_every_x():
    task = {
        "type": "daily",
        "frequency": "weekly",
        "everyX": 2,
        "startDate": "2026-08-03",
        "repeat": {"m": True, "w": True},
    }

    assert gameplay.should_do(date(2026, 8, 17), task) is True
    assert gameplay.should_do(date(2026, 8, 18), task) is False
    assert gameplay.should_do(date(2026, 8, 10), task) is False


def test_should_do_monthly_day_and_week_schedules():
    by_day = {
        "type": "daily",
        "frequency": "monthly",
        "everyX": 2,
        "startDate": "2026-06-01",
        "daysOfMonth": [14],
    }
    by_week = {
        "type": "daily",
        "frequency": "monthly",
        "everyX": 1,
        "startDate": "2026-06-01",
        "repeat": {"f": True},
        "weeksOfMonth": [2],
    }

    assert gameplay.should_do(date(2026, 8, 14), by_day) is True
    assert gameplay.should_do(date(2026, 7, 14), by_day) is False
    assert gameplay.should_do(date(2026, 8, 14), by_week) is True
    assert gameplay.should_do(date(2026, 8, 21), by_week) is False


def test_should_do_yearly_schedule():
    task = {
        "type": "daily",
        "frequency": "yearly",
        "everyX": 2,
        "startDate": "2024-08-14",
    }

    assert gameplay.should_do(date(2026, 8, 14), task) is True
    assert gameplay.should_do(date(2025, 8, 14), task) is False


def test_eligible_review_dailies_match_official_group_filter_and_whitelist():
    eligible = make_daily()
    eligible["secretHistory"] = [{"secret": "never expose"}]
    eligible["checklist"] = [
        {"id": "item-1", "text": "Warm up", "completed": True, "secret": "x"}
    ]
    group = make_daily("group")
    group["group"] = {"id": "party"}
    challenge = make_daily("challenge")
    challenge["challenge"] = {"id": "challenge"}
    broken_group = make_daily("broken-group")
    broken_group["group"] = {"broken": "TASK_DELETED"}

    result = gameplay.eligible_review_dailies(
        [
            eligible,
            make_daily("done", completed=True),
            make_daily("not-yesterday", yester_daily=False),
            group,
            challenge,
            broken_group,
        ],
        user=make_user(),
        now=NOW,
    )

    assert [daily.id for daily in result] == [
        "daily-1",
        "challenge",
        "broken-group",
    ]
    payload = result[0].to_payload()
    assert payload == {
        "id": "daily-1",
        "text": "Exercise",
        "notes": "For every 15 min",
        "priority": 1,
        "checklist": [
            {"id": "item-1", "text": "Warm up", "completed": True}
        ],
        "completed": False,
    }
    assert "secretHistory" not in payload


def test_review_excludes_yesterdaily_that_was_not_due_on_review_date():
    due = make_daily("due")
    due.update(
        {
            "frequency": "weekly",
            "everyX": 1,
            "startDate": "2026-08-01",
            "repeat": {"th": True},
        }
    )
    not_due = make_daily("not-due")
    not_due.update(
        {
            "frequency": "weekly",
            "everyX": 1,
            "startDate": "2026-08-01",
            "repeat": {"f": True},
        }
    )

    result = gameplay.eligible_review_dailies(
        [due, not_due],
        user=make_user(),
        now=NOW,
    )

    # 2026-08-13 is Thursday in the user's stored offset.
    assert [daily.id for daily in result] == ["due"]


def test_review_uses_last_cron_offset_for_reviewed_day_schedule():
    daily = make_daily("dst-boundary")
    daily["startDate"] = "2026-08-13T22:30:00Z"
    user = make_user(
        timezone_offset=-120,
        timezone_offset_at_last_cron=-60,
    )

    result = gameplay.eligible_review_dailies([daily], user=user, now=NOW)
    current_offset_only = gameplay.eligible_review_dailies(
        [daily],
        user=make_user(timezone_offset=-120),
        now=NOW,
    )

    # The current UTC+2 offset would interpret this hidden start time as
    # August 14. The server-owned UTC+1 offset at the last cron correctly
    # keeps it on the August 13 day being reviewed.
    assert [item.id for item in result] == ["dst-boundary"]
    assert current_offset_only == ()


@pytest.mark.parametrize("malformed_offset", [-60.5, -9999, "-60"])
def test_review_ignores_malformed_last_cron_offset(malformed_offset):
    daily = make_daily("malformed-offset")
    daily["startDate"] = "2026-08-13T22:30:00Z"
    user = make_user(timezone_offset=-120)
    user["preferences"]["timezoneOffsetAtLastCron"] = malformed_offset

    result = gameplay.eligible_review_dailies([daily], user=user, now=NOW)

    assert result == ()


def test_calendar_date_should_do_does_not_shift_for_extreme_offset_and_day_start():
    task = {
        "type": "daily",
        "frequency": "weekly",
        "everyX": 1,
        "startDate": "2026-08-14",
        "repeat": {"f": True},
    }

    assert gameplay.should_do(
        date(2026, 8, 14),
        task,
        preferences={"timezoneOffset": -840, "dayStart": 23},
    ) is True


def test_evaluate_day_status_handles_no_tasks_and_multiple_days():
    user = make_user(last_cron="2026-08-10T12:00:00Z")

    result = gameplay.evaluate_day_status(user, [], now=NOW)

    assert result.ok is True
    payload = result.data.to_payload()
    assert payload["profile"]["stats"]["hp"] == 20
    payload.pop("profile")
    assert payload == {
        "refreshRequired": True,
        "daysMissed": 4,
        "reviewLabel": "4 missed days",
        "dailies": [],
    }


def test_fetch_day_status_skips_dailies_when_refresh_not_required():
    calls = []

    def fetch_dailies(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("must not fetch current tasks")

    result = gameplay.fetch_day_status(
        USER_ID,
        API_KEY,
        fetch_user=lambda *_: ok(make_user(needs_cron=False)),
        fetch_dailies=fetch_dailies,
    )

    payload = result.data.to_payload()
    assert payload["refreshRequired"] is False
    assert payload["profile"]["profile"] == {
        "level": 113,
        "class": "warrior",
    }
    assert calls == []


def test_fetch_day_status_only_treats_explicit_invalid_credentials_as_auth():
    ambiguous_unauthorized = gameplay.fetch_day_status(
        USER_ID,
        API_KEY,
        fetch_user=lambda *_: failed(api.HabiticaErrorKind.UNAUTHORIZED),
    )
    invalid_credentials = gameplay.fetch_day_status(
        USER_ID,
        API_KEY,
        fetch_user=lambda *_: failed(api.HabiticaErrorKind.INVALID_CREDENTIALS),
    )
    limited = gameplay.fetch_day_status(
        USER_ID,
        API_KEY,
        fetch_user=lambda *_: failed(api.HabiticaErrorKind.RATE_LIMITED),
    )
    timed_out = gameplay.fetch_day_status(
        USER_ID,
        API_KEY,
        fetch_user=lambda *_: failed(api.HabiticaErrorKind.REQUEST_TIMEOUT),
    )
    network_error = gameplay.fetch_day_status(
        USER_ID,
        API_KEY,
        fetch_user=lambda *_: failed(api.HabiticaErrorKind.NETWORK_ERROR),
    )

    assert ambiguous_unauthorized.error.code == "habitica_unavailable"
    assert invalid_credentials.error.code == "habitica_unauthorized"
    assert limited.error.code == "rate_limited"
    assert timed_out.error.code == "request_timeout"
    assert network_error.error.code == "habitica_unavailable"


def potion_content():
    return {
        "potion": {
            "text": "Health Potion",
            "notes": "Localized text is not parsed",
            "value": 25,
        }
    }


def test_health_potion_status_uses_content_price_without_hardcoded_healing():
    result = gameplay.get_health_potion_status(
        USER_ID,
        API_KEY,
        fetch_user=lambda *_: ok(make_ready_user(hp=20, gold=30)),
        fetch_content=lambda *args, **kwargs: ok(potion_content()),
    )

    assert result.data.to_payload() == {
        "potion": {"name": "Health Potion", "price": 25, "healing": None},
        "stats": {"hp": 20, "maxHp": 50, "gold": 30},
        "healthFull": False,
        "canAfford": True,
    }


def test_health_potion_status_blocks_unresolved_day_before_content_fetch():
    content_calls = []

    result = gameplay.get_health_potion_status(
        USER_ID,
        API_KEY,
        fetch_user=lambda *_: ok(make_user(needs_cron=True)),
        fetch_content=lambda *args, **kwargs: content_calls.append((args, kwargs)),
    )

    assert result.error.code == "day_refresh_required"
    assert content_calls == []


@pytest.mark.parametrize(
    "stats_update",
    [
        {"hp": None},
        {"maxHealth": None},
        {"maxHealth": 0},
        {"gp": "100"},
    ],
)
def test_health_potion_status_rejects_missing_or_malformed_stats(stats_update):
    user = make_ready_user()
    user["stats"].update(stats_update)
    content_calls = []

    result = gameplay.get_health_potion_status(
        USER_ID,
        API_KEY,
        fetch_user=lambda *_: ok(user),
        fetch_content=lambda *args, **kwargs: content_calls.append((args, kwargs)),
    )

    assert result.error.code == "invalid_response"
    assert content_calls == []


def test_health_potion_purchase_rejects_full_health_and_insufficient_gold():
    calls = []

    def buy(*args):
        calls.append(args)
        return ok({})

    full = gameplay.purchase_health_potion(
        USER_ID,
        API_KEY,
        fetch_user=lambda *_: ok(make_ready_user(hp=50)),
        fetch_content=lambda *args, **kwargs: ok(potion_content()),
        buy_potion=buy,
    )
    poor = gameplay.purchase_health_potion(
        USER_ID,
        API_KEY,
        fetch_user=lambda *_: ok(make_ready_user(hp=10, gold=24)),
        fetch_content=lambda *args, **kwargs: ok(potion_content()),
        buy_potion=buy,
    )

    assert full.error.code == "health_already_full"
    assert poor.error.code == "not_enough_gold"
    assert calls == []


def test_health_potion_purchase_runs_once_and_returns_authoritative_snapshot():
    users = iter(
        [make_ready_user(hp=20, gold=100), make_ready_user(hp=35, gold=75)]
    )
    purchases = []

    def buy(*args):
        purchases.append(args)
        return ok({"hp": 35, "gp": 75})

    result = gameplay.purchase_health_potion(
        USER_ID,
        API_KEY,
        fetch_user=lambda *_: ok(next(users)),
        fetch_content=lambda *args, **kwargs: ok(potion_content()),
        buy_potion=buy,
    )

    assert result.ok is True
    assert result.data.profile.hp == 35
    assert result.data.profile.gold == 75
    assert result.data.to_payload()["syncRequired"] is False
    assert purchases == [(USER_ID, API_KEY)]


def test_unknown_potion_outcome_is_reconciled_without_retry():
    users = iter(
        [make_ready_user(hp=20, gold=100), make_ready_user(hp=35, gold=75)]
    )
    purchases = []

    def buy(*args):
        purchases.append(args)
        return failed(api.HabiticaErrorKind.REQUEST_TIMEOUT, outcome_unknown=True)

    result = gameplay.purchase_health_potion(
        USER_ID,
        API_KEY,
        fetch_user=lambda *_: ok(next(users)),
        fetch_content=lambda *args, **kwargs: ok(potion_content()),
        buy_potion=buy,
    )

    assert result.ok is True
    assert result.data.profile.hp == 35
    assert len(purchases) == 1


@pytest.mark.parametrize(
    "after",
    [
        make_ready_user(hp=35, gold=100),
        make_ready_user(hp=20, gold=75),
        make_ready_user(hp=20, gold=100),
    ],
)
def test_unknown_potion_outcome_stays_unknown_without_definitive_purchase(after):
    users = iter([make_ready_user(hp=20, gold=100), after])
    purchases = []

    def buy(*args):
        purchases.append(args)
        return failed(api.HabiticaErrorKind.REQUEST_TIMEOUT, outcome_unknown=True)

    result = gameplay.purchase_health_potion(
        USER_ID,
        API_KEY,
        fetch_user=lambda *_: ok(next(users)),
        fetch_content=lambda *args, **kwargs: ok(potion_content()),
        buy_potion=buy,
    )

    assert result.error.code == "request_timeout"
    assert result.error.outcome_unknown is True
    assert len(purchases) == 1


def test_confirmed_purchase_with_unusable_stats_is_success_requiring_sync():
    users = iter(
        [
            ok(make_ready_user(hp=20, gold=100)),
            failed(api.HabiticaErrorKind.NETWORK_ERROR),
        ]
    )
    purchases = []

    def buy(*args):
        purchases.append(args)
        return ok({})

    result = gameplay.purchase_health_potion(
        USER_ID,
        API_KEY,
        fetch_user=lambda *_: next(users),
        fetch_content=lambda *args, **kwargs: ok(potion_content()),
        buy_potion=buy,
    )

    assert result.ok is True
    assert result.error is None
    assert result.data.profile is None
    assert result.data.sync_required is True
    assert result.data.to_payload() == {
        "profile": None,
        "potion": {"name": "Health Potion", "price": 25, "healing": None},
        "syncRequired": True,
    }
    assert len(purchases) == 1


def test_purchase_blocks_if_day_becomes_due_before_notauthorized_reconcile():
    users = iter(
        [make_ready_user(hp=20, gold=100), make_user(needs_cron=True)]
    )

    result = gameplay.purchase_health_potion(
        USER_ID,
        API_KEY,
        fetch_user=lambda *_: ok(next(users)),
        fetch_content=lambda *args, **kwargs: ok(potion_content()),
        buy_potion=lambda *_: failed(api.HabiticaErrorKind.UNAUTHORIZED),
    )

    assert result.error.code == "day_refresh_required"


@pytest.mark.parametrize(
    ("reconciled_user", "expected_code"),
    [
        (make_ready_user(hp=50, gold=100), "health_already_full"),
        (make_ready_user(hp=20, gold=20), "not_enough_gold"),
        (make_ready_user(hp=20, gold=100), "purchase_failed"),
    ],
)
def test_potion_notauthorized_is_reconciled_as_gameplay_failure(
    reconciled_user,
    expected_code,
):
    users = iter([make_ready_user(hp=20, gold=100), reconciled_user])

    result = gameplay.purchase_health_potion(
        USER_ID,
        API_KEY,
        fetch_user=lambda *_: ok(next(users)),
        fetch_content=lambda *args, **kwargs: ok(potion_content()),
        buy_potion=lambda *_: failed(api.HabiticaErrorKind.UNAUTHORIZED),
    )

    assert result.error.code == expected_code


@pytest.mark.parametrize(
    ("kind", "expected_code"),
    [
        (api.HabiticaErrorKind.INVALID_CREDENTIALS, "habitica_unauthorized"),
        (api.HabiticaErrorKind.RATE_LIMITED, "rate_limited"),
        (api.HabiticaErrorKind.REQUEST_TIMEOUT, "request_timeout"),
        (api.HabiticaErrorKind.INVALID_RESPONSE, "invalid_response"),
    ],
)
def test_potion_notauthorized_preserves_reconciliation_read_failure(
    kind,
    expected_code,
):
    users = iter([ok(make_ready_user(hp=20, gold=100)), failed(kind)])

    result = gameplay.purchase_health_potion(
        USER_ID,
        API_KEY,
        fetch_user=lambda *_: next(users),
        fetch_content=lambda *args, **kwargs: ok(potion_content()),
        buy_potion=lambda *_: failed(api.HabiticaErrorKind.UNAUTHORIZED),
    )

    assert result.error.code == expected_code


def test_potion_invalid_credentials_still_requires_relink_without_reconciliation():
    calls = 0

    def fetch_user(*_):
        nonlocal calls
        calls += 1
        return ok(make_ready_user())

    result = gameplay.purchase_health_potion(
        USER_ID,
        API_KEY,
        fetch_user=fetch_user,
        fetch_content=lambda *args, **kwargs: ok(potion_content()),
        buy_potion=lambda *_: failed(api.HabiticaErrorKind.INVALID_CREDENTIALS),
    )

    assert result.error.code == "habitica_unauthorized"
    assert calls == 1


class RefreshGateway:
    def __init__(self, *, selected=(), fail_score=None, cron_result=None):
        self.selected = tuple(selected)
        self.fail_score = fail_score
        self.cron_result = cron_result or ok({})
        self.user_results = {}
        self.dailies = [make_daily(task_id) for task_id in self.selected]
        self.score_calls = []
        self.user_calls = 0
        self.daily_calls = 0
        self.task_calls = 0
        self.cron_calls = 0

    def fetch_user(self, *_):
        call_index = self.user_calls
        self.user_calls += 1
        if call_index in self.user_results:
            return self.user_results[call_index]
        if self.cron_calls:
            return ok(make_user(needs_cron=False, hp=18))
        return ok(make_user(needs_cron=True))

    def fetch_dailies(self, *args, **kwargs):
        self.daily_calls += 1
        return ok(self.dailies)

    def fetch_task(self, *args):
        self.task_calls += 1
        task_id = args[2]
        task = next(task for task in self.dailies if task["id"] == task_id)
        return ok(task)

    def score(self, *args):
        task_id = args[2]
        self.score_calls.append(task_id)
        if task_id == self.fail_score:
            return failed(api.HabiticaErrorKind.UPSTREAM_ERROR)
        for task in self.dailies:
            if task["id"] == task_id:
                task["completed"] = True
        return ok({"hp": 20})

    def cron(self, *args):
        self.cron_calls += 1
        return self.cron_result


def run_refresh(gateway, selected):
    return gameplay.refresh_day(
        USER_ID,
        API_KEY,
        selected,
        fetch_user=gateway.fetch_user,
        fetch_dailies=gateway.fetch_dailies,
        fetch_task=gateway.fetch_task,
        score_daily=gateway.score,
        run_cron=gateway.cron,
    )


def test_refresh_day_large_selection_pauses_safely_before_cron():
    selected = tuple(
        f"daily-{index}" for index in range(gameplay.MAX_DAILY_BATCH_SIZE + 2)
    )
    gateway = RefreshGateway(selected=selected)

    result = run_refresh(gateway, selected)

    total_calls = (
        gateway.user_calls
        + gateway.daily_calls
        + gateway.task_calls
        + len(gateway.score_calls)
        + gateway.cron_calls
    )
    assert gameplay.MAX_DAILY_BATCH_SIZE == 8
    assert result.ok is False
    assert result.error.code == "batch_incomplete"
    assert result.error.retry_after == 60
    assert result.data.status == "batch_incomplete"
    assert result.data.scored_daily_ids == selected[:8]
    assert result.data.unresolved_daily_ids == selected[8:]
    assert gateway.score_calls == list(selected[:8])
    assert gateway.cron_calls == 0
    assert total_calls == 20
    assert total_calls < 30


def test_refresh_day_large_selection_continues_explicitly_then_runs_cron():
    selected = tuple(
        f"daily-{index}" for index in range(gameplay.MAX_DAILY_BATCH_SIZE + 2)
    )
    gateway = RefreshGateway(selected=selected)

    first = run_refresh(gateway, selected)
    remaining = first.data.unresolved_daily_ids
    second = run_refresh(gateway, remaining)

    assert first.error.code == "batch_incomplete"
    assert second.ok is True
    assert second.data.status == "refreshed"
    assert second.data.scored_daily_ids == remaining
    assert gateway.score_calls == list(selected)
    assert gateway.cron_calls == 1


def test_refresh_day_selecting_none_runs_cron_after_rechecks():
    gateway = RefreshGateway()

    result = run_refresh(gateway, [])

    assert result.ok is True
    assert result.data.status == "refreshed"
    assert gateway.score_calls == []
    assert gateway.cron_calls == 1


def test_refresh_day_scores_selected_dailies_then_cron():
    gateway = RefreshGateway(selected=("one", "two"))

    result = run_refresh(gateway, ["one", "two"])

    assert result.ok is True
    assert result.data.scored_daily_ids == ("one", "two")
    assert gateway.score_calls == ["one", "two"]
    assert gateway.cron_calls == 1


@pytest.mark.parametrize("selected", [["arbitrary"], ["one", "one"]])
def test_refresh_day_rejects_arbitrary_or_duplicate_selection(selected):
    gateway = RefreshGateway(selected=("one",))

    result = run_refresh(gateway, selected)

    assert result.error.code == "invalid_daily_selection"
    assert gateway.score_calls == []
    assert gateway.cron_calls == 0


def test_refresh_day_does_not_score_daily_completed_by_another_client():
    gateway = RefreshGateway(selected=("one",))
    fetch_count = 0

    def fetch_dailies(*args, **kwargs):
        nonlocal fetch_count
        fetch_count += 1
        if fetch_count == 2:
            gateway.dailies[0]["completed"] = True
        return ok(gateway.dailies)

    gateway.fetch_dailies = fetch_dailies

    result = run_refresh(gateway, ["one"])

    assert result.ok is True
    assert gateway.score_calls == []
    assert gateway.cron_calls == 1


def test_refresh_day_retry_accepts_already_completed_due_selected_daily():
    gateway = RefreshGateway(selected=("one",))
    gateway.dailies[0]["completed"] = True

    result = run_refresh(gateway, ["one"])

    assert result.ok is True
    assert result.data.status == "refreshed"
    assert result.data.scored_daily_ids == ()
    assert gateway.score_calls == []
    assert gateway.cron_calls == 1


@pytest.mark.parametrize(
    "invalid_fields",
    [
        {"yesterDaily": False},
        {"startDate": "2999-01-01"},
        {"group": {"id": "party-id"}},
    ],
)
def test_refresh_day_retry_rejects_completed_non_review_daily(invalid_fields):
    gateway = RefreshGateway(selected=("one",))
    gateway.dailies[0].update({"completed": True, **invalid_fields})

    result = run_refresh(gateway, ["one"])

    assert result.error.code == "invalid_daily_selection"
    assert gateway.score_calls == []
    assert gateway.cron_calls == 0


def test_externally_completed_daily_is_not_reintroduced_after_later_failure():
    gateway = RefreshGateway(selected=("one", "two"), fail_score="two")
    fetch_count = 0

    def fetch_dailies(*args, **kwargs):
        nonlocal fetch_count
        fetch_count += 1
        if fetch_count == 2:
            gateway.dailies[0]["completed"] = True
        return ok(gateway.dailies)

    gateway.fetch_dailies = fetch_dailies

    result = run_refresh(gateway, ["one", "two"])

    assert result.error.code == "daily_score_failed"
    assert result.data.scored_daily_ids == ()
    assert result.data.unresolved_daily_ids == ("two",)
    assert gateway.cron_calls == 0


def test_daily_that_becomes_ineligible_is_not_scored_and_cron_can_run():
    gateway = RefreshGateway(selected=("one",))
    fetch_count = 0

    def fetch_dailies(*args, **kwargs):
        nonlocal fetch_count
        fetch_count += 1
        if fetch_count == 2:
            gateway.dailies[0]["group"] = {"id": "group-id"}
        return ok(gateway.dailies)

    gateway.fetch_dailies = fetch_dailies

    result = run_refresh(gateway, ["one"])

    assert result.ok is True
    assert result.data.scored_daily_ids == ()
    assert gateway.score_calls == []
    assert gateway.cron_calls == 1


def test_partial_daily_score_failure_never_runs_cron_and_reports_unresolved():
    gateway = RefreshGateway(selected=("one", "two"), fail_score="two")

    result = run_refresh(gateway, ["one", "two"])

    assert result.error.code == "daily_score_failed"
    assert result.data.status == "partial_failure"
    assert result.data.scored_daily_ids == ("one",)
    assert result.data.unresolved_daily_ids == ("two",)
    assert gateway.cron_calls == 0


def test_rate_limited_score_preserves_retry_and_safe_partial_state():
    gateway = RefreshGateway(selected=("one", "two"))

    def score(*args):
        task_id = args[2]
        gateway.score_calls.append(task_id)
        if task_id == "two":
            return failed(
                api.HabiticaErrorKind.RATE_LIMITED,
                retry_after=12.5,
            )
        gateway.dailies[0]["completed"] = True
        return ok({"hp": 20})

    gateway.score = score

    result = run_refresh(gateway, ["one", "two"])

    assert result.error.code == "rate_limited"
    assert result.error.retry_after == 12.5
    assert result.data.scored_daily_ids == ("one",)
    assert result.data.unresolved_daily_ids == ("two",)
    assert gateway.score_calls == ["one", "two"]
    assert gateway.cron_calls == 0


def test_unknown_daily_score_reconciles_completion_and_continues():
    gateway = RefreshGateway(selected=("one",))

    def score(*args):
        gateway.score_calls.append(args[2])
        gateway.dailies[0]["completed"] = True
        return failed(api.HabiticaErrorKind.REQUEST_TIMEOUT, outcome_unknown=True)

    gateway.score = score

    result = run_refresh(gateway, ["one"])

    assert result.ok is True
    assert result.data.scored_daily_ids == ("one",)
    assert gateway.cron_calls == 1


def test_externally_completed_daily_reconciles_rejected_score_without_retry():
    gateway = RefreshGateway(selected=("one", "two"))
    original_score = gateway.score

    def score(*args):
        task_id = args[2]
        if task_id != "two":
            return original_score(*args)
        gateway.score_calls.append(task_id)
        gateway.dailies[1]["completed"] = True
        return failed(api.HabiticaErrorKind.UNAUTHORIZED)

    gateway.score = score

    result = run_refresh(gateway, ["one", "two"])

    assert result.ok is True
    assert result.data.scored_daily_ids == ("one", "two")
    assert gateway.score_calls == ["one", "two"]
    assert gateway.task_calls == 1
    assert gateway.cron_calls == 1


def test_unknown_daily_score_stays_unresolved_when_completion_not_observed():
    gateway = RefreshGateway(selected=("one",))

    def score(*args):
        gateway.score_calls.append(args[2])
        return failed(api.HabiticaErrorKind.REQUEST_TIMEOUT, outcome_unknown=True)

    gateway.score = score

    result = run_refresh(gateway, ["one"])

    assert result.error.code == "request_timeout"
    assert result.error.outcome_unknown is True
    assert result.data.unresolved_daily_ids == ("one",)
    assert gateway.score_calls == ["one"]
    assert gateway.cron_calls == 0


def test_refresh_day_checks_needs_cron_immediately_before_each_score():
    gateway = RefreshGateway(selected=("one",))
    events = []
    original_fetch_user = gateway.fetch_user
    original_fetch_dailies = gateway.fetch_dailies
    original_score = gateway.score
    original_cron = gateway.cron

    def fetch_user(*args):
        events.append("user")
        return original_fetch_user(*args)

    def fetch_dailies(*args, **kwargs):
        events.append("dailies")
        return original_fetch_dailies(*args, **kwargs)

    def score(*args):
        events.append("score")
        return original_score(*args)

    def cron(*args):
        events.append("cron")
        return original_cron(*args)

    gateway.fetch_user = fetch_user
    gateway.fetch_dailies = fetch_dailies
    gateway.score = score
    gateway.cron = cron

    result = run_refresh(gateway, ["one"])

    assert result.ok is True
    score_index = events.index("score")
    assert events[score_index - 1 : score_index + 1] == ["user", "score"]


def test_refresh_day_detects_other_client_before_scoring():
    gateway = RefreshGateway(selected=("one",))
    gateway.user_results[2] = ok(make_user(needs_cron=False))

    result = run_refresh(gateway, ["one"])

    assert result.ok is True
    assert result.data.status == "already_refreshed"
    assert gateway.score_calls == []
    assert gateway.cron_calls == 0


def test_refresh_day_detects_other_client_before_cron():
    gateway = RefreshGateway(selected=("one",))
    gateway.user_results[3] = ok(make_user(needs_cron=False))

    result = run_refresh(gateway, ["one"])

    assert result.ok is True
    assert result.data.status == "already_refreshed"
    assert result.data.scored_daily_ids == ("one",)
    assert gateway.cron_calls == 0


def test_refresh_day_stops_before_next_score_when_other_client_runs_cron():
    gateway = RefreshGateway(selected=("one", "two"))
    gateway.user_results[3] = ok(make_user(needs_cron=False))

    result = run_refresh(gateway, ["one", "two"])

    assert result.ok is True
    assert result.data.status == "already_refreshed"
    assert result.data.scored_daily_ids == ("one",)
    assert gateway.score_calls == ["one"]
    assert gateway.cron_calls == 0


def test_refresh_day_gate_timeout_preserves_retry_safe_partial_state():
    gateway = RefreshGateway(selected=("one", "two"))
    gateway.user_results[3] = failed(api.HabiticaErrorKind.REQUEST_TIMEOUT)

    result = run_refresh(gateway, ["one", "two"])

    assert result.error.code == "request_timeout"
    assert result.data.status == "partial_failure"
    assert result.data.scored_daily_ids == ("one",)
    assert result.data.unresolved_daily_ids == ("two",)
    assert gateway.score_calls == ["one"]
    assert gateway.cron_calls == 0


def test_unknown_cron_outcome_reconciles_success_without_retry():
    gateway = RefreshGateway(
        cron_result=failed(
            api.HabiticaErrorKind.REQUEST_TIMEOUT,
            outcome_unknown=True,
        )
    )
    gateway.user_results[3] = ok(make_user(needs_cron=False))

    result = run_refresh(gateway, [])

    assert result.ok is True
    assert result.data.status == "refreshed"
    assert gateway.cron_calls == 1


def test_failed_cron_that_is_still_due_remains_blocking():
    gateway = RefreshGateway(
        cron_result=failed(
            api.HabiticaErrorKind.REQUEST_TIMEOUT,
            outcome_unknown=True,
        )
    )
    gateway.user_results[3] = ok(make_user(needs_cron=True))

    result = run_refresh(gateway, [])

    assert result.error.code == "request_timeout"
    assert result.error.outcome_unknown is True
    assert result.data.refresh_required is True
    assert gateway.cron_calls == 1


def test_confirmed_cron_resolves_gate_when_final_profile_read_fails():
    gateway = RefreshGateway()
    gateway.user_results[3] = failed(api.HabiticaErrorKind.NETWORK_ERROR)

    result = run_refresh(gateway, [])

    assert result.ok is True
    assert result.data.status == "refreshed"
    assert result.data.refresh_required is False
    assert result.data.profile is None
    assert gateway.cron_calls == 1
