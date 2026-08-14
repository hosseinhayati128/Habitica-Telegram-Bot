from __future__ import annotations

import pytest

from miniapp_tasks import (
    MAX_CHECKLIST_TEXT_LENGTH,
    MAX_NOTES_LENGTH,
    MAX_TITLE_LENGTH,
    TaskValidationError,
    checklist_changes,
    normalize_task,
    normalize_tasks,
    optimistic_scored_task,
    task_color_token,
    upstream_list_type,
    validate_task_payload,
)


TASK_ID = "123e4567-e89b-42d3-a456-426614174000"
ITEM_ID = "223e4567-e89b-42d3-a456-426614174001"
OTHER_ITEM_ID = "323e4567-e89b-42d3-a456-426614174002"


def test_list_type_translation_is_explicit():
    assert upstream_list_type("habit") == "habits"
    assert upstream_list_type("daily") == "dailys"
    assert upstream_list_type("todo") == "todos"
    assert upstream_list_type("todo", completed=True) == "completedTodos"
    with pytest.raises(TaskValidationError):
        upstream_list_type("daily", completed=True)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            {"type": "habit", "text": "Read", "up": True, "down": False},
            {
                "type": "habit",
                "text": "Read",
                "notes": "",
                "priority": 1.0,
                "up": True,
                "down": False,
            },
        ),
        (
            {"type": "daily", "text": "Walk", "repeatDays": ["m", "w", "f"]},
            {
                "type": "daily",
                "text": "Walk",
                "notes": "",
                "priority": 1.0,
                "frequency": "weekly",
                "everyX": 1,
                "repeat": {
                    "su": False,
                    "m": True,
                    "t": False,
                    "w": True,
                    "th": False,
                    "f": True,
                    "s": False,
                },
            },
        ),
        (
            {"type": "todo", "text": "Ship", "date": "2026-08-31"},
            {"type": "todo", "text": "Ship", "notes": "", "priority": 1.0, "date": "2026-08-31"},
        ),
    ],
)
def test_create_payloads_are_allowlisted(body, expected):
    task_type, payload, checklist = validate_task_payload(body, creating=True)
    assert task_type == body["type"]
    assert payload == expected
    assert checklist is None


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"type": "reward", "text": "No"},
        {"type": "habit", "text": "   "},
        {"type": "habit", "text": "X", "up": False, "down": False},
        {"type": "habit", "text": "X", "date": "2026-08-31"},
        {"type": "daily", "text": "X", "up": True},
        {"type": "daily", "text": "X", "repeatDays": []},
        {"type": "daily", "text": "X", "repeatDays": ["m", "m"]},
        {"type": "todo", "text": "X", "repeatDays": ["m"]},
        {"type": "todo", "text": "X", "date": "2026-02-30"},
        {"type": "todo", "text": "X", "completed": True},
        {"type": "todo", "text": "X", "priority": True},
        {"type": "todo", "text": "X", "priority": 3},
        {"type": "todo", "text": "X", "unknown": "private"},
    ],
)
def test_invalid_or_cross_type_fields_are_rejected(body):
    with pytest.raises(TaskValidationError):
        validate_task_payload(body, creating=True)


def test_input_size_limits_and_checklist_validation():
    with pytest.raises(TaskValidationError):
        validate_task_payload({"type": "todo", "text": "x" * (MAX_TITLE_LENGTH + 1)}, creating=True)
    with pytest.raises(TaskValidationError):
        validate_task_payload(
            {"type": "todo", "text": "x", "notes": "n" * (MAX_NOTES_LENGTH + 1)}, creating=True
        )
    with pytest.raises(TaskValidationError):
        validate_task_payload(
            {
                "type": "todo",
                "text": "x",
                "checklist": [{"text": "c" * (MAX_CHECKLIST_TEXT_LENGTH + 1)}],
            },
            creating=True,
        )


