import asyncio
import logging
import pickle
import time
from datetime import datetime, time as day_time, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from telegram import Chat, Message, Update, User

import habitica_bot as bot


FAKE_BOT_TOKEN = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"


def run(coroutine):
    return asyncio.run(coroutine)


@pytest.fixture(autouse=True)
def block_unmocked_backends(monkeypatch):
    """Make every test opt in to any Habitica-facing operation it needs."""

    def unexpected_backend_call(*args, **kwargs):
        raise AssertionError("unexpected Habitica API call")

    for name in (
        "buy_potion",
        "buy_reward",
        "create_todo_task",
        "get_status",
        "get_task_by_id",
        "get_tasks",
        "run_cron",
        "score_task",
    ):
        monkeypatch.setattr(bot, name, unexpected_backend_call)


@pytest.mark.parametrize("stats", [None, {}, [], "not-a-status-object"])
def test_status_block_rejects_missing_or_malformed_status(stats):
    assert bot.build_status_block(stats) == ""


def test_status_block_and_delta_safely_format_malformed_numbers():
    status = bot.build_status_block(
        {
            "hp": "not-a-number",
            "mp": None,
            "gp": float("nan"),
            "lvl": object(),
            "exp": float("inf"),
            "toNextLevel": [],
        }
    )

    assert "HP: 0" in status
    assert "MP: 0" in status
    assert "Gold: 0" in status
    assert "Level: 0 (0/0)" in status
    assert "nan" not in status.lower()
    assert "inf" not in status.lower()
    assert (
        bot.format_stats_delta(
            {"hp": "bad", "mp": None, "gp": float("nan"), "exp": []},
            {"hp": object(), "mp": float("inf"), "gp": {}, "exp": "bad"},
        )
        == "no change"
    )


def test_iso_reminder_time_is_converted_to_habitica_local_time():
    assert bot._parse_time_of_day(
        "2026-01-02T04:30:00Z",
        tz_offset_minutes=-210,
    ) == day_time(8, 0)
    assert bot._parse_time_of_day(
        "2026-01-02T04:30:00+03:30",
        tz_offset_minutes=-210,
    ) == day_time(4, 30)


@pytest.mark.parametrize("invalid_time", ["2400", "1260", "9999", "-100", ""])
def test_invalid_compact_reminder_time_returns_none(invalid_time):
    assert bot._parse_time_of_day(invalid_time) is None


def test_authenticated_inline_query_results_are_personal(monkeypatch):
    monkeypatch.setattr(
        bot,
        "get_status",
        lambda user_id, api_key: {"stats": {"hp": 50, "mp": 10, "gp": 2}},
    )
    inline_query = SimpleNamespace(query="", answer=AsyncMock())
    update = SimpleNamespace(inline_query=inline_query)
    context = SimpleNamespace(user_data={"USER_ID": "habitica-user", "API_KEY": "habitica-key"})

    run(bot.inline_query_handler(update, context))

    inline_query.answer.assert_awaited_once()
    _, kwargs = inline_query.answer.await_args
    assert kwargs["is_personal"] is True
    assert kwargs["cache_time"] == 0


def test_on_error_logs_only_exception_type_and_update_id(
    monkeypatch,
    caplog,
):
    secret = "habitica-api-key-secret"

    class SensitiveUpdate:
        update_id = 4242

        def __repr__(self):
            return f"<SensitiveUpdate credential={secret}>"

    monkeypatch.setattr(bot, "debug", False)
    caplog.set_level(logging.ERROR, logger=bot.__name__)

    run(
        bot.on_error(
            SensitiveUpdate(),
            SimpleNamespace(error=RuntimeError(f"backend failed: {secret}")),
        )
    )

    assert "RuntimeError" in caplog.text
    assert "4242" in caplog.text
    assert secret not in caplog.text
    assert "SensitiveUpdate" not in caplog.text


def test_command_registration_failure_is_sanitized_and_reported(
    caplog,
):
    secret = "telegram-bot-token-secret"
    telegram_bot = SimpleNamespace(
        delete_my_commands=AsyncMock(side_effect=bot.NetworkError(f"request failed: {secret}")),
        set_my_commands=AsyncMock(),
        set_chat_menu_button=AsyncMock(),
    )
    caplog.set_level(logging.WARNING, logger=bot.__name__)

    registered = run(bot._register_commands(SimpleNamespace(bot=telegram_bot)))

    assert registered is False
    assert "NetworkError" in caplog.text
    assert secret not in caplog.text
    telegram_bot.set_my_commands.assert_not_awaited()


