from __future__ import annotations

import pytest

from miniapp_todo_lists import (
    TodoListValidationError,
    duplicate_list_name,
    friendly_list_name,
    normalize_user_tags,
    primary_list_id,
    tags_for_list_assignment,
    todo_lists_from_tags,
    validate_list_name,
)


WORK = "123e4567-e89b-42d3-a456-426614174010"
PERSONAL = "123e4567-e89b-42d3-a456-426614174011"
ORDINARY = "123e4567-e89b-42d3-a456-426614174012"
UNKNOWN = "123e4567-e89b-42d3-a456-426614174013"


def raw_tags():
    return [
        {"id": ORDINARY, "name": "Focus"},
        {"id": WORK, "name": "list:Work"},
        {"id": PERSONAL, "name": "list:Personal"},
    ]


def lists():
    normalized = normalize_user_tags(raw_tags())
    assert normalized is not None
    return todo_lists_from_tags(normalized)


def test_list_tags_are_trimmed_ordered_metadata_only():
    assert friendly_list_name("list: Work ") == "Work"
    assert friendly_list_name("List:Work") is None
    assert [item["name"] for item in lists()] == ["Work", "Personal"]
    assert [item["order"] for item in lists()] == [1, 2]


@pytest.mark.parametrize("value", ["", "   ", "bad\nname", "x" * 81, None])
def test_invalid_list_names_are_rejected(value):
    with pytest.raises(TodoListValidationError):
        validate_list_name(value)


def test_duplicate_names_are_case_insensitive_but_allow_same_record_on_rename():
    assert duplicate_list_name(lists(), " work ") is True
    assert duplicate_list_name(lists(), "WORK", excluding_id=WORK) is False


def test_primary_assignment_requires_exactly_one_current_list_tag():
    assert primary_list_id([ORDINARY, WORK], lists()) == WORK
    assert primary_list_id([ORDINARY], lists()) is None
    assert primary_list_id([UNKNOWN], lists()) is None
    assert primary_list_id([WORK, PERSONAL], lists()) is None


def test_explicit_save_preserves_ordinary_and_unknown_ids_and_normalizes_lists():
    original = [ORDINARY, WORK, UNKNOWN, PERSONAL]
    assert tags_for_list_assignment(original, lists(), PERSONAL) == [
        ORDINARY,
        UNKNOWN,
        PERSONAL,
    ]
    assert tags_for_list_assignment(original, lists(), None) == [ORDINARY, UNKNOWN]


def test_selected_list_must_belong_to_current_user():
    with pytest.raises(TodoListValidationError):
        tags_for_list_assignment([ORDINARY], lists(), UNKNOWN)