def test_update_cannot_change_type_and_can_be_checklist_only():
    with pytest.raises(TaskValidationError):
        validate_task_payload(
            {"revision": "a" * 32, "type": "daily", "text": "x"},
            creating=False,
            existing_type="todo",
        )
    task_type, fields, checklist = validate_task_payload(
        {"revision": "a" * 32, "checklist": [{"id": ITEM_ID, "text": "one", "completed": False}]},
        creating=False,
        existing_type="todo",
    )
    assert task_type == "todo"
    assert fields == {}
    assert checklist == [{"id": ITEM_ID, "text": "one", "completed": False}]


def raw_task(task_type="daily"):
    return {
        "id": TASK_ID,
        "type": task_type,
        "text": "Task <b>not HTML</b>",
        "notes": "Private-looking text remains plain content",
        "priority": 1.5,
        "value": 5,
        "completed": False,
        "isDue": True,
        "frequency": "weekly",
        "everyX": 1,
        "repeat": {"m": True, "w": True},
        "startDate": "2026-08-01T00:00:00.000Z",
        "streak": 4,
        "checklist": [{"id": ITEM_ID, "text": "Step", "completed": False}],
        "owner": "secret-owner",
        "history": [{"date": 1}],
        "challenge": {},
    }


def test_normalization_whitelists_and_preserves_authoritative_daily_state():
    task = normalize_task(raw_task())
    assert task is not None
    assert task["dueToday"] is True
    assert task["repeatDays"] == ["m", "w"]
    assert task["scheduleEditable"] is True
    assert task["checklist"] == [
        {"id": ITEM_ID, "text": "Step", "textTruncated": False, "completed": False}
    ]
    assert "owner" not in task
    assert "history" not in task
    assert "challenge" not in task
    assert task["value"] == 5
    assert task["colorToken"] == "better"
    assert len(task["revision"]) == 32


@pytest.mark.parametrize(
    ("frequency", "expected"),
    [
        ("daily", "daily"),
        ("weekly", "weekly"),
        ("monthly", "monthly"),
        (None, "daily"),
        ("yearly", "daily"),
        (42, "daily"),
    ],
)
def test_habit_counter_frequency_is_strict_and_uses_model_default(frequency, expected):
    habit = raw_task("habit")
    habit["frequency"] = frequency
    normalized = normalize_task(habit)
    assert normalized is not None
    assert normalized["counterFrequency"] == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, 0),
        (0, 0),
        (2, 2),
        (2.0, 2),
        (-1, 0),
        (1.5, 0),
        (True, 0),
        ("2", 0),
        (float("nan"), 0),
        (float("inf"), 0),
        (9_007_199_254_740_992, 0),
    ],
)
def test_habit_counters_are_non_negative_exact_browser_safe_integers(value, expected):
    habit = raw_task("habit")
    habit.update({"counterUp": value, "counterDown": value, "history": [{"secret": 1}]})
    normalized = normalize_task(habit)
    assert normalized is not None
    assert normalized["counterUp"] == expected
    assert normalized["counterDown"] == expected
    assert "history" not in normalized


def test_non_habits_do_not_expose_habit_counter_values():
    daily = raw_task("daily")
    daily.update({"counterUp": 3, "counterDown": 4})
    normalized = normalize_task(daily)
    assert normalized is not None
    assert normalized["counterUp"] is None
    assert normalized["counterDown"] is None
    assert normalized["counterFrequency"] is None