def _credential_update(text):
    message = SimpleNamespace(
        text=text,
        id=17,
        message_id=17,
        delete=AsyncMock(),
        reply_text=AsyncMock(),
        is_topic_message=False,
        message_thread_id=None,
    )
    return SimpleNamespace(
        message=message,
        effective_message=message,
        effective_chat=SimpleNamespace(id=101),
        effective_user=SimpleNamespace(id=101),
    )


def test_cancelled_relink_keeps_final_credentials_unchanged():
    original = {
        "USER_ID": "old-user-id",
        "API_KEY": "old-api-key",
        "AVATAR_PNG_PATH": "/private/cache/avatar.png",
    }
    user_data = dict(original)
    context = SimpleNamespace(
        user_data=user_data,
        bot=SimpleNamespace(send_message=AsyncMock()),
    )

    relink_update = _credential_update("/relink")
    assert run(bot.relink_command_handler(relink_update, context)) == bot.USER_ID
    assert user_data == original

    user_id_update = _credential_update("new-user-id")
    assert run(bot.get_user_id_command_handler(user_id_update, context)) == bot.API_KEY
    assert user_data[bot.UD_PENDING_USER_ID] == "new-user-id"
    assert user_data["USER_ID"] == original["USER_ID"]
    assert user_data["API_KEY"] == original["API_KEY"]
    user_id_update.effective_message.delete.assert_awaited_once()

    cancel_update = _credential_update("/cancel")
    assert run(bot.cancel_command_handler(cancel_update, context)) == bot.ConversationHandler.END
    assert user_data == original


def test_empty_todo_title_is_rejected_without_advancing_state():
    message = SimpleNamespace(
        text="  \n\t ",
        is_topic_message=False,
        message_thread_id=None,
    )
    update = SimpleNamespace(
        message=message,
        effective_message=message,
        effective_chat=SimpleNamespace(id=202),
    )
    send_message = AsyncMock()
    context = SimpleNamespace(
        user_data={},
        bot=SimpleNamespace(send_message=send_message),
    )

    state = run(bot.add_todo_title_received(update, context))

    assert state == bot.ADD_TODO_TITLE
    assert "new_todo_title" not in context.user_data
    send_message.assert_awaited_once()
    assert send_message.await_args.kwargs["chat_id"] == 202
    assert "non‑empty title" in send_message.await_args.kwargs["text"]


def test_status_command_preserves_the_cached_pin(monkeypatch):
    message = SimpleNamespace(
        message_id=12,
        reply_text=AsyncMock(return_value=SimpleNamespace(message_id=13)),
    )
    update = SimpleNamespace(
        message=message,
        effective_chat=SimpleNamespace(id=202),
    )
    user_data = {
        "pinned_status_message_id_202": 99,
        "pinned_status_text_202": "existing status",
    }
    context = SimpleNamespace(user_data=user_data)
    updater = AsyncMock()
    monkeypatch.setattr(bot, "update_and_pin_status", updater)

    run(bot.get_status_command_handler(update, context))

    assert user_data["pinned_status_message_id_202"] == 99
    assert user_data["pinned_status_text_202"] == "existing status"
    updater.assert_awaited_once_with(
        context,
        chat_id=202,
        user_command_message_id=12,
        bot_status_message_id=13,
    )


def _callback_update(data):
    message = SimpleNamespace(
        reply_markup=None,
        text=None,
        caption=None,
        photo=None,
        video=None,
        animation=None,
        document=None,
        chat=SimpleNamespace(id=303, type="private"),
        message_id=33,
    )
    query = SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=303),
        message=message,
        inline_message_id=None,
        answer=AsyncMock(),
        edit_message_caption=AsyncMock(),
        edit_message_text=AsyncMock(),
        edit_message_reply_markup=AsyncMock(),
    )
    update = SimpleNamespace(
        callback_query=query,
        effective_chat=message.chat,
        effective_message=message,
    )
    return update, query


def test_failed_daily_score_does_not_toggle_refresh_state(monkeypatch):
    update, query = _callback_update("yester:0:daily-id")
    cron_meta = {"daily-id": {"checked": False, "text": "Morning routine"}}
    context = SimpleNamespace(
        user_data={
            "USER_ID": "habitica-user",
            "API_KEY": "habitica-key",
            "cron_meta": cron_meta,
        },
        bot=SimpleNamespace(),
    )
    scorer = Mock(return_value=({"hp": 50}, {"hp": 50}, None))
    monkeypatch.setattr(bot, "get_old_and_new_stats_for_scored_task", scorer)

    run(bot.task_button_handler(update, context))

    assert context.user_data["cron_meta"]["daily-id"]["checked"] is False
    scorer.assert_called_once_with(
        user_id="habitica-user",
        api_key="habitica-key",
        task_id="daily-id",
        direction="up",
    )
    query.answer.assert_awaited_once_with(
        "❌ Habitica did not update the Daily.",
        show_alert=True,
    )
    query.edit_message_reply_markup.assert_not_awaited()


@pytest.mark.parametrize("callback_data", ["yester", "yester:2:daily-id", "yester:0:"])
def test_malformed_daily_callbacks_are_rejected_before_scoring(
    monkeypatch,
    callback_data,
):
    update, query = _callback_update(callback_data)
    context = SimpleNamespace(
        user_data={"USER_ID": "habitica-user", "API_KEY": "habitica-key"},
        bot=SimpleNamespace(),
    )
    scorer = Mock(side_effect=AssertionError("malformed callback reached scoring"))
    monkeypatch.setattr(bot, "get_old_and_new_stats_for_scored_task", scorer)

    run(bot.task_button_handler(update, context))

    scorer.assert_not_called()
    assert query.answer.await_count == 1
    assert query.answer.await_args.kwargs["show_alert"] is True


def _freeze_utc(monkeypatch, utc_moment):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return utc_moment.replace(tzinfo=None)
            return utc_moment.astimezone(tz)

    monkeypatch.setattr(bot, "datetime", FrozenDateTime)


def _reminder_application(user_data):
    return SimpleNamespace(
        user_data={404: user_data},
        bot=SimpleNamespace(),
        mark_data_for_update_persistence=Mock(),
        update_persistence=AsyncMock(),
    )


def test_reminder_tick_deduplicates_and_marks_changed_user_for_persistence(
    monkeypatch,
):
    _freeze_utc(
        monkeypatch,
        datetime(2026, 1, 2, 4, 30, tzinfo=timezone.utc),
    )
    monkeypatch.setenv("REMINDER_WINDOW_SECONDS", "60")
    task = {
        "id": "daily-id",
        "text": "Take medicine",
        "completed": False,
        "isDue": True,
        "reminders": [{"id": "morning", "time": "2026-01-02T04:30:00Z"}],
    }

    def fake_get_tasks(user_id, api_key, task_type):
        return [task] if task_type == "dailys" else []

    monkeypatch.setattr(bot, "get_tasks", fake_get_tasks)
    delivery = AsyncMock(return_value=True)
    monkeypatch.setattr(bot, "_send_task_reminder", delivery)
    user_data = {
        "USER_ID": "habitica-user",
        "API_KEY": "habitica-key",
        bot.UD_TZ_OFFSET: -210,
        bot.UD_TZ_OFFSET_UPDATED_AT: int(time.time()),
    }
    application = _reminder_application(user_data)

    first = run(bot.run_reminder_tick(application))
    second = run(bot.run_reminder_tick(application))

    assert first["sent"] == 1
    assert first["errors"] == 0
    assert second["sent"] == 0
    assert delivery.await_count == 1
    assert len(user_data[bot.UD_SENT_REMINDERS]) == 1
    assert application.mark_data_for_update_persistence.call_count == 2
    application.mark_data_for_update_persistence.assert_called_with(user_ids={404})
    assert application.update_persistence.await_count == 2


def test_one_reminder_delivery_failure_does_not_block_the_next_task(
    monkeypatch,
):
    _freeze_utc(
        monkeypatch,
        datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc),
    )
    monkeypatch.setenv("REMINDER_WINDOW_SECONDS", "60")
    tasks = [
        {
            "id": task_id,
            "text": task_id,
            "completed": False,
            "isDue": True,
            "reminders": [{"id": task_id, "time": "12:00"}],
        }
        for task_id in ("first", "second")
    ]

    def fake_get_tasks(user_id, api_key, task_type):
        return tasks if task_type == "dailys" else []

    monkeypatch.setattr(bot, "get_tasks", fake_get_tasks)
    delivery = AsyncMock(side_effect=[RuntimeError("delivery failed"), True])
    monkeypatch.setattr(bot, "_send_task_reminder", delivery)
    user_data = {
        "USER_ID": "habitica-user",
        "API_KEY": "habitica-key",
        bot.UD_TZ_OFFSET: 0,
        bot.UD_TZ_OFFSET_UPDATED_AT: int(time.time()),
    }
    application = _reminder_application(user_data)

    result = run(bot.run_reminder_tick(application))

    assert result["sent"] == 1
    assert result["errors"] == 1
    assert result["users_checked"] == 1
    assert delivery.await_count == 2
    sent_keys = set(user_data[bot.UD_SENT_REMINDERS])
    assert any(":second:" in key for key in sent_keys)
    assert not any(":first:" in key for key in sent_keys)
    application.mark_data_for_update_persistence.assert_called_once_with(user_ids={404})
    application.update_persistence.assert_awaited_once()