@pytest.mark.parametrize(
    ("direction", "field"),
    [("up", "counterUp"), ("down", "counterDown")],
)
def test_confirmed_habit_score_fallback_advances_only_the_scored_counter(direction, field):
    normalized = normalize_task(
        {**raw_task("habit"), "counterUp": 2, "counterDown": 3, "frequency": "weekly"}
    )
    assert normalized is not None
    scored = optimistic_scored_task(normalized, direction)
    assert scored is not None
    assert scored[field] == normalized[field] + 1
    other = "counterDown" if field == "counterUp" else "counterUp"
    assert scored[other] == normalized[other]
    assert scored["counterFrequency"] == "weekly"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (-100, "worst"),
        (-20.0001, "worst"),
        (-20, "worse"),
        (-10.0001, "worse"),
        (-10, "bad"),
        (-1.0001, "bad"),
        (-1, "neutral"),
        (0, "neutral"),
        (0.9999, "neutral"),
        (1, "good"),
        (4.9999, "good"),
        (5, "better"),
        (9.9999, "better"),
        (10, "best"),
        (100, "best"),
    ],
)
def test_task_color_token_matches_habitica_boundaries(value, expected):
    assert task_color_token(value) == expected


@pytest.mark.parametrize(
    "value",
    [None, True, False, "10", float("nan"), float("inf"), -float("inf"), 10**1000],
)
def test_task_color_token_uses_neutral_for_invalid_values(value):
    assert task_color_token(value) == "neutral"


def test_task_value_and_color_do_not_change_edit_revision():
    low = raw_task()
    low["value"] = -21
    high = raw_task()
    high["value"] = 10
    normalized_low = normalize_task(low)
    normalized_high = normalize_task(high)
    assert normalized_low is not None
    assert normalized_high is not None
    assert normalized_low["revision"] == normalized_high["revision"]
    assert normalized_low["colorToken"] == "worst"
    assert normalized_high["colorToken"] == "best"


def test_malformed_boolean_is_not_truthy_coerced():
    malformed = raw_task()
    malformed["completed"] = "false"
    assert normalize_task(malformed) is None
    malformed = raw_task()
    malformed["checklist"][0]["completed"] = "false"
    assert normalize_task(malformed) is None


def test_advanced_daily_schedule_is_read_only_not_rewritten():
    advanced = raw_task()
    advanced.update({"frequency": "monthly", "everyX": 2})
    task = normalize_task(advanced)
    assert task is not None
    assert task["frequency"] == "monthly"
    assert task["everyX"] == 2
    assert task["scheduleEditable"] is False


def test_truncation_is_explicit_and_blocks_editor_overwrite():
    oversized = raw_task()
    oversized["text"] = "x" * (MAX_TITLE_LENGTH + 2)
    task = normalize_task(oversized)
    assert task is not None
    assert task["textTruncated"] is True
    assert task["canEdit"] is False


def test_challenge_task_cannot_be_edited_or_deleted():
    linked = raw_task()
    linked["challenge"] = {"id": "not-returned"}
    task = normalize_task(linked)
    assert task is not None
    assert task["canEdit"] is False
    assert task["canDelete"] is False


def test_normalize_tasks_rejects_malformed_collection_and_deduplicates():
    assert normalize_tasks({}) is None
    first = raw_task()
    assert normalize_tasks([first, dict(first)]) == [normalize_task(first)]
    assert normalize_tasks([first, {"bad": "shape"}]) is None


def test_checklist_diff_rejects_unknown_ids_and_is_deterministic():
    existing = [{"id": ITEM_ID, "text": "old", "completed": False}]
    requested = [
        {"id": ITEM_ID, "text": "new", "completed": False},
        {"text": "added", "completed": False},
    ]
    additions, updates, removals = checklist_changes(existing, requested)
    assert additions == [{"text": "added", "completed": False}]
    assert updates == [{"id": ITEM_ID, "text": "new", "completed": False}]
    assert removals == []

    with pytest.raises(TaskValidationError):
        checklist_changes(existing, [{"id": OTHER_ITEM_ID, "text": "bad", "completed": False}])


def test_checklist_editor_does_not_turn_completion_changes_into_writes():
    existing = [{"id": ITEM_ID, "text": "same", "completed": True}]
    requested = [{"id": ITEM_ID, "text": "same", "completed": False}]
    additions, updates, removals = checklist_changes(existing, requested)
    assert (additions, updates, removals) == ([], [], [])