def _update_for_chat_type(chat_type):
    telegram_user = User(id=505, first_name="Test", is_bot=False)
    chat_id = 505 if chat_type == "private" else -505
    message = Message(
        message_id=1,
        date=datetime(2026, 1, 2, tzinfo=timezone.utc),
        chat=Chat(id=chat_id, type=chat_type),
        from_user=telegram_user,
        text="/start",
    )
    return Update(update_id=1, message=message)


def test_application_uses_temp_persistence_and_private_account_filters(
    monkeypatch,
    tmp_path,
):
    persistence_path = tmp_path / "state" / "botdata.pkl"
    persistence_path.parent.mkdir()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", FAKE_BOT_TOKEN)
    monkeypatch.setenv("BOT_DATA_PATH", str(persistence_path))

    application = bot.build_application()

    assert application.bot.token == FAKE_BOT_TOKEN
    assert application.persistence.filepath == persistence_path.resolve()
    assert not persistence_path.exists()

    handlers = [
        handler for group_handlers in application.handlers.values() for handler in group_handlers
    ]
    account = next(
        handler
        for handler in handlers
        if isinstance(handler, bot.ConversationHandler) and handler.name == "account_link"
    )
    assert account.persistent is True

    command_handlers = [handler for handler in handlers if isinstance(handler, bot.CommandHandler)]
    for conversation in (
        handler for handler in handlers if isinstance(handler, bot.ConversationHandler)
    ):
        command_handlers.extend(
            handler
            for handler in (*conversation.entry_points, *conversation.fallbacks)
            if isinstance(handler, bot.CommandHandler)
        )
    registered_commands = {command for handler in command_handlers for command in handler.commands}
    assert {
        "start",
        "relink",
        "cancel",
        "status",
        "habits",
        "dailys",
        "todos",
        "completedtodos",
        "rewards",
        "buy_potion",
        "refresh_day",
        "add_todo",
        "inline",
        "menu",
        "menu_rk",
        "refresh_menu",
        "notify_here",
        "reminder_status",
        "task_list",
        "avatar",
        "debug",
        "sync_commands",
    } <= registered_commands

    private_update = _update_for_chat_type("private")
    group_update = _update_for_chat_type("group")
    start_entry = next(handler for handler in account.entry_points if "start" in handler.commands)
    assert start_entry.filters.check_update(private_update)
    assert not start_entry.filters.check_update(group_update)
    for state in (bot.USER_ID, bot.API_KEY):
        credential_filter = account.states[state][0].filters
        assert credential_filter.check_update(private_update)
        assert not credential_filter.check_update(group_update)

    group_guidance = next(
        handler
        for handler in handlers
        if getattr(handler, "callback", None) is bot.account_link_private_only_handler
    )
    assert not group_guidance.filters.check_update(private_update)
    assert group_guidance.filters.check_update(group_update)

    assert any(
        isinstance(handler, bot.InlineQueryHandler) and handler.callback is bot.inline_query_handler
        for handler in handlers
    )
    assert any(
        isinstance(handler, bot.CallbackQueryHandler)
        and handler.callback is bot.task_button_handler
        for handler in handlers
    )
    assert bot.on_error in application.error_handlers


def test_application_requires_a_token(monkeypatch, tmp_path):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
        bot.build_application(persistence_path=tmp_path / "never-created.pkl")


def test_legacy_pickle_keeps_credentials_reminders_and_conversation_state(tmp_path):
    persistence_path = tmp_path / "legacy-botdata.pkl"
    legacy_user_data = {
        505: {
            "USER_ID": "fake-habitica-user",
            "API_KEY": "fake-habitica-key",
            bot.UD_SENT_REMINDERS: {"fake-reminder-key": 1},
        }
    }
    legacy_conversation = {(505, 505): bot.API_KEY}
    persistence_path.write_bytes(
        pickle.dumps(
            {
                "conversations": {"account_link": legacy_conversation},
                "user_data": legacy_user_data,
                "chat_data": {},
                # Older PTB pickle files may not contain bot_data.
            }
        )
    )

    persistence = bot.PicklePersistence(filepath=persistence_path)

    assert run(persistence.get_user_data()) == legacy_user_data
    assert run(persistence.get_conversations("account_link")) == legacy_conversation
