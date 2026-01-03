from typing import Final, Optional

import json
import subprocess
import tempfile
import shutil
from pathlib import Path
from io import BytesIO


from typing import Any, Callable, Awaitable, Optional

import asyncio
import html
import logging
import os

import shutil

from datetime import datetime, timedelta, timezone, time as dtime
import time as time_mod



import httpx
import requests
from telegram import (
    BotCommand,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeDefault,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InlineQueryResultCachedPhoto,
    InlineQueryResultCachedDocument,
    InlineQueryResultPhoto,
    InputTextMessageContent,
    KeyboardButton,
    MenuButtonCommands,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.error import (
    BadRequest,
    Forbidden,
    NetworkError,
    TelegramError,
    TimedOut,
)
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    InlineQueryHandler,
    MessageHandler,
    PicklePersistence,
    filters,
)
from telegram.helpers import escape_markdown
from telegram.request import HTTPXRequest

from Habitica_API import (
    buy_potion,
    buy_reward,
    create_todo_task,
    get_status,
    get_task_by_id,
    get_tasks,

)

import json
import subprocess
import tempfile


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)

debug = False

BOT_TOKEN: Final = os.environ.get("TELEGRAM_BOT_TOKEN")

if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")


def _detect_node_bin() -> str:
    """
    Try to find a usable 'node' binary.

    Order:
    1) Explicit env override: NODE_BIN
    2) Whatever is on PATH (shutil.which)
    3) Common nvm locations under ~/.nvm/versions/node/*/bin/node
    4) Plain 'node' as a last resort (may still fail)
    """
    # 1) Explicit override
    env_bin = os.environ.get("NODE_BIN")
    if env_bin and os.path.exists(env_bin):
        return env_bin

    # 2) PATH lookup
    found = shutil.which("node")
    if found:
        return found

    # 3) Look for nvm-installed node
    home = Path.home()
    nvm_root = home / ".nvm" / "versions" / "node"
    if nvm_root.is_dir():
        # pick "highest" version folder
        candidates = sorted(nvm_root.glob("v*/bin/node"), reverse=True)
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)

    # 4) Last resort: let subprocess try plain 'node'
    return "node"


NODE_BIN = _detect_node_bin()





HABITICA_API_URL: Final = "https://habitica.com/api/v3" # <-- ADD THIS LINE
CHOOSING_ACCOUNT = "CHOOSING_ACCOUNT"   # use a string to avoid collisions


persistence = PicklePersistence(filepath="botdata.pkl")


# --- Reminder / notification user_data keys ---
UD_NOTIFY_CHAT_ID = "notify_chat_id"
UD_NOTIFY_THREAD_ID = "notify_thread_id"
UD_REMINDERS_ENABLED = "reminders_enabled"
UD_REMINDER_SHOW_STATUS = "reminder_show_status"
# --- Reply-keyboard button labels ---
RK_BTN_REMINDER_SETTINGS = "⏰ Reminder Settings"

RK_BTN_REMINDERS_ON = "🔈 Reminders are ON"
RK_BTN_REMINDERS_OFF = "🔈 Reminders are OFF"

RK_BTN_REMINDERS_BASE = "🔔 Reminders"
RK_BTN_REMINDER_STATUS_BASE = "🧾 Status"
RK_BTN_REMINDER_NOTIFY_HERE = "🔔 Notify Here"
RK_BTN_REMINDER_BACK = "⬅️ Back"


UD_TZ_OFFSET = "tz_offset"              # Habitica preferences.timezoneOffset (minutes)
UD_TZ_OFFSET_UPDATED_AT = "tz_offset_updated_at"

UD_SENT_REMINDERS = "sent_reminders"    # dict: key -> unix_ts (for de-dupe)



CATEGORY, PHOTO, DESCRIPTION = range(3)
USER_ID, API_KEY, STATUS = range(3)

ADD_TODO_TITLE = "ADD_TODO_TITLE"
ADD_TODO_DIFFICULTY = "ADD_TODO_DIFFICULTY"


RK = ReplyKeyboardMarkup(
    [
        [KeyboardButton("🔎 Inline Menu")],
        [KeyboardButton("🌀 Habits"), KeyboardButton("📅 Dailys"),
         KeyboardButton("📝 Todos"), KeyboardButton("💰 Rewards")],
        [KeyboardButton("➕ New Todo"), KeyboardButton(RK_BTN_REMINDER_SETTINGS)],
        [KeyboardButton("📊 Status"), KeyboardButton("🎭 Avatar"),
         KeyboardButton("🧪 Buy Potion")],
        [KeyboardButton("🔄 Refresh Day")],
        [KeyboardButton("✅ Completed Todos"), KeyboardButton("🔁 Menu")],
    ],
    resize_keyboard=True,
    is_persistent=True,
    one_time_keyboard=False,
)



def build_reminder_settings_rk(user_data: dict) -> ReplyKeyboardMarkup:
    """Build the Reminder settings reply keyboard for the current user."""
    reminders_enabled = bool(user_data.get(UD_REMINDERS_ENABLED, True))
    reminders_label = RK_BTN_REMINDERS_ON if reminders_enabled else RK_BTN_REMINDERS_OFF

    show_status = bool(user_data.get(UD_REMINDER_SHOW_STATUS, False))
    status_label = RK_BTN_REMINDER_STATUS_BASE + (" ✔️" if show_status else "")

    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(reminders_label)],
            [KeyboardButton(status_label)],
            [KeyboardButton(RK_BTN_REMINDER_NOTIFY_HERE)],
            [KeyboardButton(RK_BTN_REMINDER_BACK)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        one_time_keyboard=False,
    )


def build_cron_keyboard_for_user(
    user_id: str,
    api_key: str,
    ids: list[str],
    compact: bool,
) -> InlineKeyboardMarkup:
    """
    Build the inline keyboard for the 'refresh day' message.

    - One button per Daily (☐ / ☑ + shortened title)
    - 'compact = False'  -> 1 button per row
    - 'compact = True'   -> up to 2 short titles per row, long titles alone
    - Bottom row: [Refresh][Cancel]
    - Last row: layout toggle button
    """
    buttons_with_len: list[tuple[InlineKeyboardButton, int]] = []

    for tid in ids:
        task = get_task_by_id(user_id, api_key, tid)
        if not task:
            continue

        title = task.get("text", "(no title)")
        # Shorten titles for button display
        short_title = title if len(title) <= 24 else title[:21] + "..."
        completed = bool(task.get("completed", False))
        prefix = "☑" if completed else "☐"
        state = "1" if completed else "0"  # 1 = completed, 0 = not
        btn = InlineKeyboardButton(
            f"{prefix} {short_title}",
            callback_data=f"cronDaily:{state}:{tid}",
        )
        buttons_with_len.append((btn, len(short_title)))

    rows: list[list[InlineKeyboardButton]] = []

    if compact:
        # 2 short titles per row, long ones alone
        SHORT_LIMIT = 14  # tweak as you like
        current_row: list[InlineKeyboardButton] = []
        for btn, length in buttons_with_len:
            is_long = length > SHORT_LIMIT
            if is_long:
                if current_row:
                    rows.append(current_row)
                    current_row = []
                rows.append([btn])
            else:
                current_row.append(btn)
                if len(current_row) == 2:
                    rows.append(current_row)
                    current_row = []
        if current_row:
            rows.append(current_row)
    else:
        # Full layout: 1 button per row
        for btn, _ in buttons_with_len:
            rows.append([btn])

    # Row: Refresh + Cancel
    rows.append([
        InlineKeyboardButton("🔄 Sync", callback_data="cron:run"),
        InlineKeyboardButton("❌ Cancel",  callback_data="cron:cancel"),
    ])

    # Row: layout toggle
    layout_label = "➡️ Compact layout" if not compact else "⬅️ Full layout"
    rows.append([
        InlineKeyboardButton(layout_label, callback_data="cronLayout:toggle"),
    ])

    return InlineKeyboardMarkup(rows)


# --- Generic layout helpers for menus (full / compact / super) ---

LAYOUT_MODES = ("full", "compact")#, "super")


def cycle_layout_mode(current: str) -> str:
    """full -> compact -> super -> full"""
    try:
        idx = LAYOUT_MODES.index(current)
        return LAYOUT_MODES[(idx + 1) % len(LAYOUT_MODES)]
    except ValueError:
        return "compact"


def layout_buttons_for_mode(
    buttons_with_len: list[tuple[InlineKeyboardButton, int]],
    mode: str,
) -> list[list[InlineKeyboardButton]]:
    """
    Turn a flat list of (button, text_length) into rows according to mode:
      - full:    1 per row
      - compact: up to 2 per row (short labels share a row)
      - super:   up to 3 per row (only very short labels share)
    """
    if mode == "full":
        return [[btn] for btn, _ in buttons_with_len]

    if mode == "compact":
        max_per_row = 2
        short_limit = 22
    elif mode == "super":
        max_per_row = 3
        short_limit = 16
    else:
        # fallback – behave like full
        return [[btn] for btn, _ in buttons_with_len]

    rows: list[list[InlineKeyboardButton]] = []
    current_row: list[InlineKeyboardButton] = []

    for btn, length in buttons_with_len:
        is_long = length > short_limit

        if is_long:
            # long title gets its own row
            if current_row:
                rows.append(current_row)
                current_row = []
            rows.append([btn])
        else:
            current_row.append(btn)
            if len(current_row) >= max_per_row:
                rows.append(current_row)
                current_row = []

    if current_row:
        rows.append(current_row)

    return rows


def layout_toggle_label_for_mode(mode: str) -> str:
    """What the toggle button should say for the *current* mode."""
    if mode == "full":
        return "🧩 Compact view"
    # if mode == "compact":
    #     return "🧩 Super compact view"
    return "🧩 Full view"


# --- One place to build the common footer rows for panels --------------------

# Map which panels have a layout toggle and what its callback prefix is
_LAYOUT_CB = {
    "dailys": "dMenuLayout",
    "todos": "tMenuLayout",
    "rewards": "rMenuLayout",
    "completedTodos": "cMenuLayout",
    # NOTE: habits intentionally omitted (no layout toggle in your flow)
}


def build_actions_footer(
    kind: str,
    layout_mode: str | None,
    include_potion: bool = True,
) -> list[list[InlineKeyboardButton]]:
    """
    Flexible footer used by all 'All ...' panels.

    - If layout_mode == "full": each button is on its own row.
    - Otherwise: all buttons stay in one row (current behaviour).
    """
    refresh_button = InlineKeyboardButton(
        "🔄 Sync",
        callback_data=f"panelRefresh:{kind}",
    )
    refresh_day_button = InlineKeyboardButton(
        "🌀 Refresh day",
        callback_data="cmd:refresh_day",
    )

    buttons: list[InlineKeyboardButton] = [refresh_button]

    if include_potion:
        buy_potion_button = InlineKeyboardButton(
            "🧪 Buy Potion",
            callback_data=f"cmd:buy_potion:{kind}",  # <--- add panel hint
        )
        buttons.append(buy_potion_button)

    buttons.append(refresh_day_button)

    rows: list[list[InlineKeyboardButton]] = []

    if layout_mode == "full":
        # One button per row
        for b in buttons:
            rows.append([b])
    else:
        # All buttons in a single row (what you have now)
        rows.append(buttons)

    return rows




def build_layout_toggle_row(kind: str, layout_mode: str | None) -> list[list[InlineKeyboardButton]]:
    """
    Optional: a one‑row layout toggle, only for panels that support it.
    """
    cb = _LAYOUT_CB.get(kind)
    if not cb or not layout_mode:
        return []
    return [[InlineKeyboardButton(layout_toggle_label_for_mode(layout_mode), callback_data=f"{cb}:next")]]





# --- Shared text builder for /dailys, /todos, inline "All ..." etc. ---------


PANEL_TITLES: dict[str, str] = {
    "habits": "🌀 Your Habits",
    "dailys": "📅 Your Dailies",
    "todos": "📝 Your Todos",
    "rewards": "💰 Your Rewards",
    "completedTodos": "✅ Your Completed Todos",
}

PANEL_HINTS: dict[str, str] = {
    "habits": "Tap ➖ / ➕ to score your habits.",
    "dailys": "Tap to mark them complete / uncomplete.",
    "todos": "Tap to mark them complete / uncomplete.",
    "rewards": "Tap a reward to buy it.",
    "completedTodos": "Tap to un-complete them (they'll move back into your Todos list).",
}


def build_panel_header_lines(kind: str, layout_mode: str | None = None) -> list[str]:
    """
    Header helper used by all the “All …” panels.

    - Always returns a bold title line, e.g. "<b>📝 Your Todos</b>".
    - Adds the short helper line only when layout_mode is *not* "full"
      (so it shows in compact/super modes), or when layout_mode is None.
    """
    title = PANEL_TITLES.get(kind)
    if not title:
        return []

    lines: list[str] = [f"<b>{html.escape(title)}</b>"]

    if layout_mode != "full":
        hint = PANEL_HINTS.get(kind)
        if hint:
            lines.append(hint)

    return lines


# Short vs “wider” headers per kind.
# - short: used for "full" layout
# - long:  used for "compact" / "super" layouts so text width matches buttons better
PANEL_HEADERS: dict[str, dict[str, str]] = {
    "habits": {
        "short": "<b>🌀 Your Habits</b>",
        "long": "<b>🌀 Your Habits</b>\nTap ➖ / ➕ to score your habits.",
    },
    "dailys": {
        "short": "<b>📅 Your Dailies</b>",
        "long": "<b>📅 Your Dailies</b>\nTap to mark them complete / uncomplete.",
    },
    "todos": {
        "short": "<b>📝 Your Todos</b>",
        "long": "<b>📝 Your Todos</b>\nTap to mark them complete / uncomplete.",
    },
    "rewards": {
        "short": "<b>💰 Rewards</b>",
        "long": "<b>💰 Rewards</b>\nTap a reward to buy it.",
    },
    "completedTodos": {
        "short": "<b>✅ Completed Todos</b>",
        "long": "<b>✅ Completed Todos</b>\nMost recent 30 completed todos:",
    },
}


# --- Per‑panel behaviour config: status vs list vs order ---------------------

PANEL_BEHAVIOUR: dict[str, dict[str, bool]] = {
    "habits": {
        # What you currently do for /habits:
        "show_status": True,
        "show_list": False,
        "list_first": True,
    },
    "dailys": {
        "show_status": True,
        "show_list": True,
        "list_first": True,
    },
    "todos": {
        "show_status": True,
        "show_list": True,
        "list_first": False,
    },
    "completedTodos": {
        "show_status": False,
        "show_list": True,
        "list_first": True,
    },
    "rewards": {
        "show_status": True,
        "show_list": False,
        "list_first": False,
    },
}





def build_panel_header(kind: str, layout_mode: str | None) -> list[str]:
    """
    Return the header lines for a given panel and layout.

    - habits: always use the long header (title + hint).
    - others:
        * "full" or None      -> short header (1 line)
        * "compact" / "super" -> long header (2 lines), if defined
    """
    conf = PANEL_HEADERS.get(kind)
    if conf:
        if kind == "habits":
            # Default for habits: always show the long header
            header = conf["long"]
        elif layout_mode in ("compact", "super"):
            header = conf["long"]
        else:
            header = conf["short"]
        return header.split("\n")

    # Fallback if we have no special header config
    title = PANEL_TITLES.get(kind, kind.title())
    return [f"<b>{html.escape(title)}</b>"]



def build_tasks_summary_lines(kind: str, tasks: list[dict]) -> list[str]:
    """
    Build the per-type summary lines (no Status here, no main header here).
    You can edit this ONE place any time you want to change the list format.
    """
    lines: list[str] = []

    # ---- HABITS -------------------------------------------------------------
    if kind == "habits":
        # One blank line after the header, then per-habit info
        lines.append("")
        for h in tasks:
            full_text = h.get("text", "(no title)")
            safe_full = html.escape(full_text)

            counter_up = int(h.get("counterUp", 0))
            counter_down = int(h.get("counterDown", 0))

            badge_parts: list[str] = []
            if counter_up:
                badge_parts.append(f"➕ {counter_up}")
            if counter_down:
                badge_parts.append(f"➖ {counter_down}")

            badge = " / ".join(badge_parts) if badge_parts else ""
            if badge:
                lines.append(f"{safe_full} ({badge})")
            else:
                lines.append(safe_full)

        return lines

    # ---- DAILYS -------------------------------------------------------------
    if kind == "dailys":
        lines.extend(
            [
                "",  # spacer after header
                "Active:  ⬤ Daily name",
                "Inactive: ◯ Daily name",
                "",
            ]
        )

        for t in tasks:
            full_text = t.get("text", "(no title)")
            safe_full = html.escape(full_text)

            is_completed = bool(t.get("completed", False))
            icon = "✔️" if is_completed else "✖️"

            # ⬤ / ◯ marker (scheduled today or not)
            is_due = bool(t.get("isDue", False))
            active_marker = "⬤" if is_due else "◯"

            lines.append(f"{active_marker} {icon} {safe_full}")

        return lines

    # ---- TODOS --------------------------------------------------------------
    if kind == "todos":
        lines.append("")  # spacer after header
        for t in tasks:
            full_text = t.get("text", "(no title)")
            safe_full = html.escape(full_text)
            is_completed = bool(t.get("completed", False))
            icon = "✔️" if is_completed else "✖️"
            lines.append(f"{icon} {safe_full}")
        return lines

    # ---- REWARDS ------------------------------------------------------------
    if kind == "rewards":
        lines.append("")  # spacer after header
        for t in tasks:
            full_text = t.get("text", "(no title)")
            safe_full = html.escape(full_text)
            value = int(t.get("value", 0))
            lines.append(f"{safe_full} ({value}g)")
        return lines

    # ---- COMPLETED TODOS ----------------------------------------------------
    if kind == "completedTodos":
        lines.append("")  # spacer after header
        for t in tasks:
            full_text = t.get("text", "(no title)")
            safe_full = html.escape(full_text)
            lines.append(f"↩️ {safe_full}")
        return lines

    # Fallback
    for t in tasks:
        full_text = t.get("text", "(no title)")
        lines.append(html.escape(full_text))

    return lines


def build_status_block(stats: dict | None) -> str:
    """
    Status block used inside panels.

    IMPORTANT: this is the ONLY place that uses <blockquote> for panels,
    so the existing _update_panel_text_if_needed logic continues to work.
    """
    if not stats:
        return ""

    hp = stats.get("hp", 0.0)
    mp = stats.get("mp", 0.0)
    gp = stats.get("gp", 0.0)
    lvl = stats.get("lvl", 0)
    exp = stats.get("exp", 0.0)
    to_next = stats.get("toNextLevel", 0.0)

    return (
        "<blockquote><b>Status</b>\n"
        f"❤️ HP: {hp:.0f}\n"
        f"🔮 MP: {mp:.0f}\n"
        f"💰 Gold: {gp:.0f}\n"
        f"⭐ Level: {lvl} ({exp:.0f}/{to_next:.0f})"
        "</blockquote>"
    )



def build_tasks_panel_text(
    kind: str,
    tasks: list[dict],
    *,
    status_text: str | None = None,
    show_status: bool = False,
    show_list: bool = True,
    list_first: bool = False,
    layout_mode: str | None = None,
) -> str:
    """
    Build the text for the “All …” panels (/dailys, /todos, inline all dailys/todos, etc).

    kind:        "dailys", "todos", "habits", ...
    tasks:       the Habitica task list
    status_text: pre-rendered HTML status block (from build_status_block)
    show_status: include the status block or not
    show_list:   include the textual summary list or not
    list_first:  if True -> list above status, else status above list
    layout_mode: "full" / "compact" / "super" (affects only header text)

    IMPORTANT:
    - We ALWAYS include a header (so the message is never empty).
    - We ONLY show the status block if show_status=True.
    - We ONLY show the summary list if show_list=True.
    - No more fallback to "status_text or ''" when parts are empty.
    """
    parts: list[str] = []

    # 1) Header (always present, even if we hide list & status)
    parts.extend(build_panel_header(kind, layout_mode))

    # 2) Optional list body (just the summary lines, no header inside)
    list_lines: list[str] = build_tasks_summary_lines(kind, tasks) if show_list else []

    # 3) Optional status block
    if status_text and show_status:
        if list_first:
            # header -> list -> status
            if list_lines:
                parts.extend(list_lines)
            parts.append("")  # blank line between list and status
            parts.append(status_text)
        else:
            # header -> status -> list
            parts.append(status_text)
            if list_lines:
                parts.append("")
                parts.extend(list_lines)
    else:
        # No status, just header + optional list
        if list_lines:
            parts.append("")
            parts.extend(list_lines)

    return "\n".join(parts)











def append_standard_footer(
    rows: list[list[InlineKeyboardButton]],
    kind: str,
    layout_mode: str | None,
    include_potion: bool = True,
) -> None:
    """
    Convenience: append the common footer row + the layout toggle row (if supported).

    Footer layout reacts to layout_mode:
      - "full"   -> each footer button on its own line
      - others   -> all footer buttons in a single row
    """
    rows += build_actions_footer(kind, layout_mode, include_potion=include_potion)
    rows += build_layout_toggle_row(kind, layout_mode)




def build_dailys_panel_keyboard(
    dailys: list[dict],
    layout_mode: str,
) -> InlineKeyboardMarkup:
    """
    Build the inline keyboard for the 'All Dailys' panel.

    - One button per Daily (✔ / ✖ + shortened title)
    - Layout is controlled by layout_mode:
        full    -> 1 per row
        compact -> up to 2 per row
        super   -> up to 3 per row
    - Bottom rows:
        * 🔄 Refresh (rebuild panel from API without scoring anything)
        * layout toggle button
    """
    buttons_with_len: list[tuple[InlineKeyboardButton, int]] = []
    MAX_LABEL_LEN = 28

    for t in dailys:
        full_text = t.get("text", "(no title)")
        short = full_text if len(full_text) <= MAX_LABEL_LEN else full_text[:MAX_LABEL_LEN - 1] + "…"

        is_completed = bool(t.get("completed", False))
        icon = "✔️" if is_completed else "✖️"
        action = "down" if is_completed else "up"
        task_id = t.get("id")
        if not task_id:
            continue

        # Button text – NO ⬤/◯ here, only ✔/✖ + title
        label = f"{icon} {short}"
        btn = InlineKeyboardButton(label, callback_data=f"dMenu:{action}:{task_id}")
        buttons_with_len.append((btn, len(label)))

    # Use your generic layout helper
    rows = layout_buttons_for_mode(buttons_with_len, layout_mode)

    append_standard_footer(rows, "dailys", layout_mode)

    return InlineKeyboardMarkup(rows)


def build_refresh_day_keyboard(
    cron_meta: dict,
    layout_mode: str,
) -> InlineKeyboardMarkup:
    """
    Build the inline keyboard for the 'refresh day' message, but keep the
    original ✖/✔ style and full titles.

    cron_meta is a dict: {task_id: {"text": str, "checked": bool}}
    """
    buttons_with_len: list[tuple[InlineKeyboardButton, int]] = []
    MAX_LABEL_LEN = 30

    for task_id, info in cron_meta.items():
        title = info.get("text", "(no title)")
        checked = bool(info.get("checked", False))
        state = "1" if checked else "0"
        icon = "✔️" if checked else "✖️"

        label = title if len(title) <= MAX_LABEL_LEN else title[:MAX_LABEL_LEN - 3] + "…"

        btn = InlineKeyboardButton(
            f"{icon} {label}",
            callback_data=f"yester:{state}:{task_id}",
        )
        buttons_with_len.append((btn, len(label)))

    # Lay out the daily buttons
    rows = layout_buttons_for_mode(buttons_with_len, layout_mode)

    # Row: Refresh + Cancel
    rows.append([
        InlineKeyboardButton("✅ Refresh day now", callback_data="cron:run"),
        InlineKeyboardButton("❌ Cancel",          callback_data="cron:cancel"),
    ])

    # Row: layout toggle
    rows.append([
        InlineKeyboardButton(
            layout_toggle_label_for_mode(layout_mode),
            callback_data="yesterLayout:next",
        )
    ])

    return InlineKeyboardMarkup(rows)



async def handle_reply_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()

    # --- Reminder settings (Reply Keyboard) ---
    if text == RK_BTN_REMINDER_SETTINGS:
        await topic_send(
            update,
            context.bot.send_message,
            chat_id=update.effective_chat.id,
            text="⏰ Reminder settings:",
            reply_markup=build_reminder_settings_rk(context.user_data),
            disable_notification=True,
            disable_web_page_preview=True,
        )
        context.chat_data["rk_active"] = True
        return

    if text in (
            f"{RK_BTN_REMINDERS_BASE} ✔️",
            f"{RK_BTN_REMINDERS_BASE} ✖️",
            RK_BTN_REMINDERS_BASE,  # optional fallback if you ever use it without icon
    ):
        cur = bool(context.user_data.get(UD_REMINDERS_ENABLED, True))
        context.user_data[UD_REMINDERS_ENABLED] = not cur

        msg = f"✅ Reminders: {'ON' if not cur else 'OFF'}"
        await topic_send(
            update,
            context.bot.send_message,
            chat_id=update.effective_chat.id,
            text=msg,
            reply_markup=build_reminder_settings_rk(context.user_data),
            disable_notification=True,
            disable_web_page_preview=True,
        )
        context.chat_data["rk_active"] = True
        return

    if text == RK_BTN_REMINDER_NOTIFY_HERE:
        await notify_here_command_handler(update, context)
        return

    if text in (RK_BTN_REMINDERS_ON, RK_BTN_REMINDERS_OFF):
        cur = bool(context.user_data.get(UD_REMINDERS_ENABLED, True))
        context.user_data[UD_REMINDERS_ENABLED] = not cur

        msg = RK_BTN_REMINDERS_ON if not cur else RK_BTN_REMINDERS_OFF
        await topic_send(
            update,
            context.bot.send_message,
            chat_id=update.effective_chat.id,
            text=msg,
            reply_markup=build_reminder_settings_rk(context.user_data),
            disable_notification=True,
            disable_web_page_preview=True,
        )
        context.chat_data["rk_active"] = True
        return


    if text in (RK_BTN_REMINDER_STATUS_BASE, f"{RK_BTN_REMINDER_STATUS_BASE} ✔️"):
        cur = bool(context.user_data.get(UD_REMINDER_SHOW_STATUS, False))
        context.user_data[UD_REMINDER_SHOW_STATUS] = not cur

        msg = f"✅ Status in reminders: {'ON' if not cur else 'OFF'}"
        await topic_send(
            update,
            context.bot.send_message,
            chat_id=update.effective_chat.id,
            text=msg,
            reply_markup=build_reminder_settings_rk(context.user_data),
            disable_notification=True,
            disable_web_page_preview=True,
        )
        context.chat_data["rk_active"] = True
        return

    if text == RK_BTN_REMINDER_BACK:
        # Force swap back to the default reply keyboard
        await show_rk_if_needed(update, context, text="⬅️ Back to menu.", force=True)
        return


    if text == "🌀 Habits":
        return await habits_command_handler(update, context)

    if text == "📅 Dailys":
        return await dailys_command_handler(update, context)

    if text == "📝 Todos":
        return await todos_command_handler(update, context)

    if text == "✅ Completed Todos":  # <-- ADD THIS
        return await completed_todos_command_handler(update, context)

    if text == "➕ New Todo":
        return await add_todo_start(update, context)


    if text == "💰 Rewards":
        return await rewards_command_handler(update, context)

    if text == "📊 Status":
        return await get_status_command_handler(update, context)

    if text == "🎭 Avatar":
        return await avatar_command_handler(update, context)

    if text == "🧪 Buy Potion":
        return await buy_potion_command_handler(update, context)

    if text == "🔄 Refresh Day":
        return await refresh_day_command_handler(update, context)

    if text == "🔁 Menu":
        return await menu_command_handler(update, context)

    if text == "🔎 Inline Menu":
        await send_inline_launcher(update, context)
        await show_rk_if_needed(update, context)
        return



def build_inline_launcher_kb():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🌀 Habits", switch_inline_query_current_chat="habits"),
            InlineKeyboardButton("📅 Dailys", switch_inline_query_current_chat="dailys"),
        ],
        [
            InlineKeyboardButton("📝 Todos", switch_inline_query_current_chat="todos"),
            InlineKeyboardButton("💰 Rewards", switch_inline_query_current_chat="rewards"),
        ],
        [
            InlineKeyboardButton("✅ Completed Todos", switch_inline_query_current_chat="completedTodos"),
        ],
        [
            InlineKeyboardButton("📊 Status", callback_data="cmd:status"),
            InlineKeyboardButton("🧪 Buy Potion", callback_data="cmd:buy_potion"),
        ],
        [
            InlineKeyboardButton("🎭 Avatar", callback_data="cmd:avatar"),
        ],
        [
            InlineKeyboardButton("🔄 Refresh Day", callback_data="cmd:refresh_day"),
        ],
    ])





async def inline_picker_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """This handler is now only triggered by specific text messages, not button presses."""
    # We only want to show the launcher if it's a direct message to the bot
    if update.message.chat.type == 'private':
        await send_inline_launcher(update, context)
        await show_rk_if_needed(update, context)


async def show_rk_if_needed(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str = "Menu is ready — use the 4-dot button to toggle.",
    force: bool = False,   # set True if you *want* to re-send even if we think it's active
) -> bool:
    """
    Sends a message with the Reply Keyboard (RK) only if we believe it's not active
    in this chat yet. We track this in context.chat_data['rk_active'].

    Returns True if it sent a message, False if it skipped.
    """
    if not force and context.chat_data.get("rk_active"):
        # We already sent RK for this chat (and haven't removed it).
        return False

    await topic_send(
        update,
        context.bot.send_message,
        chat_id=update.effective_chat.id,
        text=text,
        reply_markup=RK,
        disable_notification=True,
        disable_web_page_preview=True,
    )

    context.chat_data["rk_active"] = True
    return True

async def hide_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Menu hidden. Use /menu to show again.",
        reply_markup=ReplyKeyboardRemove()
    )
    context.chat_data["rk_active"] = False



async def send_inline_launcher(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Show the inline shortcut menu.

    If we have a cached avatar PNG, send it as a photo with caption.
    Otherwise fall back to a plain text message.
    """
    chat_id = update.effective_chat.id
    kb = build_inline_launcher_kb()

    png_path = context.user_data.get("AVATAR_PNG_PATH")

    # Try to send with avatar
    if png_path and os.path.exists(png_path):
        try:
            with open(png_path, "rb") as img:
                msg = await topic_send(
                    update,
                    context.bot.send_photo,
                    chat_id=chat_id,
                    photo=img,
                    caption="🎯 Quick Commands:",
                    reply_markup=kb,
                )
            # cache file_id for inline mode etc.
            if msg.photo:
                context.user_data["AVATAR_FILE_ID"] = msg.photo[-1].file_id
            return
        except Exception as e:
            logging.warning(
                "Failed to send inline launcher with avatar, falling back to text: %s",
                e,
            )

    # Fallback: text-only
    await topic_send(
        update,
        context.bot.send_message,
        chat_id=chat_id,
        text="🎯 Quick Commands:",
        reply_markup=kb,
    )





async def on_error(update, context):
    err = context.error

    # Ignore the noisy "query is too old" errors from Telegram
    if isinstance(err, BadRequest) and "query is too old" in str(err).lower():
        logging.warning("Ignoring old callback/inline query: %s", err)
        return

    # Everything else: log as before
    logging.exception("Unhandled exception in update: %s", update, exc_info=err)




def _signed(x: float) -> str:
    # Avoid "-0.0"
    if abs(x) < 0.05:
        return "0.0"
    return f"{'+' if x > 0 else ''}{x:.1f}"

def format_stats_delta(old_stats: dict, new_stats: dict) -> str:
    hp  = float(new_stats.get('hp',  0)) - float(old_stats.get('hp',  0))
    mp  = float(new_stats.get('mp',  0)) - float(old_stats.get('mp',  0))
    gp  = float(new_stats.get('gp',  0)) - float(old_stats.get('gp',  0))
    exp = float(new_stats.get('exp', 0)) - float(old_stats.get('exp', 0))

    parts = []
    if abs(hp)  >= 0.05: parts.append(f"♥ {_signed(hp)}")
    if abs(mp)  >= 0.05: parts.append(f"💧 {_signed(mp)}")
    if abs(gp)  >= 0.05: parts.append(f"💰 {_signed(gp)}")
    if abs(exp) >= 0.05: parts.append(f"📈 {_signed(exp)}")
    return ", ".join(parts) if parts else "no change"




async def _register_commands(app: Application) -> None:
    """
    Called at startup (post_init) and optionally from /sync_commands.

    If Telegram is unreachable, we just log and return instead of crashing the app.
    """
    commands = [
        BotCommand("start", "Link your Habitica account"),
        BotCommand("menu", "⌨ menu ⚡"),
        BotCommand("status", "Show your current HP, MP and Gold"),
        BotCommand("habits", "Show and manage your habits"),
        BotCommand("dailys", "Show and manage your dailies"),
        BotCommand("todos", "Show and manage your todos"),
        BotCommand("add_todo", "Create a new todo task"),
        BotCommand("completedtodos", "Show recently completed todos"),
        BotCommand("buy_potion", "Buy a health potion"),
        BotCommand("refresh_menu", "Reload the reply keyboard menu"),
        BotCommand("task_list", "List tasks (short/long)"),
        BotCommand("rewards", "Show rewards"),
        BotCommand("refresh_day", "Review yesterday’s dailies and refresh your day"),
        BotCommand("notify_here", "Send task reminders in this chat/topic"),
        BotCommand("reminder_status", "Status block in reminders (on/off)"),
        BotCommand("cancel", "Cancel current action"),
        # Optional: expose /sync_commands itself in menu
        BotCommand("sync_commands", "Re-sync command list with Telegram"),
        BotCommand("avatar", "Send your Habitica avatar image"),  # 👈 add this

    ]

    try:
        # 1) clear any old per-scope overrides that could shadow the default list
        await app.bot.delete_my_commands(scope=BotCommandScopeAllPrivateChats())
        await app.bot.delete_my_commands(scope=BotCommandScopeAllGroupChats())
        await app.bot.delete_my_commands(scope=BotCommandScopeDefault())

        # 2) set your commands
        await app.bot.set_my_commands(commands, scope=BotCommandScopeDefault())
        await app.bot.set_my_commands(commands, scope=BotCommandScopeAllPrivateChats())
        await app.bot.set_my_commands(commands, scope=BotCommandScopeAllGroupChats())

        # 3) ensure the menu button opens the commands list
        await app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())

        logging.info("✅ Bot commands registered / synced successfully.")

    except (TimedOut, NetworkError) as e:
        logging.warning(
            "⚠️ Could not register commands with Telegram (network issue): %s",
            e
        )
        # Do NOT re-raise – bot should still start and run handlers.
    except TelegramError as e:
        logging.error(
            "TelegramError while registering commands: %s",
            e
        )
    except Exception as e:
        logging.exception("Unexpected error while registering commands: %s", e)


async def start_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []

    # Deep-link start (/start habits|dailys|todos|rewards)
    if args and args[0] in {"habits", "dailys", "todos", "rewards"}:
        t = args[0]
        await update.message.reply_text(
            f"Tap to open {t.title()} inline here:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    f"Open {t.title()}",
                    switch_inline_query_current_chat=t
                )
            ]])
        )
        # keep conversation closed in this branch
        await send_inline_launcher(update, context)
        await show_rk_if_needed(update, context)
        return ConversationHandler.END

    has_creds = bool(context.user_data.get("USER_ID") and context.user_data.get("API_KEY"))
    if has_creds:
        # Ask whether to keep or change account
        choice_kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔄 Change account", callback_data="acct:change"),
                InlineKeyboardButton("✅ Keep current",   callback_data="acct:keep"),
            ]
        ])
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="You already linked a Habitica account. What would you like to do?",
            reply_markup=choice_kb
        )
        await show_rk_if_needed(update, context)
        return CHOOSING_ACCOUNT

    # No creds yet → start collecting (you can keep RK visible; don’t remove it)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="Enter USER_ID"
    )
    return USER_ID



async def sync_commands_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Manually re-run _register_commands while the bot is running.

    - If Telegram is reachable: your menu is updated.
    - If not: we show a message and keep the bot alive.
    """
    await update.message.reply_text("🔄 Syncing commands with Telegram …")

    try:
        # context.application is the running Application instance
        await _register_commands(context.application)
        await update.message.reply_text("✅ Commands synced successfully.")
    except (TimedOut, NetworkError) as e:
        logging.warning("Network problem in /sync_commands: %s", e)
        await update.message.reply_text(
            "⌛ I couldn’t reach Telegram to sync commands (timeout).\n"
            "Please try /sync_commands again in a bit."
        )
    except TelegramError as e:
        logging.error("TelegramError in /sync_commands: %s", e)
        await update.message.reply_text(
            "❌ Telegram API error while syncing commands. Check logs."
        )
    except Exception as e:
        logging.exception("Unexpected error in /sync_commands: %s", e)
        await update.message.reply_text(
            "❌ Unexpected error while syncing commands. Check logs."
        )



async def account_choice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    # await query.answer()
    action = (query.data or "")

    if action == "acct:keep":
        await query.message.reply_text("Okay, keeping your current account.")
        # Leave the RK active and end the conversation
        return ConversationHandler.END

    if action == "acct:change":
        # Optional: clear old creds now or after the new ones are saved
        # context.user_data.pop("USER_ID", None)
        # context.user_data.pop("API_KEY", None)
        await query.message.reply_text("Alright. Please send your new USER_ID. or /cancel")
        return USER_ID

    # unknown callback
    await query.message.reply_text("Please choose an option.")
    return CHOOSING_ACCOUNT


async def relink_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Let’s link a different account. Please send your new USER_ID or /cancel")
    return USER_ID


async def get_user_id_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_message.text.strip()
    context.user_data["USER_ID"] = user_id
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="Enter API_KEY",
        reply_to_message_id=update.effective_message.id,
    )
    return API_KEY



def ensure_avatar_png_no_update(
    *,
    habitica_user_id: str,
    habitica_api_key: str,
    user_data: dict,
    force_refresh: bool = False,
    preloaded_user_json: dict | None = None,
) -> str | None:
    """
    A tick-safe version of ensure_avatar_png:
    - No Update / no Context
    - Uses your existing Node renderer (render_avatar_from_json.js)
    - Caches AVATAR_PNG_PATH in user_data
    """
    existing_path = user_data.get("AVATAR_PNG_PATH")
    if not force_refresh and existing_path and os.path.exists(existing_path):
        return existing_path

    # 1) Fetch user JSON from Habitica (or reuse what the tick already fetched)
    user_json = preloaded_user_json or get_status(habitica_user_id, habitica_api_key)
    if not user_json:
        logging.warning("ensure_avatar_png_no_update: could not fetch Habitica user JSON")
        return None

    # 2) Pick a stable, safe filename from Habitica username/profile
    username = ""
    auth = user_json.get("auth") or {}
    local_auth = auth.get("local") or {}
    username = local_auth.get("username") or ""

    if not username:
        profile = user_json.get("profile") or {}
        username = profile.get("name") or ""

    if not username:
        username = "habitica_user"

    safe_username = "".join(
        c if c.isalnum() or c in ("-", "_") else "_"
        for c in username.strip()
    ) or "habitica_user"

    # 3) Render avatar via Node into temp PNG, copy to Avatar/<username>.png
    base_dir = os.path.dirname(os.path.abspath(__file__))
    node_script = os.path.join(base_dir, "render_avatar_from_json.js")

    with tempfile.TemporaryDirectory() as tmpdir:
        user_json_path = os.path.join(tmpdir, "user.json")
        tmp_png_path = os.path.join(tmpdir, "avatar.png")

        with open(user_json_path, "w", encoding="utf-8") as f:
            json.dump(user_json, f)

        try:
            proc = subprocess.run(
                [NODE_BIN, node_script, user_json_path, tmp_png_path],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            logging.error("Node.js binary not found. Tried NODE_BIN=%r", NODE_BIN)
            return None

        if proc.returncode != 0 or not os.path.exists(tmp_png_path):
            logging.error(
                "Avatar render failed (code=%s)\nSTDOUT:\n%s\nSTDERR:\n%s",
                proc.returncode,
                proc.stdout,
                proc.stderr,
            )
            return None

        avatar_dir = os.path.join(base_dir, "Avatar")
        os.makedirs(avatar_dir, exist_ok=True)

        final_png_path = os.path.join(avatar_dir, f"{safe_username}.png")
        with open(tmp_png_path, "rb") as src, open(final_png_path, "wb") as dst:
            dst.write(src.read())

    user_data["AVATAR_PNG_PATH"] = final_png_path
    return final_png_path





async def get_API_key_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    api_key = update.effective_message.text.strip()
    context.user_data["API_KEY"] = api_key

    # Default notifications: DM the user (private chat id == user id)
    context.user_data.setdefault(UD_NOTIFY_CHAT_ID, update.effective_user.id)
    context.user_data.setdefault(UD_NOTIFY_THREAD_ID, None)

    # Reminders on by default
    context.user_data.setdefault(UD_REMINDERS_ENABLED, True)

    context.user_data.setdefault(UD_REMINDER_SHOW_STATUS, False)

    # De-dupe store
    context.user_data.setdefault(UD_SENT_REMINDERS, {})

    await send_inline_launcher(update, context)
    await show_rk_if_needed(update, context)
    return ConversationHandler.END




async def menu_rk_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_inline_launcher(update, context)
    await show_rk_if_needed(update, context)

async def refresh_menu_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Hide+reopen style refresh:
    - Always re-sends the inline launcher
    - Always re-sends the reply keyboard, even if we think it's already active
    """
    # Re-send inline launcher
    await send_inline_launcher(update, context)
    # Force re-show the reply keyboard, regardless of rk_active flag
    await show_rk_if_needed(
        update,
        context,
        text="Menu refreshed — use the 4‑dot button to toggle.",
        force=True,
    )





async def notify_here_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Save the current chat + topic (message_thread_id) as the destination
    for reminder notifications for THIS user.
    """
    context.user_data[UD_NOTIFY_CHAT_ID] = update.effective_chat.id
    context.user_data[UD_NOTIFY_THREAD_ID] = getattr(update.effective_message, "message_thread_id", None)

    # Use topic_send so the confirmation message stays in the same topic
    await topic_send(
        update,
        context.bot.send_message,
        chat_id=update.effective_chat.id,
        text="✅ Got it. I’ll send your reminders *here* (in this chat/topic).",
        parse_mode="Markdown",
    )





async def ensure_avatar_png(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    force_refresh: bool = False,
) -> str | None:
    """
    Make sure we have an up-to-date avatar PNG for this Habitica user.

    - If force_refresh is False and context.user_data["AVATAR_PNG_PATH"] exists
      and the file is still there, reuse it.
    - Otherwise, fetch the user from Habitica, render via Node, save as
      Avatar/<habitica_username>.png, store the path in context.user_data,
      and return it.

    Returns the absolute path to the PNG, or None on error (and sends a short
    error message to the user).
    """
    user_id = context.user_data.get("USER_ID")
    api_key = context.user_data.get("API_KEY")

    if not user_id or not api_key:
        await update.message.reply_text(
            "Use /start to set USER_ID and API_KEY first."
        )
        return None

    # Reuse existing avatar if we already rendered it and the file still exists,
    # but only when we are NOT forcing a refresh.
    existing_path = context.user_data.get("AVATAR_PNG_PATH")
    if not force_refresh and existing_path and os.path.exists(existing_path):
        return existing_path

    # 1) Fetch user JSON from Habitica
    user_data = get_status(user_id, api_key)
    if not user_data:
        await update.message.reply_text(
            "❌ Could not fetch your Habitica profile. Please try again later."
        )
        return None

    # --- Determine a safe Habitica username for the filename ---
    username = ""

    auth = user_data.get("auth") or {}
    local_auth = auth.get("local") or {}
    username = local_auth.get("username") or ""

    if not username:
        profile = user_data.get("profile") or {}
        username = profile.get("name") or ""

    if not username:
        username = "habitica_user"

    safe_username = "".join(
        c if c.isalnum() or c in ("-", "_") else "_"
        for c in username.strip()
    ) or "habitica_user"

    # 2) Render avatar via Node into a temp PNG and copy it to Avatar/<username>.png
    base_dir = os.path.dirname(os.path.abspath(__file__))
    node_script = os.path.join(base_dir, "render_avatar_from_json.js")

    with tempfile.TemporaryDirectory() as tmpdir:
        user_json_path = os.path.join(tmpdir, "user.json")
        tmp_png_path = os.path.join(tmpdir, "avatar.png")

        with open(user_json_path, "w", encoding="utf-8") as f:
            json.dump(user_data, f)

        try:
            proc = subprocess.run(
                [NODE_BIN, node_script, user_json_path, tmp_png_path],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            logging.error("Node.js binary not found. Tried NODE_BIN=%r", NODE_BIN)
            await update.message.reply_text(
                "❌ Node.js is not available on this server, so I can't render the avatar image."
            )
            return None


        if proc.returncode != 0 or not os.path.exists(tmp_png_path):
            logging.error(
                "Avatar render failed (code=%s)\nSTDOUT:\n%s\nSTDERR:\n%s",
                proc.returncode,
                proc.stdout,
                proc.stderr,
            )
            await update.message.reply_text(
                "❌ Failed to generate avatar image. Check server logs for details."
            )
            return None

        # 3) Ensure Avatar directory exists and copy the PNG there
        avatar_dir = os.path.join(base_dir, "Avatar")
        os.makedirs(avatar_dir, exist_ok=True)

        final_png_path = os.path.join(avatar_dir, f"{safe_username}.png")

        with open(tmp_png_path, "rb") as src, open(final_png_path, "wb") as dst:
            dst.write(src.read())

    # Store for reuse
    context.user_data["AVATAR_PNG_PATH"] = final_png_path
    return final_png_path


async def send_avatar_photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    caption: str | None = None,
) -> None:
    """
    Ensure the avatar PNG exists for this user and send it to the current chat.

    - Always re‑render (force_refresh=True) so /avatar is up to date.
    - Cache:
        * AVATAR_FILE_ID        -> photo file_id (for photo-based panels etc.)
        * AVATAR_DOC_FILE_ID    -> document file_id (for inline cached document)
    """
    png_path = await ensure_avatar_png(update, context, force_refresh=True)
    if not png_path:
        # ensure_avatar_png already sent an error message
        return

    chat_id = update.effective_chat.id

    try:
        # 1) Send as photo (normal /avatar behaviour)
        with open(png_path, "rb") as img:
            photo_msg = await topic_send(
                update,
                context.bot.send_photo,
                chat_id=chat_id,
                photo=img,
                caption=caption,
            )

        if photo_msg.photo:
            context.user_data["AVATAR_FILE_ID"] = photo_msg.photo[-1].file_id

        # 2) Also send as document (quietly) to get a document_file_id for inline cached doc
        try:
            with open(png_path, "rb") as doc_fp:
                doc_msg = await topic_send(
                    update,
                    context.bot.send_document,
                    chat_id=chat_id,
                    document=doc_fp,
                    filename=os.path.basename(png_path),
                    disable_notification=True,
                )

            if doc_msg.document:
                context.user_data["AVATAR_DOC_FILE_ID"] = doc_msg.document.file_id

        except Exception as e:
            logging.warning("Failed to send avatar as document: %s", e)

    except FileNotFoundError:
        await update.message.reply_text(
            "❌ I generated your avatar but couldn't find the saved file. Please try again."
        )




async def avatar_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate, save, and send the user's Habitica avatar as a PNG image."""
    await send_avatar_photo(
        update,
        context,
        caption="Here’s your Habitica avatar ✨",
    )



async def send_panel_with_saved_avatar(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    panel_text: str,
    keyboard: InlineKeyboardMarkup | None = None,
) -> None:
    """
    Send a single message consisting of:
      - the saved avatar PNG (if available) as the photo
      - `panel_text` as the caption
      - `keyboard` as the inline keyboard

    If no avatar PNG is cached yet, fall back to a normal text message with the same keyboard.
    """
    chat_id = update.effective_chat.id

    png_path = context.user_data.get("AVATAR_PNG_PATH")
    if png_path and os.path.exists(png_path):
        try:
            with open(png_path, "rb") as img:
                msg = await topic_send(
                    update,
                    context.bot.send_photo,
                    chat_id=chat_id,
                    photo=img,
                    caption=panel_text,
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )

            # Cache file_id for inline avatar usage
            if msg.photo:
                context.user_data["AVATAR_FILE_ID"] = msg.photo[-1].file_id

            return
        except Exception as e:
            logging.warning(
                "Failed to send avatar photo, falling back to text-only panel: %s", e
            )

    # Fallback: no cached avatar => just send the text panel as before
    await topic_send(
        update,
        context.bot.send_message,
        chat_id=chat_id,
        text=panel_text,
        parse_mode="HTML",
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )




# Assume get_status is defined elsewhere
# def get_status(user_id, api_key): ...

async def get_status_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /status command."""
    chat_id = update.effective_chat.id

    # Force /status to forget any cached pin info for this chat
    pinned_key = f"pinned_status_message_id_{chat_id}"
    text_key = f"pinned_status_text_{chat_id}"
    context.user_data.pop(pinned_key, None)
    context.user_data.pop(text_key, None)
    logging.info("STATUS DEBUG: cleared pinned status cache for chat %s", chat_id)

    # Send the temporary message and capture its message object
    temp_message = await update.message.reply_text("Updating status...")

    # Call the helper, passing the IDs of the messages to delete
    await update_and_pin_status(
        context,
        chat_id=chat_id,
        user_command_message_id=update.message.message_id,
        bot_status_message_id=temp_message.message_id
    )



async def buy_potion_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Buys a potion and then updates the pinned status message."""
    user_id = context.user_data.get("USER_ID")
    api_key = context.user_data.get("API_KEY")
    if not user_id or not api_key:
        await update.message.reply_text("Use /start to set USER_ID and API_KEY first.")
        return

    ok = buy_potion(user_id, api_key)
    if ok:
        # Send the temporary message and capture its message object
        temp_message = await update.message.reply_text("Potion bought! Updating status...")

        # Call the helper, passing the IDs of the messages to delete
        await update_and_pin_status(
            context,
            chat_id=update.effective_chat.id,
            user_command_message_id=update.message.message_id,
            bot_status_message_id=temp_message.message_id
        )
    else:
        await update.message.reply_text("Failed to buy potion.")


async def debug_commands(update, context):
    d = await context.bot.get_my_commands(scope=BotCommandScopeDefault())
    p = await context.bot.get_my_commands(scope=BotCommandScopeAllPrivateChats())
    g = await context.bot.get_my_commands(scope=BotCommandScopeAllGroupChats())
    await update.message.reply_text(
        "Default: " + ", ".join(c.command for c in d) + "\n" +
        "Private: " + ", ".join(c.command for c in p) + "\n" +
        "Groups: " + ", ".join(c.command for c in g)
    )

async def inline_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles both the initial menu and direct task list queries."""
    query = update.inline_query
    if not query:
        return

    user_id = context.user_data.get("USER_ID")
    api_key = context.user_data.get("API_KEY")

    if not user_id or not api_key:
        results = [
            InlineQueryResultArticle(
                id='auth_error',
                title='❌ Not Authenticated',
                input_message_content=InputTextMessageContent(
                    message_text="Please /start this bot in a private chat to link your Habitica account first."
                )
            )
        ]
        await query.answer(results, cache_time=0)
        return

    query_type = query.query.lower().strip()

    valid_types_map = {
        "habits": "habits",
        "dailys": "dailys",
        "todos": "todos",
        "rewards": "rewards",
        "completedtodos": "completedTodos",  # normalize camelCase
    }
    normalized_type = valid_types_map.get(query_type)

    # Accept any of the keys the user can type (all-lowercase)
    valid_types = list(valid_types_map.keys())

    # Get current status to include in messages
    status_data = get_status(user_id, api_key)
    stats = status_data.get("stats", {}) if status_data else {}
    status_text = build_status_block(stats)

    # --- Empty inline query: only "Show my options" ---
    # --- Empty inline query: "Show my options" ---
    # --- Empty inline query: just show a text launcher (no avatar in popup) ---
    # --- Empty inline query: "Inline shortcuts" with avatar DOCUMENT if we have it ---
    if not query_type:
        results = []
        avatar_doc_id = context.user_data.get("AVATAR_DOC_FILE_ID")

        if avatar_doc_id:
            # Use the avatar as a cached DOCUMENT so the shortcuts menu message
            # has the avatar file + caption + shortcuts keyboard.
            caption = "<b>Inline shortcuts</b>\n\n" + status_text

            results.append(
                InlineQueryResultCachedDocument(
                    id="show_options_with_avatar_doc",
                    title="Inline shortcuts",
                    document_file_id=avatar_doc_id,
                    description="Open quick picker (Habits / Dailys / Todos / Rewards / Completed)",
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=build_inline_launcher_kb(),
                )
            )
        else:
            # Fallback: old text-only behaviour if we don't yet have a cached avatar doc
            results.append(
                InlineQueryResultArticle(
                    id="show_options",
                    title="Show my options",
                    description="Open quick picker (Habits / Dailys / Todos / Rewards / Completed)",
                    input_message_content=InputTextMessageContent("Inline shortcuts:"),
                    reply_markup=build_inline_launcher_kb(),
                )
            )

        await query.answer(results, cache_time=0, is_personal=True)
        return


    if not normalized_type:
        # User typed something that's not a known type
        await query.answer([], cache_time=10)
        return

    if query_type not in valid_types:
        await query.answer([], cache_time=10)
        return

    tasks = get_tasks(user_id, api_key, task_type=normalized_type)

    if not tasks:
        results = [
            InlineQueryResultArticle(
                id='no_tasks',
                title=f'No {query_type.title()} Found',
                input_message_content=InputTextMessageContent(
                    message_text=f"You have no {query_type} to display."
                )
            )
        ]
        await query.answer(results, cache_time=0)
        return

    results: list = []


    # ------------------------------------------------------------------
    # "ALL" PANEL ARTICLE AT TOP, USING THE SAME LAYOUT AS /habits,/dailys,/todos,/rewards
    # ------------------------------------------------------------------
    if normalized_type == "habits":
        MAX_LABEL_LEN = 18
        rows: list[list[InlineKeyboardButton]] = []

        for h in tasks:
            full_text = h.get("text", "(no title)")
            counter_up = int(h.get("counterUp", 0))
            counter_down = int(h.get("counterDown", 0))

            up = bool(h.get("up", False))
            down = bool(h.get("down", False))
            task_id = h.get("id")
            if not task_id:
                continue

            short = full_text if len(full_text) <= MAX_LABEL_LEN else full_text[:MAX_LABEL_LEN - 1] + "…"

            row: list[InlineKeyboardButton] = []
            if down:
                row.append(
                    InlineKeyboardButton(
                        f"➖({counter_down}) {short}",
                        callback_data=f"hMenu:down:{task_id}",
                    )
                )
            if up:
                row.append(
                    InlineKeyboardButton(
                        f"➕({counter_up}) {short}",
                        callback_data=f"hMenu:up:{task_id}",
                    )
                )
            if row:
                rows.append(row)

        # Habits panels always use the "full" layout (one footer button per row)
        layout_mode = "full"

        # Footer row: refresh list + buy potion + refresh‑day
        append_standard_footer(rows, "habits", layout_mode=layout_mode)
        habits_markup = InlineKeyboardMarkup(rows) if rows else None

        # Use shared behaviour for panel text
        cfg = PANEL_BEHAVIOUR["habits"]
        panel_text = build_tasks_panel_text(
            kind="habits",
            tasks=tasks,
            status_text=status_text,
            show_status=cfg["show_status"],
            show_list=cfg["show_list"],
            list_first=cfg["list_first"],
            layout_mode=layout_mode,
        )

        # ---- ALL HABITS INLINE RESULT ----
        # Try to use cached DOCUMENT avatar if we have it, otherwise fall back to text article.
        avatar_doc_id = context.user_data.get("AVATAR_DOC_FILE_ID")

        if avatar_doc_id:
            # Document tile in popup, document bubble + caption in chat
            results.append(
                InlineQueryResultCachedDocument(
                    id="panel_habits_all_doc",
                    title="🌀 All Habits",
                    document_file_id=avatar_doc_id,
                    description="Send a Habits panel with +/- buttons",
                    caption=panel_text,
                    parse_mode="HTML",
                    reply_markup=habits_markup,
                )
            )
        else:
            # Fallback: text-only article (no avatar) if we haven't cached doc yet
            results.append(
                InlineQueryResultArticle(
                    id="panel_habits_all",
                    title="🌀 All Habits",
                    description="Send a Habits panel with +/- buttons",
                    input_message_content=InputTextMessageContent(
                        message_text=panel_text,
                        parse_mode="HTML",
                    ),
                    reply_markup=habits_markup,
                )
            )






    elif normalized_type == "dailys":
        dailys = tasks  # you already set tasks = get_tasks(...) above

        layout_mode = context.user_data.get("d_menu_layout", "full")

        # Status for the panel
        status_data = get_status(user_id, api_key) or {}
        stats = status_data.get("stats", {}) or {}
        status_html = build_status_block(stats)

        # Build panel text (here: list + status, status under the list)
        cfg = PANEL_BEHAVIOUR["dailys"]
        panel_text = build_tasks_panel_text(
            kind="dailys",
            tasks=dailys,
            status_text=status_html,
            show_status=cfg["show_status"],
            show_list=cfg["show_list"],
            list_first=cfg["list_first"],
            layout_mode=layout_mode,
        )

        # Keyboard: one helper, no manual footer here
        dailys_markup = build_dailys_panel_keyboard(dailys, layout_mode)

        avatar_doc_id = context.user_data.get("AVATAR_DOC_FILE_ID")

        if avatar_doc_id:
            # Document tile in popup, document bubble + caption in chat
            results.append(
                InlineQueryResultCachedDocument(
                    id="panel_dailys_all_doc",
                    title="📅 All Dailies",
                    document_file_id=avatar_doc_id,
                    description="Send a Dailies panel with ✔ / ✖ buttons",
                    caption=panel_text,
                    parse_mode="HTML",
                    reply_markup=dailys_markup,
                )
            )
        else:
            # Fallback: text-only article (no avatar) if we haven't cached doc yet
            results.append(
                InlineQueryResultArticle(
                    id="panel_dailys_all",
                    title="📅 All Dailies",
                    description="Send a Dailies panel with ✔ / ✖ buttons",
                    input_message_content=InputTextMessageContent(
                        message_text=panel_text,
                        parse_mode="HTML",
                    ),
                    reply_markup=dailys_markup,
                )
            )









    elif normalized_type == "todos":
        todos = tasks  # you already set tasks = get_tasks(...) above
        layout_mode = context.user_data.get("t_menu_layout", "full")

        # Status for the panel
        status_data = get_status(user_id, api_key) or {}
        stats = status_data.get("stats", {}) or {}
        status_html = build_status_block(stats)

        # --- Text: again, use the same config as /todos
        cfg = PANEL_BEHAVIOUR["todos"]
        panel_text = build_tasks_panel_text(
            kind="todos",
            tasks=todos,
            status_text=status_html,
            show_status=cfg["show_status"],
            show_list=cfg["show_list"],
            list_first=cfg["list_first"],
            layout_mode=layout_mode,
        )

        # --- Keyboard: same layout + footer as /todos
        buttons_with_len: list[tuple[InlineKeyboardButton, int]] = []
        MAX_LABEL_LEN = 28
        for t in todos:
            full_text = t.get("text", "(no title)")
            short = (
                full_text
                if len(full_text) <= MAX_LABEL_LEN
                else full_text[:MAX_LABEL_LEN - 1] + "…"
            )

            is_completed = bool(t.get("completed", False))
            icon = "✔️" if is_completed else "✖️"
            action = "down" if is_completed else "up"

            task_id = t.get("id")
            if not task_id:
                continue

            label = f"{icon} {short}"
            btn = InlineKeyboardButton(label, callback_data=f"tMenu:{action}:{task_id}")
            buttons_with_len.append((btn, len(label)))

        rows = layout_buttons_for_mode(buttons_with_len, layout_mode)
        append_standard_footer(rows, "todos", layout_mode, include_potion=True)
        todos_markup = InlineKeyboardMarkup(rows)

        avatar_doc_id = context.user_data.get("AVATAR_DOC_FILE_ID")

        if avatar_doc_id:
            results.append(
                InlineQueryResultCachedDocument(
                    id="panel_todos_all_doc",
                    title="📝 All Todos",
                    document_file_id=avatar_doc_id,
                    description="Send a Todos panel with ✔ / ✖ buttons",
                    caption=panel_text,
                    parse_mode="HTML",
                    reply_markup=todos_markup,
                )
            )
        else:
            results.append(
                InlineQueryResultArticle(
                    id="panel_todos_all",
                    title="📝 All Todos",
                    description="Send a Todos panel with ✔ / ✖ buttons",
                    input_message_content=InputTextMessageContent(
                        message_text=panel_text,
                        parse_mode="HTML",
                    ),
                    reply_markup=todos_markup,
                )
            )






    elif normalized_type == "completedTodos":
        completed = tasks  # tasks already fetched via get_tasks(...)

        layout_mode = context.user_data.get("c_menu_layout", "full")

        # Status (same as /completedTodos panel)
        status_data = get_status(user_id, api_key) or {}
        stats = status_data.get("stats", {}) or {}
        status_html = build_status_block(stats)

        cfg = PANEL_BEHAVIOUR["completedTodos"]
        panel_text = build_tasks_panel_text(
            kind="completedTodos",
            tasks=completed,
            status_text=status_html,
            show_status=cfg["show_status"],
            show_list=cfg["show_list"],
            list_first=cfg["list_first"],
            layout_mode=layout_mode,
        )

        # Buttons (same layout as /completedTodos)
        buttons_with_len: list[tuple[InlineKeyboardButton, int]] = []
        MAX_LABEL_LEN = 28

        for t in completed:
            full_text = t.get("text", "(no title)")
            short = (
                full_text
                if len(full_text) <= MAX_LABEL_LEN
                else full_text[: MAX_LABEL_LEN - 1] + "…"
            )

            is_completed = bool(t.get("completed", True))
            icon = "↩️" if is_completed else "✖️"
            action = "down" if is_completed else "up"

            task_id = t.get("id")
            if not task_id:
                continue

            label = f"{icon} {short}"
            btn = InlineKeyboardButton(
                label,
                callback_data=f"cMenu:{action}:{task_id}",
            )
            buttons_with_len.append((btn, len(label)))

        rows = layout_buttons_for_mode(buttons_with_len, layout_mode)
        append_standard_footer(rows, "completedTodos", layout_mode, include_potion=True)
        completed_markup = InlineKeyboardMarkup(rows)

        avatar_doc_id = context.user_data.get("AVATAR_DOC_FILE_ID")

        if avatar_doc_id:
            results.append(
                InlineQueryResultCachedDocument(
                    id="panel_completed_all_doc",
                    title="✅ All Completed Todos",
                    document_file_id=avatar_doc_id,
                    description="Send a panel of completed Todos (tap to un-complete).",
                    caption=panel_text,
                    parse_mode="HTML",
                    reply_markup=completed_markup,
                )
            )
        else:
            results.append(
                InlineQueryResultArticle(
                    id="panel_completed_all",
                    title="✅ All Completed Todos",
                    description="Send a panel of completed Todos (tap to un-complete).",
                    input_message_content=InputTextMessageContent(
                        message_text=panel_text,
                        parse_mode="HTML",
                    ),
                    reply_markup=completed_markup,
                )
            )








    elif normalized_type == "rewards":
        buttons_with_len: list[tuple[InlineKeyboardButton, int]] = []
        MAX_LABEL_LEN = 24

        for t in tasks:
            full_text = t.get("text", "(no title)")
            short = full_text if len(full_text) <= MAX_LABEL_LEN else full_text[:MAX_LABEL_LEN - 1] + "…"
            value = int(t.get("value", 0) or 0)
            task_id = t.get("id")
            if not task_id:
                continue

            label = f"{short} ({value}g)"
            btn = InlineKeyboardButton(label, callback_data=f"rMenu:buy:{task_id}")
            buttons_with_len.append((btn, len(label)))

        layout_mode = context.user_data.get("r_menu_layout", "full")
        rows = layout_buttons_for_mode(buttons_with_len, layout_mode)
        append_standard_footer(rows, "rewards", layout_mode)

        lines = ["<b>💰 Rewards</b>"]
        if layout_mode != "full":
            lines.append("Tap a reward to buy it.")
        if status_text:
            lines.append(status_text)

        cfg = PANEL_BEHAVIOUR["rewards"]

        panel_text = build_tasks_panel_text(
            kind="rewards",
            tasks=tasks,
            status_text=status_text,
            show_status=cfg["show_status"],
            show_list=cfg["show_list"],
            list_first=cfg["list_first"],
            layout_mode=layout_mode,
        )

        rewards_markup = InlineKeyboardMarkup(rows)
        avatar_doc_id = context.user_data.get("AVATAR_DOC_FILE_ID")

        if avatar_doc_id:
            # Document tile in popup, document bubble + caption in chat
            results.append(
                InlineQueryResultCachedDocument(
                    id="panel_rewards_all_doc",
                    title="💰 All Rewards",
                    document_file_id=avatar_doc_id,
                    description="Send a Rewards panel with buy buttons",
                    caption=panel_text,
                    parse_mode="HTML",
                    reply_markup=rewards_markup,
                )
            )
        else:
            # Fallback: text-only article if we don't yet have a cached doc id
            results.append(
                InlineQueryResultArticle(
                    id="panel_rewards_all",
                    title="💰 All Rewards",
                    description="Send a Rewards panel with buy buttons",
                    input_message_content=InputTextMessageContent(
                        message_text=panel_text,
                        parse_mode="HTML",
                    ),
                    reply_markup=rewards_markup,
                )
            )

    # ------------------------------------------------------------------
    # Per-task inline results (single cards, with avatar document if we have it)
    # ------------------------------------------------------------------
    avatar_doc_id = context.user_data.get("AVATAR_DOC_FILE_ID")

    for task in tasks[:10]:
        task_text = html.escape(task.get("text", "(no title)"))
        task_id = task.get("id")
        if not task_id:
            continue

        reply_markup = None
        message_content = task_text
        title_prefix = "🔹"

        if normalized_type == "habits":
            up = task.get("up", False)
            down = task.get("down", False)
            counter_up = task.get("counterUp", 0)
            counter_down = task.get("counterDown", 0)

            keyboard: list[InlineKeyboardButton] = []
            if up:
                keyboard.append(
                    InlineKeyboardButton(
                        f"➕ {counter_up}",
                        callback_data=f"habits:up:{task_id}:{counter_up}:{up}:{down}",
                    )
                )
            if down:
                keyboard.append(
                    InlineKeyboardButton(
                        f"➖ {counter_down}",
                        callback_data=f"habits:down:{task_id}:{counter_down}:{up}:{down}",
                    )
                )
            reply_markup = InlineKeyboardMarkup([keyboard]) if keyboard else None

            formatted_task_text = f"<blockquote>🌀<b><i>{task_text}</i></b></blockquote>"
            message_content = f"{formatted_task_text}\n{status_text}"
            title_prefix = "🌀"

        elif normalized_type in ["dailys", "todos", "completedTodos"]:
            is_completed = task.get("completed", False)
            button_text = "✔️" if is_completed else "✖️"
            action = "down" if is_completed else "up"
            keyboard = [[InlineKeyboardButton(button_text, callback_data=f"{normalized_type}:{action}:{task_id}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            if normalized_type == "dailys":
                formatted_task_text = f"<blockquote>📅 <b><i>{task_text}</i></b></blockquote>"
                title_prefix = "📅"
            elif normalized_type == "todos":
                formatted_task_text = f"<blockquote>📝 <b><i>{task_text}</i></b></blockquote>"
                title_prefix = "📝"
            else:  # completedTodos
                formatted_task_text = f"<blockquote>✅ <b><i>{task_text}</i></b></blockquote>"
                title_prefix = "✅"

            message_content = f"{formatted_task_text}\n{status_text}"

        elif normalized_type == "rewards":
            value = task.get("value", 0)
            keyboard = [[InlineKeyboardButton(f"Buy ({value} Gold)", callback_data=f"rewards:buy:{task_id}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            formatted_task_text = f"<blockquote>💰 <b><i>{task_text}</i></b></blockquote>"
            message_content = f"{formatted_task_text}\n{status_text}"
            title_prefix = "💰"

        title = f"{title_prefix} {task_text}"

        if avatar_doc_id:
            # Send avatar PNG as document + caption with the task & buttons
            results.append(
                InlineQueryResultCachedDocument(
                    id=f"{normalized_type}_doc_{task_id}",
                    title=title,
                    document_file_id=avatar_doc_id,
                    description=task_text,
                    caption=message_content,
                    parse_mode="HTML",
                    reply_markup=reply_markup,
                )
            )
        else:
            # Fallback: old text-only behaviour
            results.append(
                InlineQueryResultArticle(
                    id=f"{normalized_type}_art_{task_id}",
                    title=title,
                    description=task_text,
                    input_message_content=InputTextMessageContent(
                        message_text=message_content,
                        parse_mode="HTML",
                    ),
                    reply_markup=reply_markup,
                )
            )

    await query.answer(results, cache_time=10)




async def menu_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Reuse the improved refresh-menu behavior
    return await refresh_menu_command_handler(update, context)



async def completed_todos_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_completed_todos_menu(update, context)


async def habits_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_habits_menu(update, context)

async def dailys_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_dailys_menu(update, context)

async def todos_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_todos_menu(update, context)

async def rewards_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_rewards_menu(update, context)


async def task_list_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /task_list with 'short' and 'long' modes."""
    user_id = context.user_data.get("USER_ID")
    api_key = context.user_data.get("API_KEY")
    if not user_id or not api_key:
        await update.message.reply_text("Use /start to set USER_ID and API_KEY first.")
        return

    args = context.args
    task_type = (args[0] if args else "todos").lower()
    mode = (args[1] if len(args) > 1 else "short").lower()

    # Updated to include completedTodos
    valid_types_map = {
        "habits": "habits",
        "dailys": "dailys",
        "todos": "todos",
        "rewards": "rewards",
        "completedtodos": "completedTodos",  # Added completedTodos
        "all": "all"
    }
    task_type = valid_types_map.get(task_type)
    if not task_type:
        await update.message.reply_text(f"Invalid type. Use: {', '.join(valid_types_map.keys())}")
        return

    if mode not in ['short', 'long']:
        await update.message.reply_text("Invalid mode. Use 'short' or 'long'.")
        return

    # --- Fetch tasks ---
    tasks_to_process = []
    if task_type == "all":
        types_to_fetch = ["habits", "dailys", "todos", "rewards", "completedTodos"]
        for t_type in types_to_fetch:
            tasks = get_tasks(user_id, api_key, task_type=t_type)
            if tasks:
                tasks_to_process.extend(tasks)
    else:
        tasks = get_tasks(user_id, api_key, task_type=task_type)
        if tasks:
            tasks_to_process = tasks

    if not tasks_to_process:
        await update.message.reply_text(f"No '{task_type}' tasks found.")
        return

    # --- Choose the correct formatter ---
    if mode == 'long':
        # Use the new interactive formatter
        await format_and_send_interactive_tasks(update, context, tasks_to_process)
    else:
        # Use the improved short formatter
        response_text = format_standard_tasks(tasks_to_process)
        await update.message.reply_text(response_text, parse_mode='HTML')


def format_standard_tasks(tasks: list) -> str:
    """Formats tasks with professional styling for each task type."""
    # Group tasks by type for better organization
    task_groups = {
        'habit': [], 'daily': [], 'todo': [], 'reward': []
    }
    for task in tasks:
        task_groups.get(task.get('type'), []).append(task)

    # Build the formatted message
    sections = []

    # Format habits
    if task_groups['habit']:
        sections.append("<b>🌀 HABITS</b>")
        for task in task_groups['habit']:
            task_text = html.escape(task.get('text', '(no title)'))
            counter_up = task.get('counterUp', 0)
            counter_down = task.get('counterDown', 0)
            up = task.get('up', False)
            down = task.get('down', False)

            # Create counter display
            counters = []
            if up: counters.append(f"➕ {counter_up}")
            if down: counters.append(f"➖ {counter_down}")
            counter_display = " | ".join(counters) if counters else ""

            # Format the habit line
            habit_line = f"• <i>{task_text}</i>"
            if counter_display:
                habit_line += f" <code>({counter_display})</code>"
            sections.append(habit_line)
        sections.append("")  # Add empty line after section

    # Format dailies
    if task_groups['daily']:
        sections.append("<b>📅 DAILIES</b>")
        for task in task_groups['daily']:
            task_text = html.escape(task.get('text', '(no title)'))
            is_completed = task.get('completed', False)
            streak = task.get('streak', 0)

            # Format based on completion status
            if is_completed:
                daily_line = f"• <s>{task_text}</s> <code>✓</code>"
            else:
                daily_line = f"• <b>{task_text}</b> <code>✗</code>"

            # Add streak if it exists
            if streak > 0:
                daily_line += f" <code>🔥 {streak}</code>"

            sections.append(daily_line)
        sections.append("")  # Add empty line after section

    # Format todos
    if task_groups['todo']:
        sections.append("<b>📝 TODOS</b>")
        for task in task_groups['todo']:
            task_text = html.escape(task.get('text', '(no title)'))
            is_completed = task.get('completed', False)

            # Format based on completion status
            if is_completed:
                todo_line = f"• <s>{task_text}</s> <code>✓</code>"
            else:
                todo_line = f"• <b>{task_text}</b> <code>✗</code>"

            sections.append(todo_line)
        sections.append("")  # Add empty line after section

    # Format rewards
    if task_groups['reward']:
        sections.append("<b>💰 REWARDS</b>")
        for task in task_groups['reward']:
            task_text = html.escape(task.get('text', '(no title)'))
            value = task.get('value', 0)

            # Format with value
            reward_line = f"• <i>{task_text}</i> <code>({value} Gold)</code>"
            sections.append(reward_line)

    # Join all sections with newlines
    return "\n".join(sections)


async def format_and_send_interactive_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE, tasks: list):
    """Sends interactive messages for dailies, todos, and rewards."""
    chat_id = update.effective_chat.id

    # Group tasks by type for potential future summaries
    task_groups = {'habit': [], 'daily': [], 'todo': [], 'reward': []}
    for task in tasks:
        task_groups.get(task.get('type'), []).append(task)

    # Send habits using the existing function
    if task_groups['habit']:
        await format_and_send_habits(update, context, task_groups['habit'])

    # Send other tasks
    for task_type in ['daily', 'todo', 'reward']:
        if not task_groups[task_type]:
            continue

        for task in task_groups[task_type]:
            task_text = html.escape(task.get('text', '(no title)'))

            if task_type == "daily":
                task_text = f"📅 <b><i>{task_text}</i></b>"
                api_type = "dailys"
            elif task_type == "todo":
                task_text = f"📝 <b><i>{task_text}</i></b>"
                api_type = "todos"
            else:  # reward
                task_text = f"💰 <b><i>{task_text}</i></b>"
                api_type = "rewards"

            task_id = task.get('id')
            is_completed = task.get('completed', False)

            # Determine button text and action
            if is_completed:
                button_text = "✔️"
                action = "down"  # Clicking to un-complete
            else:
                button_text = "✖️"
                action = "up"  # Clicking to complete

            # Rewards are not scoreable, so no button
            if task_type == 'reward':
                reply_markup = None
            else:
                keyboard = [[InlineKeyboardButton(button_text, callback_data=f"{api_type}:{action}:{task_id}")]]
                reply_markup = InlineKeyboardMarkup(keyboard)

            await topic_send(
                update,
                context.bot.send_message,
                chat_id=chat_id,
                text=task_text,
                reply_markup=reply_markup,
                parse_mode='HTML',
            )




def _topic_thread_id(update: "Update") -> Optional[int]:
    """
    Return the forum topic thread id for this update, or None if not in a topic.

    IMPORTANT: We skip thread_id==1 (General topic). For General, omit message_thread_id.
    """
    msg = update.effective_message
    if not msg:
        return None

    # Only treat message_thread_id as a forum-topic id if this is a topic message
    if not getattr(msg, "is_topic_message", False):
        return None

    tid = getattr(msg, "message_thread_id", None)
    if not tid:
        return None

    # General topic is special; sending with message_thread_id=1 can error.
    if tid == 1:
        return None

    return tid


async def topic_send(
    update: "Update",
    send_func: Callable[..., Awaitable[Any]],
    /,
    *args,
    **kwargs,
):
    """
    Wrapper around bot.send_* methods that keeps the response inside the same forum topic.
    Only injects message_thread_id when sending to the same chat as the update.
    """
    tid = _topic_thread_id(update)

    # Only inject if caller is sending to the current chat (avoid DMs, other chats, etc.)
    chat_id = kwargs.get("chat_id")
    eff_chat = update.effective_chat
    if tid is not None and eff_chat is not None and chat_id == eff_chat.id:
        kwargs.setdefault("message_thread_id", tid)

    return await send_func(*args, **kwargs)





async def format_and_send_habits(update: Update, context: ContextTypes.DEFAULT_TYPE, habits: list):
    """Sends a summary and then individual interactive messages for each habit."""
    chat_id = update.effective_chat.id

    total_positive = sum(h.get('counterUp', 0) for h in habits)
    total_negative = sum(h.get('counterDown', 0) for h in habits)

    # 1) Send the summary message
    summary_text = (
        f"<b>📊 Habit Summary</b>\n"
        f"➕ <code>Total Positive: {total_positive}</code> | "
        f"➖ <code>Total Negative: {total_negative}</code>"
    )

    await topic_send(
        update,
        context.bot.send_message,
        chat_id=chat_id,
        text=summary_text,
        parse_mode='HTML',
    )

    # 2) Send a message for each habit with its keyboard
    for task in habits:
        task_text = html.escape(task.get('text', '(no title)'))
        task_text = f"🌀 <b><i>{task_text}</i></b>"

        task_id = task.get('id')
        up = task.get('up', False)
        down = task.get('down', False)
        counter_up = task.get('counterUp', 0)
        counter_down = task.get('counterDown', 0)

        keyboard = []
        if up:
            keyboard.append(
                InlineKeyboardButton(
                    f"➕ {counter_up}",
                    callback_data=f"habits:up:{task_id}:{counter_up}:{up}:{down}"
                )
            )
        if down:
            keyboard.append(
                InlineKeyboardButton(
                    f"➖ {counter_down}",
                    callback_data=f"habits:down:{task_id}:{counter_down}:{up}:{down}"
                )
            )

        reply_markup = InlineKeyboardMarkup([keyboard]) if keyboard else None

        await topic_send(
            update,
            context.bot.send_message,
            chat_id=chat_id,
            text=task_text,
            reply_markup=reply_markup,
            parse_mode='HTML',
        )

def extract_stats_from_score_response(score_data: Optional[dict]) -> dict:
    """
    Try to extract user stats from Habitica's ScoreTask response.

    We handle a few possibilities:
      - data['user']['stats']
      - data['stats']
      - top-level hp/mp/gp/exp
    """
    if not isinstance(score_data, dict):
        return {}

    # Most likely: {"user": {"stats": {...}}, ...}
    user = score_data.get("user")
    if isinstance(user, dict):
        stats = user.get("stats")
        if isinstance(stats, dict):
            return stats

    # Sometimes stats might be at the top level or under "stats"
    stats = score_data.get("stats")
    if isinstance(stats, dict):
        return stats

    hp = score_data.get("hp")
    mp = score_data.get("mp")
    gp = score_data.get("gp")
    exp = score_data.get("exp")

    out = {}
    if isinstance(hp, (int, float)):
        out["hp"] = hp
    if isinstance(mp, (int, float)):
        out["mp"] = mp
    if isinstance(gp, (int, float)):
        out["gp"] = gp
    if isinstance(exp, (int, float)):
        out["exp"] = exp
    return out



def get_old_and_new_stats_for_scored_task(
    user_id: str,
    api_key: str,
    task_id: str,
    direction: str,
) -> tuple[dict, dict, Optional[dict]]:
    """
    Convenience helper:
      - fetch /user before,
      - score the task,
      - try to use score response for new stats (if present),
        otherwise fall back to another /user call.
    Returns (old_stats, new_stats, raw_score_data).
    """
    # 1) Old stats
    old_status = get_status(user_id, api_key) or {}
    old_stats = old_status.get("stats", {}) or {}

    # 2) Score task
    score_data = score_task(user_id, api_key, task_id, direction)

    # 3) Try to get new stats from score response first
    new_stats = extract_stats_from_score_response(score_data or {}) or {}

    # Fallback: if we still didn't get anything useful, call /user again
    if not new_stats:
        new_status = get_status(user_id, api_key) or {}
        new_stats = new_status.get("stats", {}) or {}

    return old_stats, new_stats, score_data



def score_task(user_id: str, api_key: str, task_id: str, direction: str) -> Optional[dict]:
    """Scores a task in Habitica and returns the updated task data."""
    if direction not in ['up', 'down']:
        logging.error(f"Invalid direction '{direction}' for scoring task {task_id}.")
        return None

    headers = {
        "x-api-user": user_id,
        "x-api-key": api_key,
        "x-client": "habitica-python-3.0.0"
    }

    url = f"{HABITICA_API_URL}/tasks/{task_id}/score/{direction}"
    try:
        response = requests.post(url, headers=headers)
        response.raise_for_status()
        logging.info(f"Successfully scored task {task_id} {direction}.")
        return response.json().get('data')
    except requests.exceptions.HTTPError as e:
        # Check for the specific "session outdated" error
        if e.response.status_code == 401 or (e.response.status_code == 401 and "session is outdated" in e.response.text.lower()):
            logging.warning("Session outdated. Attempting to refresh user data.")
            # Try to refresh by getting user status, which often renews the session
            try:
                refresh_url = f"{HABITICA_API_URL}/user"
                refresh_response = requests.get(refresh_url, headers=headers)
                refresh_response.raise_for_status()
                logging.info("Session refreshed successfully. Retrying task scoring.")
                # Retry the original request
                retry_response = requests.post(url, headers=headers)
                retry_response.raise_for_status()
                logging.info(f"Successfully scored task {task_id} {direction} on retry.")
                return retry_response.json().get('data')
            except requests.exceptions.RequestException as refresh_error:
                logging.error(f"Failed to refresh session: {refresh_error}")
                return None
        else:
            # Handle other HTTP errors
            error_message = "Unknown error"
            if e.response is not None:
                try:
                    error_details = e.response.json()
                    error_message = error_details.get('message', 'No message in error response')
                except ValueError:
                    error_message = e.response.text
            logging.error(f"Failed to score task {task_id}. API Error: {error_message}")
            return None
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to score task {task_id} due to a network error: {e}")
        return None



def run_cron_for_user(user_id: str, api_key: str) -> bool:
    """Call Habitica cron (refresh the day). Returns True on success."""
    headers = {
        "x-api-user": user_id,
        "x-api-key": api_key,
        "x-client": "habitica-python-3.0.0",
        "Content-Type": "application/json",
    }
    url = f"{HABITICA_API_URL}/cron"
    try:
        resp = requests.post(url, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict):
            return bool(data.get("success", True))
        return True
    except requests.RequestException as e:
        logging.error(f"Failed to run cron: {e}")
        return False




# Make sure you have this at the top of your file
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')



async def task_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Unified callback/query handler for inline buttons and panels.

    - Adds support for 'yester' / 'yesterLayout' callbacks (used by refresh day UI)
    - Ensures panel messages that include a "Status" block get their text updated
      when tasks are scored (so the in-message HP/MP/Gold numbers change),
      not just the pinned private status message.
    - Uses query.answer(..., show_alert=False) for lightweight toasts.
    """
    query = update.callback_query
    if not query:
        return
    data = (query.data or "").strip()
    parts = data.split(":")
    # Home chat (for pinned status) should be the user's private chat (their ID)
    home_chat_id = query.from_user.id

    # --- Basic credentials check ---
    user_id = context.user_data.get("USER_ID")
    api_key = context.user_data.get("API_KEY")
    if not user_id or not api_key:
        try:
            await query.answer("❌ Link your account first with /start.", show_alert=True)
        except Exception:
            pass
        return


    # --- Small local helpers used only here ---
    def _signed(x: float) -> str:
        if abs(x) < 0.05:
            return "0.0"
        return f"{'+' if x > 0 else ''}{x:.1f}"

    def _delta_text(old_stats: dict, new_stats: dict) -> str:
        hp  = float(new_stats.get('hp',  0)) - float(old_stats.get('hp',  0))
        mp  = float(new_stats.get('mp',  0)) - float(old_stats.get('mp',  0))
        gp  = float(new_stats.get('gp',  0)) - float(old_stats.get('gp',  0))
        exp = float(new_stats.get('exp', 0)) - float(old_stats.get('exp', 0))
        parts_ = []
        if abs(hp)  >= 0.05: parts_.append(f"♥ {_signed(hp)}")
        if abs(mp)  >= 0.05: parts_.append(f"💧 {_signed(mp)}")
        if abs(gp)  >= 0.05: parts_.append(f"💰 {_signed(gp)}")
        if abs(exp) >= 0.05: parts_.append(f"📈 {_signed(exp)}")
        return ", ".join(parts_) if parts_ else "no change"

    def _fmt_stats(stats: dict) -> str:
        return (
            f"HP: {int(stats.get('hp', 0))} ♥\n"
            f"MP: {int(stats.get('mp', 0))} 💧\n"
            f"Gold: {int(stats.get('gp', 0))} 💰"
        )

    def _status_block(stats: dict) -> str:
        """Return HTML fragment used in inline panels for the status block."""
        return build_status_block(stats)

    def _replace_status_in_text(orig_text: Optional[str], new_status_html: str) -> str:
        """
        Replace only the <blockquote><b>Status</b>…</blockquote> block
        while leaving the rest of the message untouched.
        If no such block is found, append the new status at the end.
        """
        if not orig_text:
            return new_status_html

        marker = "<blockquote><b>Status</b>"
        start = orig_text.find(marker)
        if start == -1:
            # No explicit Status block – just append
            return orig_text.rstrip() + "\n\n" + new_status_html

        end = orig_text.find("</blockquote>", start)
        if end == -1:
            # Malformed block – also just append
            return orig_text.rstrip() + "\n\n" + new_status_html

        end += len("</blockquote>")  # include closing tag
        head = orig_text[:start]
        tail = orig_text[end:]

        # Keep the text before the Status block and after it, just swap the block itself
        return head.rstrip() + "\n" + new_status_html + tail

    # Default inline launcher (fallback markup)
    try:
        default_markup = build_inline_launcher_kb()
    except Exception:
        default_markup = query.message.reply_markup if query.message else None

    async def edit_here(new_text: str, markup: InlineKeyboardMarkup | None = None):
        """
        Edit the current message in-place, preserving the current keyboard by default.
        Works for:
          - normal messages (query.message exists)
          - inline messages (query.inline_message_id exists, query.message is None)
        """
        current_markup = markup
        if current_markup is None and query and query.message:
            current_markup = query.message.reply_markup
        if current_markup is None:
            current_markup = build_inline_launcher_kb()

        def _is_not_modified(err: Exception) -> bool:
            return "message is not modified" in str(err).lower()

        async def _edit_caption() -> None:
            await query.edit_message_caption(
                caption=new_text,
                parse_mode="HTML",
                reply_markup=current_markup,
            )

        async def _edit_text() -> None:
            await query.edit_message_text(
                new_text,
                parse_mode="HTML",
                reply_markup=current_markup,
                disable_web_page_preview=True,
            )

        msg = query.message

        # 1) Normal messages: we can reliably detect media vs text
        if msg is not None:
            try:
                if msg.photo or msg.video or msg.animation or msg.document:
                    await _edit_caption()
                else:
                    await _edit_text()
            except BadRequest as e:
                if _is_not_modified(e):
                    try:
                        await query.edit_message_reply_markup(reply_markup=current_markup)
                    except Exception:
                        pass
                else:
                    raise
            return

        # 2) Inline messages: query.message is missing, so we don't know if it's caption or text.
        # Try caption first (works for cached avatar doc/photo panels), then fall back to text.
        try:
            await _edit_caption()
        except BadRequest as e1:
            if _is_not_modified(e1):
                try:
                    await query.edit_message_reply_markup(reply_markup=current_markup)
                except Exception:
                    pass
                return

            try:
                await _edit_text()
            except BadRequest as e2:
                if _is_not_modified(e2):
                    try:
                        await query.edit_message_reply_markup(reply_markup=current_markup)
                    except Exception:
                        pass
                else:
                    raise

    # Whether this callback came from a private chat (useful for pinned status updates)
    chat = update.effective_chat
    is_private = bool(chat and chat.type == "private")

    # ---------------------------
    # Helper: rebuild panel and (if panel had Status block) update its text too
    # Use this wherever you rebuild a panel's keyboard (dMenu/tMenu/rMenu/hMenu/cron)
    # ---------------------------
    async def _update_panel_text_if_needed(new_stats: dict, markup: InlineKeyboardMarkup | None):
        """
        For panel-style messages (/habits, /dailys, /todos, /rewards, /completedTodos),
        rebuild the full text using build_tasks_panel_text so that the embedded Status
        block always reflects `new_stats`.

        For non-panel messages (single-task messages, inline shortcuts, etc.)
        we fall back to just updating the keyboard or appending a fresh Status
        block at the end.
        """
        panel_kind: str | None = None

        if parts:
            tag = parts[0]

            # Panel buttons (score / refresh)
            if tag == "dMenu":
                panel_kind = "dailys"
            elif tag == "tMenu":
                panel_kind = "todos"
            elif tag == "cMenu":
                panel_kind = "completedTodos"
            elif tag == "rMenu":
                panel_kind = "rewards"
            elif tag == "hMenu":
                panel_kind = "habits"
            elif tag == "panelRefresh" and len(parts) > 1:
                sub = parts[1]
                if sub in {"habits", "todos", "rewards", "completedTodos"}:
                    panel_kind = sub

            # Footer “🧪 Buy Potion” from a panel: cmd:buy_potion:<kind>
            elif tag == "cmd" and len(parts) > 2 and parts[1] == "buy_potion":
                hint = parts[2]
                if hint in {"habits", "dailys", "todos", "rewards", "completedTodos"}:
                    panel_kind = hint

        if panel_kind:
            # Map panel kind -> Habitica API type
            api_type = "completedTodos" if panel_kind == "completedTodos" else panel_kind

            try:
                tasks = get_tasks(user_id, api_key, api_type) or []
            except Exception:
                tasks = []

            # Layout mode per panel (same keys you already use elsewhere)
            if panel_kind == "dailys":
                layout_mode = context.user_data.get("d_menu_layout", "full")
            elif panel_kind == "todos":
                layout_mode = context.user_data.get("t_menu_layout", "full")
            elif panel_kind == "completedTodos":
                layout_mode = context.user_data.get("c_menu_layout", "full")
            elif panel_kind == "rewards":
                layout_mode = context.user_data.get("r_menu_layout", "full")
            else:  # habits
                layout_mode = "full"

            status_html = _status_block(new_stats)
            cfg = PANEL_BEHAVIOUR.get(
                panel_kind,
                {"show_status": True, "show_list": True, "list_first": False},
            )

            panel_text = build_tasks_panel_text(
                kind=panel_kind,
                tasks=tasks,
                status_text=status_html,
                show_status=cfg.get("show_status", True),
                show_list=cfg.get("show_list", True),
                list_first=cfg.get("list_first", False),
                layout_mode=layout_mode,
            )

            # If no explicit markup passed, try to reuse the existing keyboard
            if markup is None and query and query.message:
                markup = query.message.reply_markup

            await edit_here(panel_text, markup)
            return

        # --- Fallback: non-panel messages (single-task messages, inline shortcuts, etc.) ---

        # Inline messages without panel context → just update keyboard if we have one
        if query.message is None:
            if markup is not None:
                try:
                    await query.edit_message_reply_markup(reply_markup=markup)
                except Exception:
                    pass
            return

        # Non-panel normal messages: append a fresh Status block
        orig_text = query.message.text or query.message.caption or ""
        new_text = orig_text.rstrip() + "\n\n" + _status_block(new_stats)
        await edit_here(new_text, markup)


    # -------------------------------------------------------------------------
    # Now the big pattern-matching switch (existing behaviour, with fixes)
    # -------------------------------------------------------------------------
    # A) Layout toggles for Dailies / Todos / Rewards panels
    # A) Layout toggles for Dailies / Todos / Rewards panels
    # --- Layout toggles for Dailies / Todos / Completed Todos panels ---
    if parts and parts[0] == "dMenuLayout":
        # Cycle Dailies layout
        current = context.user_data.get("d_menu_layout", "full")
        new_mode = cycle_layout_mode(current)
        context.user_data["d_menu_layout"] = new_mode

        # Re‑fetch Dailies + status
        dailys = get_tasks(user_id, api_key, "dailys") or []
        status_data = get_status(user_id, api_key) or {}
        stats = status_data.get("stats", {}) or {}
        status_html = build_status_block(stats)

        # Text for /dailys + inline “All Dailys”:
        #   - header is dynamic from layout_mode
        #   - we show both list + status, list first
        cfg = PANEL_BEHAVIOUR["dailys"]
        panel_text = build_tasks_panel_text(
            kind="dailys",
            tasks=dailys,
            status_text=status_html,
            show_status=cfg["show_status"],
            show_list=cfg["show_list"],
            list_first=cfg["list_first"],
            layout_mode=new_mode,
        )

        # Keyboard for Dailies panel (uses layout_mode internally)
        keyboard = build_dailys_panel_keyboard(dailys, new_mode)

        # Edit the whole message (text + keyboard)
        try:
            await query.edit_message_text(
                panel_text,
                parse_mode="HTML",
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )
        except BadRequest as e:
            # If it's “message is not modified” just update buttons
            if "message is not modified" in str(e).lower():
                try:
                    await query.edit_message_reply_markup(reply_markup=keyboard)
                except Exception:
                    pass
            else:
                raise

        home_chat_id = context.user_data.get("HOME_CHAT_ID")
        if home_chat_id:
            await update_and_pin_status(context, home_chat_id, stats_override=stats)

        await query.answer("📅 Layout updated.", show_alert=False)
        return

    if parts and parts[0] == "tMenuLayout":
        # Rotate layout mode
        current = context.user_data.get("t_menu_layout", "full")
        new_mode = cycle_layout_mode(current)
        context.user_data["t_menu_layout"] = new_mode

        # Fresh tasks + stats
        todos = get_tasks(user_id, api_key, "todos") or []
        status_data = get_status(user_id, api_key) or {}
        stats = status_data.get("stats", {}) or {}
        status_html = build_status_block(stats)

        # Reuse the same text builder as /todos & inline "All Todos"
        cfg = PANEL_BEHAVIOUR["todos"]
        panel_text = build_tasks_panel_text(
            kind="todos",
            tasks=todos,
            status_text=status_html,
            show_status=cfg["show_status"],
            show_list=cfg["show_list"],
            list_first=cfg["list_first"],
            layout_mode=new_mode,
        )

        # Rebuild keyboard in the new layout mode, with your footer
        buttons_with_len: list[tuple[InlineKeyboardButton, int]] = []
        MAX_LABEL_LEN = 28
        for t in todos:
            full_text = t.get("text", "(no title)")
            short = full_text if len(full_text) <= MAX_LABEL_LEN else full_text[:MAX_LABEL_LEN - 1] + "…"

            is_completed = bool(t.get("completed", False))
            icon = "✔️" if is_completed else "✖️"
            action = "down" if is_completed else "up"

            task_id = t.get("id")
            if not task_id:
                continue

            label = f"{icon} {short}"
            btn = InlineKeyboardButton(label, callback_data=f"tMenu:{action}:{task_id}")
            buttons_with_len.append((btn, len(label)))

        rows = layout_buttons_for_mode(buttons_with_len, new_mode)
        append_standard_footer(rows, "todos", new_mode)
        markup = InlineKeyboardMarkup(rows)

        # Update *text + keyboard* together, just like Dailies
        try:
            await query.edit_message_text(
                panel_text,
                parse_mode="HTML",
                reply_markup=markup,
                disable_web_page_preview=True,
            )
        except BadRequest as e:
            # Typical case: "message is not modified" – just update keyboard
            if "message is not modified" in str(e).lower():
                try:
                    await query.edit_message_reply_markup(reply_markup=markup)
                except Exception:
                    pass
            else:
                # Fallback: keyboard-only update
                try:
                    await query.edit_message_reply_markup(reply_markup=markup)
                except Exception:
                    pass

        await update_and_pin_status(context, home_chat_id, stats_override=stats)
        await query.answer(f"Layout: {new_mode.title()}", show_alert=False)
        return



    if parts and parts[0] == "cMenuLayout":
        current = context.user_data.get("c_menu_layout", "full")
        new_mode = cycle_layout_mode(current)
        context.user_data["c_menu_layout"] = new_mode

        completed = get_tasks(user_id, api_key, "completedTodos") or []
        status_data = get_status(user_id, api_key) or {}
        stats = status_data.get("stats", {}) or {}
        status_html = build_status_block(stats)

        cfg = PANEL_BEHAVIOUR["completedTodos"]
        panel_text = build_tasks_panel_text(
            kind="completedTodos",
            tasks=completed,
            status_text=status_html,
            show_status=cfg["show_status"],
            show_list=cfg["show_list"],
            list_first=cfg["list_first"],
            layout_mode=new_mode,
        )

        buttons_with_len: list[tuple[InlineKeyboardButton, int]] = []
        MAX_LABEL_LEN = 28

        for t in completed:
            full_text = t.get("text", "(no title)")
            short = full_text if len(full_text) <= MAX_LABEL_LEN else full_text[:MAX_LABEL_LEN - 1] + "…"
            # these are completed by definition; you already use ↩️ to un-complete
            icon = "↩️"
            action_for_t = "down"
            tid = t.get("id")
            if not tid:
                continue
            label = f"{icon} {short}"
            btn = InlineKeyboardButton(label, callback_data=f"cMenu:{action_for_t}:{tid}")
            buttons_with_len.append((btn, len(label)))

        rows = layout_buttons_for_mode(buttons_with_len, new_mode)
        append_standard_footer(rows, "completedTodos", new_mode, include_potion=False)
        markup = InlineKeyboardMarkup(rows)

        try:
            await query.edit_message_text(
                panel_text,
                parse_mode="HTML",
                reply_markup=markup,
                disable_web_page_preview=True,
            )
        except BadRequest as e:
            if "message is not modified" in str(e).lower():
                try:
                    await query.edit_message_reply_markup(reply_markup=markup)
                except Exception:
                    pass
            else:
                try:
                    await query.edit_message_reply_markup(reply_markup=markup)
                except Exception:
                    pass

        await update_and_pin_status(context, home_chat_id, stats_override=stats)
        await query.answer(f"Layout: {new_mode.title()}", show_alert=False)
        return


    if parts and parts[0] == "rMenuLayout":
        current = context.user_data.get("r_menu_layout", "full")
        new_mode = cycle_layout_mode(current)
        context.user_data["r_menu_layout"] = new_mode

        rewards = get_tasks(user_id, api_key, "rewards") or []
        buttons_with_len = []
        MAX_LABEL_LEN = 24
        for t in rewards:
            full_text = t.get("text", "(no title)")
            short = full_text if len(full_text) <= MAX_LABEL_LEN else full_text[:MAX_LABEL_LEN - 1] + "…"
            value = int(t.get("value", 0))
            tid = t.get("id")
            if not tid:
                continue
            label = f"{short} ({value}g)"
            btn = InlineKeyboardButton(label, callback_data=f"rMenu:buy:{tid}")
            buttons_with_len.append((btn, len(label)))

        rows = layout_buttons_for_mode(buttons_with_len, new_mode)
        # ✅ keep the common footer (refresh / buy potion / refresh day + layout toggle)
        append_standard_footer(rows, "rewards", new_mode)

        try:
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(rows))
        except Exception:
            pass

        await query.answer(f"Layout: {new_mode.title()}", show_alert=False)
        return

    # --- Cron layout toggle (refresh-day panel) ---
    # --- Refresh-day layout toggle (keeps ✖/✔ style) ---
    if parts and parts[0] == "yesterLayout":
        # Cycle through full -> compact -> super
        layout_mode = context.user_data.get("cron_layout_mode", "full")
        new_mode = cycle_layout_mode(layout_mode)
        context.user_data["cron_layout_mode"] = new_mode

        cron_meta = context.user_data.get("cron_meta") or {}
        if cron_meta:
            markup = build_refresh_day_keyboard(cron_meta, new_mode)
            try:
                await query.edit_message_reply_markup(reply_markup=markup)
            except Exception:
                pass

        await query.answer(f"Layout: {new_mode.title()}", show_alert=False)
        return

    # Backwards-compat: treat 'cronLayout' the same as 'yesterLayout'
    if parts and parts[0] == "cronLayout":
        layout_mode = context.user_data.get("cron_layout_mode", "full")
        new_mode = cycle_layout_mode(layout_mode)
        context.user_data["cron_layout_mode"] = new_mode

        cron_meta = context.user_data.get("cron_meta") or {}
        if cron_meta:
            markup = build_refresh_day_keyboard(cron_meta, new_mode)
            try:
                await query.edit_message_reply_markup(reply_markup=markup)
            except Exception:
                pass

        await query.answer(f"Layout: {new_mode.title()}", show_alert=False)
        return

    # --- Cron / refresh-day: yesterday's Dailies (✖/✔ buttons) ---
    if parts and parts[0] in ("yester", "cronDaily"):
        if len(parts) < 3:
            await query.answer("⚠️ Invalid Daily button.", show_alert=True)
            return

        state = parts[1]     # "0" = unchecked, "1" = checked
        task_id = parts[2]

        currently_checked = (state == "1")
        new_checked = not currently_checked
        direction = "up" if new_checked else "down"

        # Score the daily & compute stat delta
        try:
            old_stats, new_stats, _score_data = get_old_and_new_stats_for_scored_task(
                user_id=user_id,
                api_key=api_key,
                task_id=task_id,
                direction=direction,
            )
        except Exception:
            # Fallback manual implementation
            old_status = get_status(user_id, api_key) or {}
            old_stats = old_status.get("stats", {}) if old_status else {}
            score_task(user_id, api_key, task_id, direction=direction)
            new_status = get_status(user_id, api_key) or {}
            new_stats = new_status.get("stats", {}) or {}
        delta = _delta_text(old_stats, new_stats)

        # Update local refresh-day state
        cron_meta = context.user_data.get("cron_meta") or {}
        if task_id in cron_meta:
            cron_meta[task_id]["checked"] = new_checked
            context.user_data["cron_meta"] = cron_meta

        # Rebuild the keyboard, but keep the same ✖/✔ style
        layout_mode = context.user_data.get("cron_layout_mode", "full")
        if cron_meta:
            markup = build_refresh_day_keyboard(cron_meta, layout_mode)
            try:
                await query.edit_message_reply_markup(reply_markup=markup)
            except Exception:
                pass

        # Always update pinned HUD in the user's private chat
        await update_and_pin_status(context, home_chat_id, stats_override=new_stats)

        await query.answer(
            f"📅 Daily {'marked done' if new_checked else 'un-done'} ({delta})",
            show_alert=False,
        )
        return


    # cron confirm / run / cancel
    if parts and parts[0] == "cron":
        action = parts[1] if len(parts) > 1 else ""

        # Treat either our explicit flag or the fact that this is an inline
        # callback as "inline refresh-day mode".
        from_inline_refresh = bool(context.user_data.get("cron_from_inline"))
        is_inline_refresh = from_inline_refresh or (query.inline_message_id is not None)

        if action == "run":
            # Get old status for delta and to check needsCron
            old_status = get_status(user_id, api_key)
            if not old_status:
                await query.answer("❌ Couldn't reach Habitica.", show_alert=True)
                return

            old_stats = old_status.get("stats", {}) or {}
            needs_cron = bool(old_status.get("needsCron", False))

            # No cron needed → just show current stats again
            if not needs_cron:
                feedback = "✅ Day is already refreshed (cron already ran)."

                if is_inline_refresh:
                    # Inline panel: show Status + shortcuts again
                    text = build_status_block(old_stats)
                    await edit_here(text)  # uses Inline shortcuts keyboard as default
                    context.user_data["cron_from_inline"] = False
                else:
                    text = f"<b>🔄 Day already refreshed</b>\n\n{build_status_block(old_stats)}"
                    await edit_here(text, build_inline_launcher_kb())

                await query.answer(feedback, show_alert=False)
                return

            # Actually run Habitica cron
            ok = run_cron_for_user(user_id, api_key)

            # Always re-fetch status afterwards for accurate stats
            new_status = get_status(user_id, api_key) or {}
            new_stats = new_status.get("stats", {}) or {}
            delta = _delta_text(old_stats, new_stats)

            if ok:
                feedback = f"🔄 Day refreshed ({delta})"

                if is_inline_refresh:
                    # Inline panel: turn back into "Status + shortcuts"
                    text = build_status_block(new_stats)
                    await edit_here(text, build_inline_launcher_kb())

                else:
                    text = f"<b>🔄 Day refreshed!</b>\n\n{build_status_block(new_stats)}"
                    await edit_here(text)

                # Update pinned HUD in the user's private chat
                await update_and_pin_status(
                    context, home_chat_id, stats_override=new_stats
                )
            else:
                feedback = "❌ Failed to refresh day."

                if is_inline_refresh:
                    # Inline panel: also go back to "Status + shortcuts", but with old stats
                    text = build_status_block(old_stats)
                    await edit_here(text, build_inline_launcher_kb())


                else:
                    text = f"<b>❌ Failed to refresh day.</b>\n\n{build_status_block(old_stats)}"
                    await edit_here(text)

            # Drop any cached refresh-day state so the next run starts fresh.
            context.user_data.pop("cron_meta", None)
            context.user_data["cron_from_inline"] = False
            await query.answer(feedback, show_alert=not ok)
            return

        if action == "cancel":
            # Close refresh-day UI and return to the inline shortcut menu everywhere.
            #
            # NOTE: use edit_here so it works for BOTH text messages and avatar-photo
            # messages (where the content lives in the caption).
            try:
                await edit_here("🎯 Quick Commands:", build_inline_launcher_kb())
            except Exception:
                # Last-resort fallback: at least replace the text/caption.
                try:
                    await edit_here("❌ Refresh cancelled.")
                except Exception:
                    pass

            # Drop any cached refresh-day state so the next open starts fresh.
            context.user_data.pop("cron_meta", None)
            context.user_data["cron_from_inline"] = False

            # Best-effort: keep the pinned HUD in the user's private chat in sync.
            try:
                current = get_status(user_id, api_key) or {}
                stats = current.get("stats", {}) or {}
                await update_and_pin_status(context, home_chat_id, stats_override=stats)
            except Exception:
                pass

            await query.answer("❌ Refresh cancelled.", show_alert=False)
            return

    # quick cmd actions (cmd:status / cmd:buy_potion)
    # quick cmd actions (cmd:status / cmd:buy_potion / cmd:refresh_day)
    # quick cmd actions (cmd:status / cmd:buy_potion / cmd:refresh_day)
    # quick cmd actions (cmd:status / cmd:buy_potion / cmd:refresh_day)
    if parts and parts[0] == "cmd":
        # home_chat_id = query.from_user.id (defined earlier in the function)
        if len(parts) > 1 and parts[1] == "status":
            data_status = get_status(user_id, api_key) or {}
            stats = data_status.get("stats", {}) or {}
            text = build_status_block(stats)


            await edit_here(text)
            await update_and_pin_status(context, home_chat_id, stats_override=stats)
            await query.answer("Status shown.", show_alert=False)
            return

        if len(parts) > 1 and parts[1] == "avatar":
            # Re-render and send the user's avatar (photo + document) in this chat
            await avatar_command_handler(update, context)
            try:
                await query.answer("Avatar updated.", show_alert=False)
            except Exception:
                pass
            return

        if len(parts) > 1 and parts[1] == "buy_potion":
            old = get_status(user_id, api_key) or {}
            old_stats = old.get("stats", {}) if old else {}

            ok = buy_potion(user_id, api_key)

            new = get_status(user_id, api_key) or {}
            new_stats = new.get("stats", {}) if new else {}
            delta = _delta_text(old_stats, new_stats)

            # Optional panel hint: habits/todos/rewards/completedTodos/dailys
            panel_hint = parts[2] if len(parts) > 2 else None

            # 🔹 Case A: plain "cmd:buy_potion" from the inline shortcut menu
            # (the button in build_inline_launcher_kb)
            if len(parts) == 2:
                # Inline shortcut menu inserted into chats (inline_message_id exists)
                if query.inline_message_id is not None:
                    text = "<b>Inline shortcuts</b>\n\n" + _status_block(new_stats)
                    await edit_here(text, build_inline_launcher_kb())
                else:
                    # Private launcher message ("🎯 Quick Commands:") → keep it unchanged.
                    # Optional: if the message already contains a Status block, refresh it in-place:
                    if query.message:
                        orig = query.message.text or query.message.caption or ""
                        if "<blockquote><b>Status</b>" in orig:
                            await edit_here(
                                _replace_status_in_text(orig, _status_block(new_stats)),
                                query.message.reply_markup,
                            )


            # 🔹 Case B: "cmd:buy_potion:<panel_hint>" from a panel footer,
            #            in an INLINE message (habits/dailys/todos/rewards panels)
            elif query.inline_message_id is not None and panel_hint in {"habits", "dailys", "todos", "rewards"}:
                # Rebuild the proper panel keyboard so we keep the same UI
                if panel_hint == "habits":
                    habits = get_tasks(user_id, api_key, "habits") or []
                    rows = []
                    MAX_LABEL_LEN = 18
                    for h in habits:
                        full_text = h.get("text", "(no title)")
                        short = full_text if len(full_text) <= MAX_LABEL_LEN else full_text[:MAX_LABEL_LEN - 1] + "…"
                        up_flag = bool(h.get("up", False))
                        down_flag = bool(h.get("down", False))
                        counter_up = int(h.get("counterUp", 0))
                        counter_down = int(h.get("counterDown", 0))
                        hid = h.get("id")
                        if not hid:
                            continue
                        row = []
                        if down_flag:
                            row.append(
                                InlineKeyboardButton(
                                    f"➖({counter_down}) {short}",
                                    callback_data=f"hMenu:down:{hid}",
                                )
                            )
                        if up_flag:
                            row.append(
                                InlineKeyboardButton(
                                    f"➕({counter_up}) {short}",
                                    callback_data=f"hMenu:up:{hid}",
                                )
                            )
                        if row:
                            rows.append(row)
                    layout_mode = "full"
                    append_standard_footer(rows, "habits", layout_mode=layout_mode)
                    # no layout toggle row for habits

                    markup = InlineKeyboardMarkup(rows)
                    header = "<b>🌀 Your Habits</b>"

                elif panel_hint == "dailys":
                    dailys = get_tasks(user_id, api_key, "dailys") or []
                    buttons_with_len = []
                    MAX_LABEL_LEN = 28
                    for t in dailys:
                        full_text = t.get("text", "(no title)")
                        short = full_text if len(full_text) <= MAX_LABEL_LEN else full_text[:MAX_LABEL_LEN - 1] + "…"
                        is_c = bool(t.get("completed", False))
                        icon = "✔️" if is_c else "✖️"
                        action_for_t = "down" if is_c else "up"
                        tid = t.get("id")
                        if not tid:
                            continue
                        label = f"{icon} {short}"
                        btn = InlineKeyboardButton(label, callback_data=f"dMenu:{action_for_t}:{tid}")
                        buttons_with_len.append((btn, len(label)))

                    layout_mode = context.user_data.get("d_menu_layout", "full")
                    rows = layout_buttons_for_mode(buttons_with_len, layout_mode)
                    append_standard_footer(rows, "dailys", layout_mode)
                    markup = InlineKeyboardMarkup(rows)
                    header = "<b>📅 Your Dailies</b>"

                elif panel_hint == "todos":
                    todos = get_tasks(user_id, api_key, "todos") or []
                    buttons_with_len = []
                    MAX_LABEL_LEN = 28
                    for t in todos:
                        full_text = t.get("text", "(no title)")
                        short = full_text if len(full_text) <= MAX_LABEL_LEN else full_text[:MAX_LABEL_LEN - 1] + "…"
                        is_c = bool(t.get("completed", False))
                        icon = "✔️" if is_c else "✖️"
                        action_for_t = "down" if is_c else "up"
                        tid = t.get("id")
                        if not tid:
                            continue
                        label = f"{icon} {short}"
                        btn = InlineKeyboardButton(label, callback_data=f"tMenu:{action_for_t}:{tid}")
                        buttons_with_len.append((btn, len(label)))
                    layout_mode = context.user_data.get("t_menu_layout", "full")
                    rows = layout_buttons_for_mode(buttons_with_len, layout_mode)
                    append_standard_footer(rows, "todos", layout_mode)

                    markup = InlineKeyboardMarkup(rows)
                    header = "<b>📝 Your Todos</b>"

                else:  # rewards
                    rewards = get_tasks(user_id, api_key, "rewards") or []
                    buttons_with_len = []
                    MAX_LABEL_LEN = 24
                    for t in rewards:
                        full_text = t.get("text", "(no title)")
                        short = full_text if len(full_text) <= MAX_LABEL_LEN else full_text[:MAX_LABEL_LEN - 1] + "…"
                        value = int(t.get("value", 0))
                        tid = t.get("id")
                        if not tid:
                            continue
                        label = f"{short} ({value}g)"
                        btn = InlineKeyboardButton(label, callback_data=f"rMenu:buy:{tid}")
                        buttons_with_len.append((btn, len(label)))
                    layout_mode = context.user_data.get("r_menu_layout", "full")
                    rows = layout_buttons_for_mode(buttons_with_len, layout_mode)
                    append_standard_footer(rows, "rewards", layout_mode)

                    markup = InlineKeyboardMarkup(rows)
                    header = "<b>💰 Rewards</b>"

                # Inline message: update header+status text + keep same keyboard
                await _update_panel_text_if_needed(new_stats, markup)


            # 🔹 Case C: panel footer in a normal chat message
            else:
                await _update_panel_text_if_needed(
                    new_stats,
                    query.message.reply_markup if query.message else None,
                )

            # Always keep the pinned HUD in sync in the user’s private chat
            await update_and_pin_status(context, home_chat_id, stats_override=new_stats)

            await query.answer(
                f"🧪 {'Potion bought' if ok else 'Potion failed'} ({delta})",
                show_alert=False,
            )
            return


    if len(parts) > 1 and parts[1] == "refresh_day":
        # "Refresh day" button – used from panels (/dailys, /habits, /todos, …)
        # and from the inline shortcut menu.
        if not user_id or not api_key:
            await edit_here(
                "Use /start to set USER_ID and API_KEY first.",
                build_inline_launcher_kb(),
            )
            await query.answer(show_alert=True)
            return

        if debug:
            logging.info(
                "Opening refresh-day menu from callback for user %s (inline=%s)",
                user_id,
                bool(query.inline_message_id),
            )

        # Always fetch a fresh list of yesterday’s unfinished Dailies
        cron_meta = fetch_cron_meta(user_id, api_key)
        if cron_meta is None:
            await edit_here(
                "⚠️ Could not reach Habitica. Please try again.",
                build_inline_launcher_kb(),
            )
            await query.answer(show_alert=True)
            return

        context.user_data["cron_meta"] = cron_meta
        layout_mode = context.user_data.get("cron_layout_mode", "full")

        # Remember whether this came from an inline message so cron:run/cancel
        # knows whether to restore the Status + shortcuts panel afterwards.
        context.user_data["cron_from_inline"] = bool(query.inline_message_id)

        lines: list[str] = []
        if cron_meta:
            lines.append(
                "<b>These Dailies were due yesterday and are still unchecked:</b>"
            )
            for meta in cron_meta.values():
                text = html.escape(meta.get("text", "(no title)"))
                lines.append(f"• {text}")
            lines.append("")
            lines.append(
                "Tap the buttons below to mark what you actually did yesterday,"
            )
            lines.append("then press <b>“Refresh day now”</b>.")
        else:
            lines.append(
                "No unfinished Dailies from yesterday were found.\n"
                "You can safely refresh your day now."
            )

        keyboard = build_refresh_day_keyboard(cron_meta, layout_mode)

        try:
            # Works for both text and photo+caption messages
            await edit_here("\n".join(lines), keyboard)
            await query.answer()
        except Exception as e:
            logging.exception(
                "Failed to edit message to refresh-day panel: %s",
                e,
            )
        return


    # -------------------------------------------------------------------------
    # Panel refresh (no scoring, just re-sync from Habitica)
    # -------------------------------------------------------------------------
    if parts and parts[0] == "panelRefresh":
        kind = parts[1] if len(parts) > 1 else ""

        # Fetch fresh stats once
        status_data = get_status(user_id, api_key) or {}
        stats = status_data.get("stats", {}) or {}

        # ----- Dailys panel (keeps the inline summary text with ⬤/◯) -----
        if kind == "dailys":
            dailys = get_tasks(user_id, api_key, "dailys") or []
            layout_mode = context.user_data.get("d_menu_layout", "full")
            cfg = PANEL_BEHAVIOUR["dailys"]

            # Panel text built from your config
            status_html = build_status_block(stats)
            panel_text = build_tasks_panel_text(
                kind="dailys",
                tasks=dailys,
                status_text=status_html,
                show_status=cfg["show_status"],
                show_list=cfg["show_list"],
                list_first=cfg["list_first"],
                layout_mode=layout_mode,
            )

            # Keyboard for the panel
            dailys_markup = build_dailys_panel_keyboard(dailys, layout_mode)

            try:
                await query.edit_message_text(
                    panel_text,
                    parse_mode="HTML",
                    reply_markup=dailys_markup,
                    disable_web_page_preview=True,
                )
            except BadRequest as e:
                # If only the text didn't change, at least refresh the keyboard
                if "message is not modified" in str(e).lower():
                    try:
                        await query.edit_message_reply_markup(reply_markup=dailys_markup)
                    except Exception:
                        pass
                else:
                    try:
                        await query.edit_message_reply_markup(reply_markup=dailys_markup)
                    except Exception:
                        pass

            await update_and_pin_status(context, home_chat_id, stats_override=stats)
            await query.answer("📅 Dailies refreshed.", show_alert=False)
            return


        # ----- Todos panel -----
        elif kind == "todos":
            todos = get_tasks(user_id, api_key, "todos") or []
            buttons_with_len = []
            MAX_LABEL_LEN = 28

            for t in todos:
                full_text = t.get("text", "(no title)")
                short = full_text if len(full_text) <= MAX_LABEL_LEN else full_text[:MAX_LABEL_LEN - 1] + "…"
                is_c = bool(t.get("completed", False))
                icon = "✔️" if is_c else "✖️"
                action_for_t = "down" if is_c else "up"
                tid = t.get("id")
                if not tid:
                    continue
                label = f"{icon} {short}"
                btn = InlineKeyboardButton(label, callback_data=f"tMenu:{action_for_t}:{tid}")
                buttons_with_len.append((btn, len(label)))

            layout_mode = context.user_data.get("t_menu_layout", "full")
            rows = layout_buttons_for_mode(buttons_with_len, layout_mode)
            append_standard_footer(rows, "todos", layout_mode)

            markup = InlineKeyboardMarkup(rows) if rows else None

            await _update_panel_text_if_needed(stats, markup)
            await update_and_pin_status(context, home_chat_id, stats_override=stats)
            await query.answer("📝 Todos refreshed.", show_alert=False)
            return

        # ----- Completed Todos panel -----
        elif kind == "completedTodos":
            completed = get_tasks(user_id, api_key, "completedTodos") or []
            buttons_with_len = []
            MAX_LABEL_LEN = 28

            for t in completed:
                full_text = t.get("text", "(no title)")
                short = full_text if len(full_text) <= MAX_LABEL_LEN else full_text[:MAX_LABEL_LEN - 1] + "…"
                icon = "↩️"  # your existing pattern
                action_for_t = "down"
                tid = t.get("id")
                if not tid:
                    continue
                label = f"{icon} {short}"
                btn = InlineKeyboardButton(label, callback_data=f"cMenu:{action_for_t}:{tid}")
                buttons_with_len.append((btn, len(label)))

            layout_mode = context.user_data.get("c_menu_layout", "full")
            rows = layout_buttons_for_mode(buttons_with_len, layout_mode)
            append_standard_footer(rows, "completedTodos", layout_mode)

            markup = InlineKeyboardMarkup(rows) if rows else None

            await _update_panel_text_if_needed(stats, markup)
            await update_and_pin_status(context, home_chat_id, stats_override=stats)
            await query.answer("✅ Completed todos refreshed.", show_alert=False)
            return

        # ----- Rewards panel -----
        elif kind == "rewards":
            rewards = get_tasks(user_id, api_key, "rewards") or []
            buttons_with_len = []
            MAX_LABEL_LEN = 24

            for t in rewards:
                full_text = t.get("text", "(no title)")
                short = full_text if len(full_text) <= MAX_LABEL_LEN else full_text[:MAX_LABEL_LEN - 1] + "…"
                value = int(t.get("value", 0))
                tid = t.get("id")
                if not tid:
                    continue
                label = f"{short} ({value}g)"
                btn = InlineKeyboardButton(label, callback_data=f"rMenu:buy:{tid}")
                buttons_with_len.append((btn, len(label)))

            layout_mode = context.user_data.get("r_menu_layout", "full")
            rows = layout_buttons_for_mode(buttons_with_len, layout_mode)
            append_standard_footer(rows, "rewards", layout_mode)

            markup = InlineKeyboardMarkup(rows) if rows else None

            await _update_panel_text_if_needed(stats, markup)
            await update_and_pin_status(context, home_chat_id, stats_override=stats)
            await query.answer("💰 Rewards refreshed.", show_alert=False)
            return

        # ----- Habits panel -----
        elif kind == "habits":
            habits = get_tasks(user_id, api_key, "habits") or []
            rows = []
            MAX_LABEL_LEN = 18

            for h in habits:
                full_text = h.get("text", "(no title)")
                counter_up = int(h.get("counterUp", 0))
                counter_down = int(h.get("counterDown", 0))
                up = bool(h.get("up", False))
                down = bool(h.get("down", False))
                tid = h.get("id")
                if not tid:
                    continue

                short = full_text if len(full_text) <= MAX_LABEL_LEN else full_text[:MAX_LABEL_LEN - 1] + "…"

                row = []
                if down:
                    row.append(InlineKeyboardButton(
                        f"➖({counter_down}) {short}",
                        callback_data=f"hMenu:down:{tid}",
                    ))
                if up:
                    row.append(InlineKeyboardButton(
                        f"➕({counter_up}) {short}",
                        callback_data=f"hMenu:up:{tid}",
                    ))
                if row:
                    rows.append(row)

            layout_mode = "full"
            append_standard_footer(rows, "habits", layout_mode=layout_mode)

            markup = InlineKeyboardMarkup(rows) if rows else None

            await _update_panel_text_if_needed(stats, markup)
            await update_and_pin_status(context, home_chat_id, stats_override=stats)
            await query.answer("🌀 Habits refreshed.", show_alert=False)
            return

        # Unknown kind
        await query.answer("⚠️ Unknown panel type.", show_alert=True)
        return

    # -------------------------------------------------------------------------
    # Panels: dMenu / tMenu / rMenu / hMenu (single-panel updates)
    # For these we rebuild the keyboard and also update the message text's status block
    # -------------------------------------------------------------------------
    if len(parts) == 3 and parts[0] == "dMenu":
        _, action, task_id = parts
        if action not in ("up", "down"):
            await query.answer("⚠️ Unknown action.", show_alert=True)
            return

        # Old stats for delta
        old_status = get_status(user_id, api_key)
        old_stats = old_status.get("stats", {}) if old_status else {}

        # Score the Daily
        score_task(user_id, api_key, task_id, direction=action)
        updated_task = get_task_by_id(user_id, api_key, task_id)
        if not updated_task:
            await query.answer("❌ Failed to update Daily.", show_alert=True)
            return

        # New stats
        new_status = get_status(user_id, api_key) or {}
        new_stats = new_status.get("stats", {}) or {}
        delta = _delta_text(old_stats, new_stats)

        # Rebuild full Dailies panel (text + keyboard) from PANEL_BEHAVIOUR
        dailys = get_tasks(user_id, api_key, "dailys") or []
        layout_mode = context.user_data.get("d_menu_layout", "full")
        cfg = PANEL_BEHAVIOUR["dailys"]

        status_html = build_status_block(new_stats)
        panel_text = build_tasks_panel_text(
            kind="dailys",
            tasks=dailys,
            status_text=status_html,
            show_status=cfg["show_status"],
            show_list=cfg["show_list"],
            list_first=cfg["list_first"],
            layout_mode=layout_mode,
        )

        dailys_markup = build_dailys_panel_keyboard(dailys, layout_mode)

        # Use the generic helper so this works for both text-only and photo+caption panels
        await edit_here(panel_text, dailys_markup)

        # Keep pinned HUD in sync
        await update_and_pin_status(context, home_chat_id, stats_override=new_stats)

        await query.answer(
            f"📅 Daily {'completed' if bool(updated_task.get('completed', False)) else 'uncompleted'} ({delta})",
            show_alert=False,
        )
        return

    if len(parts) == 3 and parts[0] == "tMenu":
        _, action, task_id = parts
        if action not in ("up", "down"):
            await query.answer("⚠️ Unknown action.", show_alert=True)
            return

        old_status = get_status(user_id, api_key)
        old_stats = old_status.get("stats", {}) if old_status else {}

        score_task(user_id, api_key, task_id, direction=action)
        updated_task = get_task_by_id(user_id, api_key, task_id)
        if not updated_task:
            await query.answer("❌ Failed to update Todo.", show_alert=True)
            return

        new_status = get_status(user_id, api_key) or {}
        new_stats = new_status.get("stats", {}) or {}
        delta = _delta_text(old_stats, new_stats)

        todos = get_tasks(user_id, api_key, "todos") or []
        buttons_with_len = []
        MAX_LABEL_LEN = 28
        for t in todos:
            full_text = t.get("text", "(no title)")
            short = full_text if len(full_text) <= MAX_LABEL_LEN else full_text[:MAX_LABEL_LEN - 1] + "…"
            is_c = bool(t.get("completed", False))
            icon = "✔️" if is_c else "✖️"
            action_for_t = "down" if is_c else "up"
            tid = t.get("id")
            if not tid:
                continue
            label = f"{icon} {short}"
            btn = InlineKeyboardButton(label, callback_data=f"tMenu:{action_for_t}:{tid}")
            buttons_with_len.append((btn, len(label)))

        layout_mode = context.user_data.get("t_menu_layout", "full")
        rows = layout_buttons_for_mode(buttons_with_len, layout_mode)
        append_standard_footer(rows, "todos", layout_mode)
        markup = InlineKeyboardMarkup(rows) if rows else None

        # ✅ Rebuild the full Todos panel text using PANEL_BEHAVIOUR
        status_html = build_status_block(new_stats)
        cfg = PANEL_BEHAVIOUR["todos"]
        panel_text = build_tasks_panel_text(
            kind="todos",
            tasks=todos,
            status_text=status_html,
            show_status=cfg["show_status"],
            show_list=cfg["show_list"],
            list_first=cfg["list_first"],
            layout_mode=layout_mode,
        )

        # Works for both inline and normal chat messages
        await edit_here(panel_text, markup)

        await update_and_pin_status(context, home_chat_id, stats_override=new_stats)

        await query.answer(
            f"📝 Todo {'completed' if bool(updated_task.get('completed', False)) else 'uncompleted'} ({delta})",
            show_alert=False,
        )
        return

    if len(parts) == 3 and parts[0] == "cMenu":
        _, action, task_id = parts
        if action not in ("up", "down"):
            await query.answer("⚠️ Unknown action.", show_alert=True)
            return

        # Old stats
        old_status = get_status(user_id, api_key)
        old_stats = old_status.get("stats", {}) if old_status else {}

        # Score the todo (down = un-complete)
        score_task(user_id, api_key, task_id, direction=action)
        updated_task = get_task_by_id(user_id, api_key, task_id)
        if not updated_task:
            await query.answer("❌ Failed to update completed todo.", show_alert=True)
            return

        # New stats
        new_status = get_status(user_id, api_key) or {}
        new_stats = new_status.get("stats", {}) or {}
        delta = _delta_text(old_stats, new_stats)

        # Rebuild completed-todos panel (the just-uncompleted one will usually disappear)
        completed = get_tasks(user_id, api_key, "completedTodos") or []
        buttons_with_len = []
        MAX_LABEL_LEN = 28
        for t in completed:
            full_text = t.get("text", "(no title)")
            short = full_text if len(full_text) <= MAX_LABEL_LEN else full_text[:MAX_LABEL_LEN - 1] + "…"
            is_c = bool(t.get("completed", True))
            icon = "↩️" if is_c else "✖️"
            action_for_t = "down" if is_c else "up"
            tid = t.get("id")
            if not tid:
                continue

            label = f"{icon} {short}"
            btn = InlineKeyboardButton(label, callback_data=f"cMenu:{action_for_t}:{tid}")
            buttons_with_len.append((btn, len(label)))

        layout_mode = context.user_data.get("c_menu_layout", "full")
        rows = layout_buttons_for_mode(buttons_with_len, layout_mode)
        toggle_label = layout_toggle_label_for_mode(layout_mode)
        rows.append([InlineKeyboardButton(toggle_label, callback_data="cMenuLayout:next")])
        markup = InlineKeyboardMarkup(rows) if rows else None

        await _update_panel_text_if_needed(new_stats, markup)
        await update_and_pin_status(context, home_chat_id, stats_override=new_stats)

        # Toast
        became_completed = bool(updated_task.get("completed", False))
        msg = "completed" if became_completed else "uncompleted"
        await query.answer(f"✅ Completed todo {msg} ({delta})", show_alert=False)
        return




    if len(parts) == 3 and parts[0] == "rMenu":
        _, action, task_id = parts
        if action != "buy":
            await query.answer("⚠️ Unknown reward action.", show_alert=True)
            return

        old = get_status(user_id, api_key) or {}
        old_stats = old.get("stats", {}) if old else {}
        ok = False
        try:
            ok = buy_reward(user_id, api_key, task_id)
        except Exception:
            ok = False

        new = get_status(user_id, api_key) or {}
        new_stats = new.get("stats", {}) if new else {}
        delta = _delta_text(old_stats, new_stats)

        # rebuild rewards panel & update text if it contains status
        rewards = get_tasks(user_id, api_key, "rewards") or []
        buttons_with_len = []
        MAX_LABEL_LEN = 24
        for t in rewards:
            full_text = t.get("text", "(no title)")
            short = full_text if len(full_text) <= MAX_LABEL_LEN else full_text[:MAX_LABEL_LEN - 1] + "…"
            value = int(t.get("value", 0))
            tid = t.get("id")
            if not tid:
                continue
            label = f"{short} ({value}g)"
            btn = InlineKeyboardButton(label, callback_data=f"rMenu:buy:{tid}")
            buttons_with_len.append((btn, len(label)))

        layout_mode = context.user_data.get("r_menu_layout", "full")
        rows = layout_buttons_for_mode(buttons_with_len, layout_mode)
        append_standard_footer(rows, "rewards", layout_mode)
        markup = InlineKeyboardMarkup(rows) if rows else None

        # ✅ Rebuild the full Rewards panel text using PANEL_BEHAVIOUR
        status_html = build_status_block(new_stats)
        cfg = PANEL_BEHAVIOUR["rewards"]
        panel_text = build_tasks_panel_text(
            kind="rewards",
            tasks=rewards,
            status_text=status_html,
            show_status=cfg["show_status"],
            show_list=cfg["show_list"],
            list_first=cfg["list_first"],
            layout_mode=layout_mode,
        )

        await edit_here(panel_text, markup)

        await update_and_pin_status(context, home_chat_id, stats_override=new_stats)

        await query.answer(
            f"💰 {'Reward bought' if ok else 'Reward failed'} ({delta})",
            show_alert=not ok,
        )
        return

    # Habits panel (hMenu)
    # Habits panel (hMenu)
    if len(parts) == 3 and parts[0] == "hMenu":
        _, direction, task_id = parts
        if direction not in ("up", "down"):
            await query.answer("⚠️ Unknown habit action.", show_alert=True)
            return

        old = get_status(user_id, api_key)
        old_stats = old.get("stats", {}) if old else {}

        score_task(user_id, api_key, task_id, direction=direction)

        new = get_status(user_id, api_key) or {}
        new_stats = new.get("stats", {}) or {}
        delta = _delta_text(old_stats, new_stats)

        # Rebuild Habits panel (text + keyboard) from PANEL_BEHAVIOUR
        habits = get_tasks(user_id, api_key, "habits") or []

        rows: list[list[InlineKeyboardButton]] = []
        MAX_LABEL_LEN = 18
        for h in habits:
            full_text = h.get("text", "(no title)")
            short = full_text if len(full_text) <= MAX_LABEL_LEN else full_text[:MAX_LABEL_LEN - 1] + "…"

            up_flag = bool(h.get("up", False))
            down_flag = bool(h.get("down", False))
            counter_up = int(h.get("counterUp", 0))
            counter_down = int(h.get("counterDown", 0))
            hid = h.get("id")
            if not hid:
                continue

            row: list[InlineKeyboardButton] = []
            if down_flag:
                row.append(
                    InlineKeyboardButton(
                        f"➖({counter_down}) {short}",
                        callback_data=f"hMenu:down:{hid}",
                    )
                )
            if up_flag:
                row.append(
                    InlineKeyboardButton(
                        f"➕({counter_up}) {short}",
                        callback_data=f"hMenu:up:{hid}",
                    )
                )
            if row:
                rows.append(row)

        layout_mode = "full"  # Habits don't have a layout toggle
        append_standard_footer(rows, "habits", layout_mode=layout_mode)
        markup = InlineKeyboardMarkup(rows) if rows else None

        cfg = PANEL_BEHAVIOUR["habits"]
        status_html = build_status_block(new_stats)
        panel_text = build_tasks_panel_text(
            kind="habits",
            tasks=habits,
            status_text=status_html,
            show_status=cfg["show_status"],
            show_list=cfg["show_list"],
            list_first=cfg["list_first"],
            layout_mode=layout_mode,
        )

        # ... everything above stays the same up to panel_text = ...

        cfg = PANEL_BEHAVIOUR["habits"]
        status_html = build_status_block(new_stats)
        panel_text = build_tasks_panel_text(
            kind="habits",
            tasks=habits,
            status_text=status_html,
            show_status=cfg["show_status"],
            show_list=cfg["show_list"],
            list_first=cfg["list_first"],
            layout_mode=layout_mode,
        )

        # ✅ Use the generic helper – it will edit caption for the Habits photo panel
        await edit_here(panel_text, markup)

        await update_and_pin_status(context, home_chat_id, stats_override=new_stats)

        await query.answer(
            f"🌀 Habit {'increased' if direction == 'up' else 'decreased'} ({delta})",
            show_alert=False,
        )
        return



    # -------------------------------------------------------------------------
    # Remaining: older handlers where parts length == 6 for 'habits' detailed (backwards compat)
    # -------------------------------------------------------------------------
    if len(parts) == 6 and parts[0] == "habits":
        _, action, task_id, _cur, up_str, down_str = parts
        original_up = (up_str == "True")
        original_down = (down_str == "True")

        old = get_status(user_id, api_key)
        old_stats = old.get("stats", {}) if old else {}

        score_task(user_id, api_key, task_id, direction=action)
        updated_task = get_task_by_id(user_id, api_key, task_id)
        if not updated_task:
            await query.answer("❌ Failed to update habit.", show_alert=True)
            return

        new = get_status(user_id, api_key)
        new_stats = new.get("stats", {}) if new else {}
        delta = _delta_text(old_stats, new_stats)

        counter_up = updated_task.get('counterUp', 0)
        counter_down = updated_task.get('counterDown', 0)
        kb_row = []
        if original_up:
            kb_row.append(InlineKeyboardButton(
                f"➕ {counter_up}",
                callback_data=f"habits:up:{task_id}:{counter_up}:{original_up}:{original_down}"
            ))
        if original_down:
            kb_row.append(InlineKeyboardButton(
                f"➖ {counter_down}",
                callback_data=f"habits:down:{task_id}:{counter_down}:{original_up}:{original_down}"
            ))
        markup = InlineKeyboardMarkup([kb_row]) if kb_row else None

        task_text = html.escape(updated_task.get('text', '(no title)'))
        body = (
                f"<blockquote>🌀<b><i>{task_text}</i></b></blockquote>\n"
                + build_status_block(new_stats)
        )

        if query.message is not None:
            try:
                await query.edit_message_reply_markup(reply_markup=markup)
            except Exception:
                pass
        else:
            await edit_here(body, markup)

        # Update pinned status
        await update_and_pin_status(context, home_chat_id, stats_override=new_stats)

        await query.answer(f"🌀 Habit {'increased' if action == 'up' else 'decreased'} ({delta})", show_alert=False)
        return

    # --- Dailies / Todos / Completed & Rewards buy (fallback old style) ---
    if len(parts) == 3:
        ttype, action, task_id = parts

        if ttype in {"dailys", "todos", "completedTodos"}:
            old = get_status(user_id, api_key)
            old_stats = old.get("stats", {}) if old else {}

            score_task(user_id, api_key, task_id, direction=action)
            updated_task = get_task_by_id(user_id, api_key, task_id)
            if not updated_task:
                await query.answer("❌ Failed to update task.", show_alert=True)
                return

            new = get_status(user_id, api_key)
            new_stats = new.get("stats", {}) if new else {}
            delta = _delta_text(old_stats, new_stats)

            is_completed = updated_task.get('completed', False)
            btn = "✔️" if is_completed else "✖️"
            new_action = "down" if is_completed else "up"
            reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton(btn, callback_data=f"{ttype}:{new_action}:{task_id}")]])

            icon = "📅 " if ttype == "dailys" else ("📝 " if ttype == "todos" else "✅ ")
            task_text = html.escape(updated_task.get("text", "(no title)"))

            show_status = bool(context.user_data.get(UD_REMINDER_SHOW_STATUS, False))

            if show_status:
                # Status ON -> quoted task title + quoted status below
                body = f"<blockquote>{icon}<b>{task_text}</b></blockquote>\n{_status_block(new_stats)}"
            else:
                # Status OFF -> unquoted bold task title only
                body = f"{icon}<b>{task_text}</b>"

            # IMPORTANT: edit only once (prevents the “flash” / overwrite)
            await edit_here(body, reply_markup)

            await update_and_pin_status(context, home_chat_id, stats_override=new_stats)
            await query.answer(
                f"{icon} {ttype.replace('s', '').title()} "
                f"{'completed' if is_completed else 'uncompleted'} ({delta})",
                show_alert=False,
            )
            return

            await query.answer(f"{icon} {ttype.replace('s','').title()} {'completed' if is_completed else 'uncompleted'} ({delta})", show_alert=False)
            return

        if ttype == "rewards" and action == "buy":
            old = get_status(user_id, api_key)
            old_stats = old.get("stats", {}) if old else {}

            ok = False
            try:
                ok = buy_reward(user_id, api_key, task_id)
            except Exception:
                ok = False

            new = get_status(user_id, api_key)
            new_stats = new.get("stats", {}) if new else {}
            delta = _delta_text(old_stats, new_stats)
            text = f"<b>{'💰 Reward bought!' if ok else '❌ Reward failed.'}</b>\n\n{build_status_block(new_stats)}"

            await edit_here(text)
            await query.answer(f"💰 {'Reward bought' if ok else 'Reward failed'} ({delta})", show_alert=not ok)
            return

    # Unknown pattern -> keep the old helpful fallback but as a toast
    await query.answer("⚠️ Button not recognized (update your bot).", show_alert=True)




async def cancel_command_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    text = (
        "✅ The current action has been canceled.\n\n"
        "If you’d like to see the list of available commands, type /help."
    )
    await context.bot.send_message(
        chat_id = update.effective_chat.id,
        text = text,
        reply_to_message_id = update.effective_message.id
    )
    return ConversationHandler.END


async def update_and_pin_status(
        context: ContextTypes.DEFAULT_TYPE,
        chat_id: int,
        user_command_message_id: int = None,
        bot_status_message_id: int = None,
        stats_override: Optional[dict] = None,
):
    """
    Fetches status (or uses provided stats), edits the pinned message if possible,
    or creates a new one. Cleans up the command and temporary messages.

    Uses context.user_data to remember the pinned status message id,
    keyed by chat_id (so it works across restarts with persistence).

    NEW BEHAVIOR:
    - If the computed status_text is identical to the last one we pinned
      for this chat, and a pinned message exists, we skip editing/pinning
      entirely and just clean up temporary messages.
    """
    logging.info(f"Attempting to update pinned status for chat {chat_id}.")

    user_id = context.user_data.get("USER_ID")
    api_key = context.user_data.get("API_KEY")
    if not (user_id and api_key):
        logging.warning("Cannot update status: USER_ID or API_KEY not found.")
        return

    # --- Get stats (either from override or from Habitica /user) ---
    if stats_override is not None:
        stats = stats_override or {}
        logging.info("Using stats_override for pinned status.")
    else:
        status_data = get_status(user_id, api_key)
        if not status_data:
            logging.warning("Cannot update status: Failed to fetch data from Habitica API.")
            return
        stats = status_data.get("stats", {}) or {}

    status_text = build_status_block(stats)


    # --- Keys we use in user_data for this chat ---
    pinned_key = f"pinned_status_message_id_{chat_id}"
    text_key = f"pinned_status_text_{chat_id}"

    pinned_id = context.user_data.get(pinned_key)
    last_text = context.user_data.get(text_key)
    edit_was_successful = False

    logging.info(
        "STATUS DEBUG: entering update_and_pin_status for chat %s. "
        "pinned_id=%s, last_text_len=%s",
        chat_id,
        pinned_id,
        len(last_text) if last_text is not None else None,
    )


    # If we *already* have a pinned message AND the text didn't change,
    # skip editing/pinning completely and just clean up the temp messages.
    if pinned_id and last_text == status_text:
        logging.info(
            "Status text unchanged and pinned message exists; "
            "skipping edit/pin for chat %s.", chat_id
        )
        await _cleanup_messages(context, chat_id, user_command_message_id, bot_status_message_id)
        return

    # Try to edit existing pinned message (only if we have an ID)
    if pinned_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=pinned_id,
                text=status_text,
                parse_mode="HTML",
            )
            logging.info(
                "Successfully edited pinned message %s in chat %s.",
                pinned_id, chat_id
            )
            # Store the text we just pinned
            context.user_data[text_key] = status_text
            edit_was_successful = True

        except BadRequest as e:
            # "message is not modified" is harmless, treat as success
            if "message is not modified" in str(e).lower():
                logging.info(
                    "Status unchanged (BadRequest 'message is not modified'). "
                    "No edit needed for pinned message %s in chat %s.",
                    pinned_id, chat_id
                )
                context.user_data[text_key] = status_text
                edit_was_successful = True
            else:
                logging.warning(
                    "Could not edit pin %s in chat %s. "
                    "Will create a new one. Error: %s",
                    pinned_id, chat_id, e
                )
                context.user_data.pop(pinned_key, None)

        except TelegramError as e:
            logging.error(
                "An unexpected error occurred while editing pinned message %s "
                "in chat %s: %s",
                pinned_id, chat_id, e
            )
            context.user_data.pop(pinned_key, None)

    # If edit worked (or nothing changed), just clean up and exit
    if edit_was_successful:
        await _cleanup_messages(context, chat_id, user_command_message_id, bot_status_message_id)
        return

    # Otherwise, create & pin a new message
    logging.info(
        "No existing pin found or edit failed in chat %s. "
        "Creating and pinning a new status message.",
        chat_id,
    )
    new_message = await context.bot.send_message(
        chat_id=chat_id,
        text=status_text,
        parse_mode="HTML",
    )

    logging.info(
        "STATUS DEBUG: sent new status message %s in chat %s, now pinning it.",
        new_message.message_id,
        chat_id,
    )
    try:
        # In private chats this is fine; in groups it may fail if bot has no rights.
        await context.bot.unpin_all_chat_messages(chat_id=chat_id)
        await context.bot.pin_chat_message(
            chat_id=chat_id,
            message_id=new_message.message_id,
            disable_notification=True,
        )
        # Remember the new pin + text for this chat
        context.user_data[pinned_key] = new_message.message_id
        context.user_data[text_key] = status_text
        logging.info(
            "Successfully created and pinned new status message %s in chat %s.",
            new_message.message_id, chat_id
        )
    except TelegramError as e:
        logging.error(
            "Failed to pin new status message %s in chat %s: %s",
            new_message.message_id, chat_id, e
        )

    # Clean up temp messages at the end
    await _cleanup_messages(context, chat_id, user_command_message_id, bot_status_message_id)





async def _cleanup_messages(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_msg_id: int, bot_msg_id: int):
    """Deletes the specified messages if they exist."""
    for msg_id in filter(None, [user_msg_id, bot_msg_id]):
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except TelegramError:
            pass # Ignore errors during cleanup (e.g., message already deleted)



def run_cron(user_id: str, api_key: str) -> bool:
    """Call Habitica cron (refresh the day). Returns True on success."""
    headers = {
        "x-api-user": user_id,
        "x-api-key": api_key,
        "x-client": "habitica-python-3.0.0",
        "Content-Type": "application/json",
    }
    url = f"{HABITICA_API_URL}/cron"
    try:
        resp = requests.post(url, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict):
            return bool(data.get("success", True))
        return True
    except requests.RequestException as e:
        logging.error(f"Failed to run cron: {e}")
        return False



def fetch_cron_meta(user_id: str, api_key: str) -> dict[str, dict[str, object]] | None:
    """
    Build the 'cron_meta' dict used by the refresh-day UI.

    Keys are task ids, values are {"text": title, "checked": bool}.
    Only includes Dailies that Habitica considers candidates for
    "Record Yesterday's Activity": they were due yesterday and are still
    incomplete (yesterDaily == True and completed == False).
    """
    dailies = get_tasks(user_id, api_key, "dailys")
    if dailies is None:
        # Propagate network / API errors to the caller so it can show
        # a proper error message instead of an empty list.
        return None

    cron_meta: dict[str, dict[str, object]] = {}

    for task in dailies:
        # Habitica marks Record-Yesterday-Activity candidates with yesterDaily=True.
        if not task.get("yesterDaily", False):
            continue
        # Already checked off yesterday – nothing to recover.
        if task.get("completed", False):
            continue

        tid = task.get("id")
        if not tid:
            continue

        cron_meta[tid] = {
            "text": task.get("text", "(no title)"),
            "checked": False,
        }

    return cron_meta





async def open_refresh_day_menu_for_chat(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
) -> None:
    """
    Build and send the refresh-day menu **with avatar** if available.
    """
    user_data = context.user_data
    user_id = user_data.get("USER_ID")
    api_key = user_data.get("API_KEY")

    if not user_id or not api_key:
        await topic_send(
            update,
            context.bot.send_message,
            chat_id=chat_id,
            text="Use /start to set USER_ID and API_KEY first.",
        )
        return

    # Fetch Dailies and build the same panel as the inline cron UI
    # Fetch Dailies and build the same panel as the inline cron UI.
    # We rely on Habitica's own `yesterDaily` flag instead of guessing
    # from `isDue`, so the list matches the official Record Yesterday's
    # Activity screen.
    cron_meta = fetch_cron_meta(user_id, api_key) or {}

    context.user_data["cron_meta"] = cron_meta
    layout_mode = context.user_data.get("cron_layout_mode", "full")
    context.user_data["cron_layout_mode"] = layout_mode
    context.user_data["cron_from_inline"] = False  # comes from command

    # Build panel lines (same style as inline branch)
    lines: list[str] = []
    if cron_meta:
        lines.append(
            "<b>These Dailies were due yesterday and are still unchecked:</b>"
        )
        for meta in cron_meta.values():
            text = html.escape(meta.get("text", "(no title)"))
            lines.append(f"• {text}")
        lines.append("")
        lines.append(
            "Tap the buttons below to mark what you actually did yesterday,"
        )
        lines.append("then press <b>“Refresh day now”</b>.")
    else:
        lines.append(
            "No unfinished Dailies from yesterday were found.\n"
            "You can safely refresh your day now."
        )

    panel_text = "\n".join(lines)
    keyboard = build_refresh_day_keyboard(cron_meta, layout_mode)

    # --- Try to send with avatar PNG ------------------------------------------
    try:
        png_path = await ensure_avatar_png(update, context, force_refresh=False)
    except Exception as e:
        logging.warning("ensure_avatar_png failed for refresh-day menu: %s", e)
        png_path = context.user_data.get("AVATAR_PNG_PATH")

    if png_path and os.path.exists(png_path):
        try:
            with open(png_path, "rb") as img:
                msg = await topic_send(
                    update,
                    context.bot.send_photo,
                    chat_id=chat_id,
                    photo=img,
                    caption=panel_text,
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )
            if msg.photo:
                context.user_data["AVATAR_FILE_ID"] = msg.photo[-1].file_id
            context.user_data["AVATAR_PNG_PATH"] = png_path
            return
        except Exception as e:
            logging.warning(
                "Failed to send refresh-day menu with avatar, falling back to text: %s",
                e,
            )

    # Fallback: text-only refresh-day menu
    await topic_send(
        update,
        context.bot.send_message,
        chat_id=chat_id,
        text=panel_text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )



async def reminder_status_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Control whether reminder messages include the Status block in the caption.

    Usage:
      /reminder_status on
      /reminder_status off
      /reminder_status toggle
    """
    arg = (context.args[0].lower().strip() if getattr(context, "args", None) else "")

    if arg in ("on", "yes", "true", "1", "enable", "enabled"):
        context.user_data[UD_REMINDER_SHOW_STATUS] = True
        msg = "✅ Status in reminders: ON"
    elif arg in ("off", "no", "false", "0", "disable", "disabled"):
        context.user_data[UD_REMINDER_SHOW_STATUS] = False
        msg = "✅ Status in reminders: OFF"
    elif arg in ("toggle", "switch"):
        cur = bool(context.user_data.get(UD_REMINDER_SHOW_STATUS, False))
        context.user_data[UD_REMINDER_SHOW_STATUS] = not cur
        msg = f"✅ Status in reminders: {'ON' if not cur else 'OFF'}"
    else:
        cur = bool(context.user_data.get(UD_REMINDER_SHOW_STATUS, False))
        msg = (
            f"Status in reminders is currently: {'ON' if cur else 'OFF'}\n\n"
            "Use:\n"
            "/reminder_status on\n"
            "/reminder_status off"
        )

    # Reply in the same topic if this was run inside a forum topic
    await topic_send(
        update,
        context.bot.send_message,
        chat_id=update.effective_chat.id,
        text=msg,
    )





async def refresh_day_command_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    /refresh_day command entry-point.

    In private chats it opens the menu here.
    In groups/channels it tells the user that the menu is only available in
    private and opens it there.
    """
    chat = update.effective_chat
    user = update.effective_user

    if not chat or not user:
        return

    if chat.type == "private":
        target_chat_id = chat.id
    else:
        # Tell the user (IN THE SAME TOPIC) and open the menu in their private chat
        await topic_send(
            update,
            context.bot.send_message,
            chat_id=chat.id,
            text=(
                "The refresh-day menu only opens in our private chat. "
                "I'll open it there for you."
            ),
        )
        target_chat_id = user.id

    await open_refresh_day_menu_for_chat(update, context, target_chat_id)




async def show_dailys_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show all Dailies as one big button panel with layout toggle."""
    user_id = context.user_data.get("USER_ID")
    api_key = context.user_data.get("API_KEY")
    if not user_id or not api_key:
        await update.message.reply_text("Use /start to set USER_ID and API_KEY first.")
        return

    dailys = get_tasks(user_id, api_key, "dailys") or []
    if not dailys:
        await update.message.reply_text("📅 You have no Dailies.")
        return

    layout_mode = context.user_data.get("d_menu_layout", "full")

    # Fresh status for the block
    status_data = get_status(user_id, api_key) or {}
    stats = status_data.get("stats", {}) or {}
    status_html = build_status_block(stats)

    # Use the shared text builder
    cfg = PANEL_BEHAVIOUR["dailys"]
    panel_text = build_tasks_panel_text(
        kind="dailys",
        tasks=dailys,
        status_text=status_html,
        show_status=cfg["show_status"],
        show_list=cfg["show_list"],
        list_first=cfg["list_first"],
        layout_mode=layout_mode,
    )

    # Keyboard (buttons) – reuse your panel keyboard helper
    keyboard = build_dailys_panel_keyboard(dailys, layout_mode)

    # NEW: send as avatar+caption panel (like /habits)
    await send_panel_with_saved_avatar(
        update=update,
        context=context,
        panel_text=panel_text,
        keyboard=keyboard,
    )



async def show_todos_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show all Todos as one big button panel with layout toggle."""
    user_id = context.user_data.get("USER_ID")
    api_key = context.user_data.get("API_KEY")
    if not user_id or not api_key:
        await update.message.reply_text("Use /start to set USER_ID and API_KEY first.")
        return

    todos = get_tasks(user_id, api_key, "todos") or []
    if not todos:
        await update.message.reply_text("📝 You have no Todos.")
        return

    layout_mode = context.user_data.get("t_menu_layout", "full")

    # Fresh status
    status_data = get_status(user_id, api_key) or {}
    stats = status_data.get("stats", {}) or {}
    status_html = build_status_block(stats)

    # --- Text: use global behaviour config for "todos"
    cfg = PANEL_BEHAVIOUR["todos"]
    panel_text = build_tasks_panel_text(
        kind="todos",
        tasks=todos,
        status_text=status_html,
        show_status=cfg["show_status"],
        show_list=cfg["show_list"],
        list_first=cfg["list_first"],
        layout_mode=layout_mode,
    )

    # --- Keyboard: same as before, just using layout_mode
    buttons_with_len: list[tuple[InlineKeyboardButton, int]] = []
    MAX_LABEL_LEN = 28

    for t in todos:
        full_text = t.get("text", "(no title)")
        short = (
            full_text
            if len(full_text) <= MAX_LABEL_LEN
            else full_text[:MAX_LABEL_LEN - 1] + "…"
        )

        is_completed = bool(t.get("completed", False))
        icon = "✔️" if is_completed else "✖️"
        action = "down" if is_completed else "up"

        task_id = t.get("id")
        if not task_id:
            continue

        label = f"{icon} {short}"
        btn = InlineKeyboardButton(label, callback_data=f"tMenu:{action}:{task_id}")
        buttons_with_len.append((btn, len(label)))

    rows = layout_buttons_for_mode(buttons_with_len, layout_mode)
    append_standard_footer(rows, "todos", layout_mode, include_potion=True)

    keyboard = InlineKeyboardMarkup(rows)

    # NEW: send as avatar+caption panel (like /habits)
    await send_panel_with_saved_avatar(
        update=update,
        context=context,
        panel_text=panel_text,
        keyboard=keyboard,
    )




async def add_todo_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Entry point for /add_todo or the reply-keyboard button.
    Asks the user for the todo title.
    """

    logging.info(
        "add_todo_start chat=%s thread=%s user=%s",
        update.effective_chat.id if update.effective_chat else None,
        getattr(update.effective_message, "message_thread_id", None),
        update.effective_user.id if update.effective_user else None,
    )

    user_id = context.user_data.get("USER_ID")
    api_key = context.user_data.get("API_KEY")

    # Clear any stale partial state from a previous interrupted run
    context.user_data.pop("new_todo_title", None)

    if not user_id or not api_key:
        await topic_send(
            update,
            context.bot.send_message,
            chat_id=update.effective_chat.id,
            text="Use /start to set USER_ID and API_KEY first.",
        )
        return ConversationHandler.END

    await topic_send(
        update,
        context.bot.send_message,
        chat_id = update.effective_chat.id,
        text = "Send me the title of your new To‑Do.\n\nYou can /cancel to abort.",
        )
    return ADD_TODO_TITLE


async def add_todo_title_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    We got the title. Store it and ask for difficulty via inline buttons.
    """
    title = (update.message.text or "").strip()
    if not title:
        await topic_send(
            update,
            context.bot.send_message,
            context.bot.send_message,
            text="Please send a non‑empty title, or /cancel.",
        )
        return ADD_TODO_TITLE

    context.user_data["new_todo_title"] = title

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🟢 Trivial", callback_data="addTodoDifficulty:trivial"),
            InlineKeyboardButton("🟢 Easy",    callback_data="addTodoDifficulty:easy"),
        ],
        [
            InlineKeyboardButton("🟠 Medium",  callback_data="addTodoDifficulty:medium"),
            InlineKeyboardButton("🔴 Hard",    callback_data="addTodoDifficulty:hard"),
        ],
    ])

    await topic_send(
        update,
        context.bot.send_message,
        chat_id=update.effective_chat.id,
        text=f"Title:\n<b>{html.escape(title)}</b>\n\nChoose difficulty:",
        parse_mode="HTML",
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )
    return ADD_TODO_DIFFICULTY


async def add_todo_difficulty_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle difficulty button press, create the todo, update status, end conversation.
    """
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    parts = data.split(":")
    difficulty_key = parts[1] if len(parts) > 1 else "medium"

    title = context.user_data.get("new_todo_title")
    user_id = context.user_data.get("USER_ID")
    api_key = context.user_data.get("API_KEY")

    if not (user_id and api_key and title):
        await query.edit_message_text("❌ Something went wrong. Please try /add_todo again.")
        return ConversationHandler.END

    priority_map = {
        "trivial": 0.1,
        "easy": 1.0,
        "medium": 1.5,
        "hard": 2.0,
    }
    priority = priority_map.get(difficulty_key, 1.0)

    task = create_todo_task(user_id, api_key, title, priority)

    if not task:
        await query.edit_message_text("❌ Failed to create todo in Habitica. Please try again later.")
        return ConversationHandler.END

    # Clean up stored title
    context.user_data.pop("new_todo_title", None)

    difficulty_label_map = {
        "trivial": "Trivial",
        "easy": "Easy",
        "medium": "Medium",
        "hard": "Hard",
    }
    difficulty_label = difficulty_label_map.get(difficulty_key, "Easy")

    text = (
        "✅ New To‑Do created:\n"
        f"<b>{html.escape(title)}</b>\n"
        f"<i>Difficulty:</i> {difficulty_label}"
    )

    try:
        await query.edit_message_text(text, parse_mode="HTML")
    except Exception:
        # Fallback if editing fails
        await topic_send(
            update,
            context.bot.send_message,
            chat_id=query.message.chat_id,
            text=text,
            parse_mode="HTML",
        )
        # Update pinned status in the user's private chat (like other actions)
    try:
        await update_and_pin_status(context, chat_id=query.from_user.id)
    except Exception as e:
        logging.warning("Failed to update status after add_todo: %s", e)

    return ConversationHandler.END




async def show_completed_todos_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show completed Todos as one big button panel with layout toggle."""
    user_id = context.user_data.get("USER_ID")
    api_key = context.user_data.get("API_KEY")
    if not user_id or not api_key:
        await update.message.reply_text("Use /start to set USER_ID and API_KEY first.")
        return

    completed = get_tasks(user_id, api_key, "completedTodos") or []
    if not completed:
        await update.message.reply_text("✅ You have no completed Todos.")
        return

    layout_mode = context.user_data.get("c_menu_layout", "full")

    # Status (only used if cfg["show_status"] is True)
    status_data = get_status(user_id, api_key) or {}
    stats = status_data.get("stats", {}) or {}
    status_html = build_status_block(stats)

    cfg = PANEL_BEHAVIOUR["completedTodos"]

    panel_text = build_tasks_panel_text(
        kind="completedTodos",
        tasks=completed,
        status_text=status_html,
        show_status=cfg["show_status"],
        show_list=cfg["show_list"],
        list_first=cfg["list_first"],
        layout_mode=layout_mode,
    )

    # Build keyboard: same layout you already had for completed todos
    buttons_with_len: list[tuple[InlineKeyboardButton, int]] = []
    MAX_LABEL_LEN = 28
    for t in completed:
        full_text = t.get("text", "(no title)")
        short = (
            full_text
            if len(full_text) <= MAX_LABEL_LEN
            else full_text[:MAX_LABEL_LEN - 1] + "…"
        )

        is_completed = bool(t.get("completed", True))
        icon = "↩️" if is_completed else "✖️"
        action = "down" if is_completed else "up"

        task_id = t.get("id")
        if not task_id:
            continue

        label = f"{icon} {short}"
        btn = InlineKeyboardButton(label, callback_data=f"cMenu:{action}:{task_id}")
        buttons_with_len.append((btn, len(label)))

    rows = layout_buttons_for_mode(buttons_with_len, layout_mode)
    append_standard_footer(rows, "completedTodos", layout_mode, include_potion=True)
    keyboard = InlineKeyboardMarkup(rows)

    # NEW: send avatar + caption instead of a plain text message
    await send_panel_with_saved_avatar(
        update=update,
        context=context,
        panel_text=panel_text,
        keyboard=keyboard,
    )


async def show_rewards_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show all Rewards as one big button panel with layout toggle."""
    user_id = context.user_data.get("USER_ID")
    api_key = context.user_data.get("API_KEY")
    if not user_id or not api_key:
        await update.message.reply_text("Use /start to set USER_ID and API_KEY first.")
        return

    rewards = get_tasks(user_id, api_key, "rewards") or []
    if not rewards:
        await update.message.reply_text("💰 You have no custom Rewards.")
        return

    layout_mode = context.user_data.get("r_menu_layout", "full")

    # Status (used if cfg["show_status"] is True)
    status_data = get_status(user_id, api_key) or {}
    stats = status_data.get("stats", {}) or {}
    status_html = build_status_block(stats)

    cfg = PANEL_BEHAVIOUR["rewards"]

    panel_text = build_tasks_panel_text(
        kind="rewards",
        tasks=rewards,
        status_text=status_html,
        show_status=cfg["show_status"],
        show_list=cfg["show_list"],
        list_first=cfg["list_first"],
        layout_mode=layout_mode,
    )

    # Build keyboard: same rewards buttons as before
    buttons_with_len: list[tuple[InlineKeyboardButton, int]] = []
    MAX_LABEL_LEN = 24
    for t in rewards:
        full_text = t.get("text", "(no title)")
        short = (
            full_text
            if len(full_text) <= MAX_LABEL_LEN
            else full_text[:MAX_LABEL_LEN - 1] + "…"
        )
        value = int(t.get("value", 0))
        task_id = t.get("id")
        if not task_id:
            continue

        label = f"{short} ({value}g)"
        btn = InlineKeyboardButton(label, callback_data=f"rMenu:buy:{task_id}")
        buttons_with_len.append((btn, len(label)))

    rows = layout_buttons_for_mode(buttons_with_len, layout_mode)
    append_standard_footer(rows, "rewards", layout_mode, include_potion=True)
    keyboard = InlineKeyboardMarkup(rows)

    # NEW: send avatar + caption instead of a plain text message
    await send_panel_with_saved_avatar(
        update=update,
        context=context,
        panel_text=panel_text,
        keyboard=keyboard,
    )




async def show_habits_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show all Habits as one big button panel (with avatar if available)."""
    user_id = context.user_data.get("USER_ID")
    api_key = context.user_data.get("API_KEY")
    if not user_id or not api_key:
        await update.message.reply_text("Use /start to set USER_ID and API_KEY first.")
        return

    habits = get_tasks(user_id, api_key, "habits") or []
    if not habits:
        await update.message.reply_text("🌀 You have no Habits.")
        return

    # Habits panel uses a fixed "full" layout
    layout_mode = "full"

    # Fresh status (for top-of-panel status block)
    status_data = get_status(user_id, api_key) or {}
    stats = status_data.get("stats", {}) or {}
    status_html = build_status_block(stats)

    # Shared behaviour config
    cfg = PANEL_BEHAVIOUR["habits"]

    # Build the text that will become the *caption* of the photo
    panel_text = build_tasks_panel_text(
        kind="habits",
        tasks=habits,
        status_text=status_html,
        show_status=cfg["show_status"],
        show_list=cfg["show_list"],
        list_first=cfg["list_first"],
        layout_mode=layout_mode,
    )

    # Build +/- buttons for each habit, plus the standard footer
    rows: list[list[InlineKeyboardButton]] = []
    MAX_LABEL_LEN = 18

    for h in habits:
        full_text = h.get("text", "(no title)")
        short = (
            full_text
            if len(full_text) <= MAX_LABEL_LEN
            else full_text[:MAX_LABEL_LEN - 1] + "…"
        )

        up_flag = bool(h.get("up", False))
        down_flag = bool(h.get("down", False))
        counter_up = int(h.get("counterUp", 0))
        counter_down = int(h.get("counterDown", 0))
        hid = h.get("id")
        if not hid:
            continue

        row: list[InlineKeyboardButton] = []
        if down_flag:
            row.append(
                InlineKeyboardButton(
                    f"➖({counter_down}) {short}",
                    callback_data=f"hMenu:down:{hid}",
                )
            )
        if up_flag:
            row.append(
                InlineKeyboardButton(
                    f"➕({counter_up}) {short}",
                    callback_data=f"hMenu:up:{hid}",
                )
            )
        if row:
            rows.append(row)

    append_standard_footer(rows, "habits", layout_mode=layout_mode)
    keyboard = InlineKeyboardMarkup(rows) if rows else None

    # 🔥 Key part: one single message – avatar photo (if cached) + caption + buttons
    await send_panel_with_saved_avatar(
        update=update,
        context=context,
        panel_text=panel_text,
        keyboard=keyboard,
    )



async def run_reminder_tick(application: Application) -> dict:
    """
    Runs one reminder check for ALL users stored in PicklePersistence (botdata.pkl).
    Intended to be called from a Flask /tick endpoint.
    """
    utc_now = datetime.utcnow()
    window_seconds = int(os.environ.get("REMINDER_WINDOW_SECONDS", "60"))
    window = timedelta(seconds=window_seconds)

    sent_count = 0
    users_checked = 0
    errors = 0

    # application.user_data is loaded from PicklePersistence when the app initializes
    for telegram_user_id, ud in (application.user_data or {}).items():
        try:
            if not ud.get(UD_REMINDERS_ENABLED, True):
                continue

            habitica_user_id = ud.get("USER_ID")
            habitica_api_key = ud.get("API_KEY")
            if not habitica_user_id or not habitica_api_key:
                continue

            # --- timezoneOffset caching (minutes) ---
            # Habitica stores timezoneOffset like JS getTimezoneOffset (UTC+10 is -600) :contentReference[oaicite:8]{index=8}
            tz_offset = ud.get(UD_TZ_OFFSET)
            age = int(time_mod.time()) - int(ud.get(UD_TZ_OFFSET_UPDATED_AT, 0) or 0)
            if tz_offset is None or age > 24 * 3600:
                status = get_status(habitica_user_id, habitica_api_key) or {}
                tz_offset = (status.get("preferences") or {}).get("timezoneOffset", 0)
                try:
                    ud[UD_TZ_OFFSET] = int(tz_offset)
                except Exception:
                    ud[UD_TZ_OFFSET] = 0
                ud[UD_TZ_OFFSET_UPDATED_AT] = int(time_mod.time())

            tz_offset = int(ud.get(UD_TZ_OFFSET, 0))
            now_local = utc_now - timedelta(minutes=tz_offset)
            today_local = now_local.date()

            chat_id, thread_id = _get_notify_target(int(telegram_user_id), ud)

            avatar_doc_id = ud.get("AVATAR_DOC_FILE_ID")
            avatar_png_path = ud.get("AVATAR_PNG_PATH")
            status_text_cache: str | None = None

            # Fetch tasks
            dailys = get_tasks(habitica_user_id, habitica_api_key, "dailys") or []
            todos = get_tasks(habitica_user_id, habitica_api_key, "todos") or []

            # --- DAILIES ---
            for t in dailys:
                if t.get("completed"):
                    continue
                if t.get("isDue") is False:
                    continue

                for rem in (t.get("reminders") or []):
                    if not isinstance(rem, dict):
                        continue

                    # Habitica reminders include id/startDate/time :contentReference[oaicite:9]{index=9}
                    rem_time = _parse_time_of_day(rem.get("time"))
                    if not rem_time:
                        continue

                    when = datetime.combine(today_local, rem_time)
                    if not (when <= now_local < when + window):
                        continue

                    rem_id = rem.get("id") or rem_time.strftime("%H:%M")
                    key = f"rem:dailys:{t.get('id')}:{rem_id}:{today_local.isoformat()}:{rem_time.strftime('%H:%M')}"

                    sent_map = ud.setdefault(UD_SENT_REMINDERS, {})
                    if key in sent_map:
                        continue

                    show_status = bool(ud.get(UD_REMINDER_SHOW_STATUS, False))

                    status_html = ""
                    if show_status:
                        status_data = get_status(habitica_user_id, habitica_api_key) or {}
                        stats = status_data.get("stats", {}) or {}
                        status_html = build_status_block(stats)

                    await _send_task_reminder(
                        application.bot,
                        chat_id=chat_id,
                        thread_id=thread_id,
                        normalized_type="dailys",
                        task=t,
                        user_data=ud,
                        habitica_user_id=habitica_user_id,
                        habitica_api_key=habitica_api_key,
                        status_html=status_html,
                    )

                    sent_map[key] = int(time_mod.time())
                    sent_count += 1

            # --- TODOS ---
            for t in todos:
                if t.get("completed"):
                    continue

                reminders = t.get("reminders") or []
                if reminders:
                    for rem in reminders:
                        if not isinstance(rem, dict):
                            continue

                        rem_time = _parse_time_of_day(rem.get("time"))
                        if not rem_time:
                            continue

                        when = datetime.combine(today_local, rem_time)
                        if not (when <= now_local < when + window):
                            continue

                        rem_id = rem.get("id") or rem_time.strftime("%H:%M")
                        key = f"rem:todos:{t.get('id')}:{rem_id}:{today_local.isoformat()}:{rem_time.strftime('%H:%M')}"

                        sent_map = ud.setdefault(UD_SENT_REMINDERS, {})
                        if key in sent_map:
                            continue

                        show_status = bool(ud.get(UD_REMINDER_SHOW_STATUS, False))

                        status_html = ""
                        if show_status:
                            status_data = get_status(habitica_user_id, habitica_api_key) or {}
                            stats = status_data.get("stats", {}) or {}
                            status_html = build_status_block(stats)

                        await _send_task_reminder(
                            application.bot,
                            chat_id=chat_id,
                            thread_id=thread_id,
                            normalized_type="todos",
                            task=t,
                            user_data=ud,
                            habitica_user_id=habitica_user_id,
                            habitica_api_key=habitica_api_key,
                            status_html=status_html,
                        )

                        sent_map[key] = int(time_mod.time())
                        sent_count += 1
                else:
                    # Optional fallback: todo due datetime in `date` (only if no reminders)
                    due_dt = _parse_iso_dt(t.get("date"))
                    if due_dt:
                        # convert due to UTC naive, then to local
                        if due_dt.tzinfo is not None:
                            due_utc = due_dt.astimezone(timezone.utc).replace(tzinfo=None)
                            due_local = due_utc - timedelta(minutes=tz_offset)
                        else:
                            due_local = due_dt

                        if due_local <= now_local < due_local + window:
                            key = f"due:todos:{t.get('id')}:{due_local.strftime('%Y-%m-%dT%H:%M')}"
                            sent_map = ud.setdefault(UD_SENT_REMINDERS, {})
                            if key not in sent_map:

                                show_status = bool(ud.get(UD_REMINDER_SHOW_STATUS, False))

                                status_html = ""
                                if show_status:
                                    status_data = get_status(habitica_user_id, habitica_api_key) or {}
                                    stats = status_data.get("stats", {}) or {}
                                    status_html = build_status_block(stats)

                                await _send_task_reminder(
                                    application.bot,
                                    chat_id=chat_id,
                                    thread_id=thread_id,
                                    normalized_type="todos",
                                    task=t,
                                    user_data=ud,
                                    habitica_user_id=habitica_user_id,
                                    habitica_api_key=habitica_api_key,
                                    status_html=status_html,
                                )

                                sent_map[key] = int(time_mod.time())
                                sent_count += 1

            _sent_key_prune(ud)

            users_checked += 1

        except Exception:
            errors += 1
            logging.exception("Reminder tick failed for telegram_user_id=%s", telegram_user_id)

    # Make sure persistence sees our updated user_data maps
    try:
        await application.update_persistence()
    except Exception:
        pass

    return {
        "sent": sent_count,
        "users_checked": users_checked,
        "errors": errors,
        "window_seconds": window_seconds,
    }


def _parse_time_of_day(value) -> dtime | None:
    """Accepts 'HH:MM', 'HH:MM:SS', ISO datetime, or minutes since midnight."""
    if value is None:
        return None

    if isinstance(value, str):
        s = value.strip()
        # ISO datetime string?
        if "T" in s:
            try:
                dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
                return dt.time().replace(second=0, microsecond=0)
            except Exception:
                pass

        for fmt in ("%H:%M", "%H:%M:%S"):
            try:
                return datetime.strptime(s, fmt).time()
            except Exception:
                pass

        # "1200" -> 12:00
        if s.isdigit() and len(s) == 4:
            return dtime(hour=int(s[:2]), minute=int(s[2:]))

    if isinstance(value, (int, float)):
        mins = int(value)
        if 0 <= mins < 24 * 60:
            return dtime(hour=mins // 60, minute=mins % 60)

    return None


def _parse_iso_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _get_notify_target(telegram_user_id: int, user_data: dict) -> tuple[int, int | None]:
    chat_id = user_data.get(UD_NOTIFY_CHAT_ID) or telegram_user_id
    thread_id = user_data.get(UD_NOTIFY_THREAD_ID)
    return int(chat_id), (int(thread_id) if thread_id is not None else None)


def _sent_key_prune(user_data: dict, *, keep_seconds: int = 7 * 24 * 3600) -> None:
    sent = user_data.get(UD_SENT_REMINDERS)
    if not isinstance(sent, dict):
        return
    cutoff = int(time_mod.time()) - keep_seconds
    for k, ts in list(sent.items()):
        if not isinstance(ts, int) or ts < cutoff:
            sent.pop(k, None)


async def _send_task_reminder(
    bot,
    *,
    chat_id: int,
    thread_id: int | None,
    normalized_type: str,   # "habits" / "dailys" / "todos" / "completedTodos" / "rewards"
    task: dict,
    user_data: dict,
    habitica_user_id: str,
    habitica_api_key: str,
    status_html: str = "",
):
    """
    Reminder message styled like inline single-task cards:
      - avatar attached as DOCUMENT
      - caption:
          <blockquote>ICON <b><i>task</i></b></blockquote>
          <blockquote>Status ...</blockquote>   (optional toggle)
      - buttons match existing callback formats
    """

    # Avoid thread_id=1 (General topic) issues
    if thread_id in (None, 1):
        thread_id = None

    # --- caption (task blockquote + optional status) ---
    task_text = html.escape(task.get("text", "(no title)"))

    icon_map = {
        "habits": "🌀",
        "dailys": "📅",
        "todos": "📝",
        "completedTodos": "✅",
        "rewards": "💰",
    }
    icon = icon_map.get(normalized_type, "🔔")

    show_status = bool(user_data.get(UD_REMINDER_SHOW_STATUS, False))

    if show_status:
        # When ON: task name in quote, and Status below in quote
        caption = f"<blockquote>{icon} <b>{task_text}</b></blockquote>"
        if status_html:
            caption = f"{caption}\n{status_html}"
    else:
        # When OFF: just show unquoted task name (bold)
        caption = f"{icon} <b>{task_text}</b>"

    # --- buttons (same formats you already handle) ---
    task_id = task.get("id")
    if not task_id:
        return

    reply_markup = None

    if normalized_type == "habits":
        up = bool(task.get("up", False))
        down = bool(task.get("down", False))
        counter_up = int(task.get("counterUp", 0))
        counter_down = int(task.get("counterDown", 0))

        keyboard: list[InlineKeyboardButton] = []
        if up:
            keyboard.append(
                InlineKeyboardButton(
                    f"➕ {counter_up}",
                    callback_data=f"habits:up:{task_id}:{counter_up}:{up}:{down}",
                )
            )
        if down:
            keyboard.append(
                InlineKeyboardButton(
                    f"➖ {counter_down}",
                    callback_data=f"habits:down:{task_id}:{counter_down}:{up}:{down}",
                )
            )
        reply_markup = InlineKeyboardMarkup([keyboard]) if keyboard else None

    elif normalized_type in ("dailys", "todos", "completedTodos"):
        is_completed = bool(task.get("completed", False))
        button_text = "✔️" if is_completed else "✖️"
        action = "down" if is_completed else "up"
        reply_markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton(button_text, callback_data=f"{normalized_type}:{action}:{task_id}")]]
        )

    elif normalized_type == "rewards":
        value = int(task.get("value", 0) or 0)
        reply_markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton(f"Buy ({value} Gold)", callback_data=f"rewards:buy:{task_id}")]]
        )

    # --- send as photo (avatar) ---
    photo_kwargs = dict(
        chat_id=chat_id,
        caption=caption,
        parse_mode="HTML",
        reply_markup=reply_markup,
    )
    if thread_id is not None:
        photo_kwargs["message_thread_id"] = thread_id

    # 1) Fast path: cached Telegram PHOTO file_id
    avatar_photo_id = user_data.get("AVATAR_FILE_ID")
    if avatar_photo_id:
        try:
            await bot.send_photo(photo=avatar_photo_id, **photo_kwargs)
            return
        except Exception:
            # If Telegram rejects old/invalid file_id, drop it and fall back to PNG
            user_data.pop("AVATAR_FILE_ID", None)

    # 2) Next: existing PNG on disk (maybe created by /avatar or other panels)
    png_path = user_data.get("AVATAR_PNG_PATH")
    if not (png_path and os.path.exists(png_path)):
        # 3) Fallback: render avatar via Node (no export_avatar_png)
        png_path = ensure_avatar_png_no_update(
            habitica_user_id=habitica_user_id,
            habitica_api_key=habitica_api_key,
            user_data=user_data,
            force_refresh=False,
            preloaded_user_json=None,
        )

    if png_path and os.path.exists(png_path):
        try:
            with open(png_path, "rb") as fp:
                msg = await bot.send_photo(photo=fp, **photo_kwargs)
            if getattr(msg, "photo", None):
                # Cache the biggest size photo file_id
                user_data["AVATAR_FILE_ID"] = msg.photo[-1].file_id
            return
        except Exception:
            # Fall through to text-only fallback below
            pass

    # 4) Final fallback (if avatar render failed): text-only
    msg_kwargs = dict(
        chat_id=chat_id,
        text=caption,
        parse_mode="HTML",
        reply_markup=reply_markup,
    )
    if thread_id is not None:
        msg_kwargs["message_thread_id"] = thread_id
    await bot.send_message(**msg_kwargs)



def build_application(*, register_commands: bool = False) -> Application:
    """
    Create and configure the PTB Application.

    - register_commands=True is useful for local polling mode so that
      _register_commands runs once at startup.
    - For webhook/serverless mode, you usually set it to False and use
      /sync_commands when you want to refresh the menu.
    """
    request = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=30.0,
    )

    builder = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .request(request)
        .persistence(persistence)
    )

    if register_commands:
        builder = builder.post_init(_register_commands)

    app = builder.build()

    # Conversation (start/relink + account choice + credentials)
    account_conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start_command_handler),
            CommandHandler("relink", relink_command_handler),
        ],
        states={
            CHOOSING_ACCOUNT: [
                CallbackQueryHandler(
                    account_choice_handler,
                    pattern=r"^acct:(keep|change)$",
                )
            ],
            USER_ID: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    get_user_id_command_handler,
                )
            ],
            API_KEY: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    get_API_key_command_handler,
                )
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_command_handler)],
        allow_reentry=True,
        per_message=False,
        name="account_link",  # <--- ADD THIS
        persistent=True,  # <--- AND THIS
    )
    app.add_handler(account_conv)

    # 🔹 Conversation for /add_todo and the "➕ New Todo" button
    add_todo_conv = ConversationHandler(
        entry_points=[
            # /add_todo command
            CommandHandler("add_todo", add_todo_start),
            # Reply‑keyboard button "➕ New Todo"
            MessageHandler(filters.Regex(r"^➕ New Todo$"), add_todo_start),
        ],
        states={
            ADD_TODO_TITLE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    add_todo_title_received,
                ),
            ],
            ADD_TODO_DIFFICULTY: [
                CallbackQueryHandler(
                    add_todo_difficulty_chosen,
                    pattern=r"^addTodoDifficulty:",
                ),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_command_handler),
            # let user “restart” if they got stuck mid-flow
            CommandHandler("add_todo", add_todo_start),
        ],
        per_message=False,
        allow_reentry=True,
        name="add_todo",  # <--- ADD THIS
        persistent=True,  # <--- AND THIS
    )
    app.add_handler(add_todo_conv)

    # Reply-keyboard buttons -> route to your existing handlers
    RK_BUTTONS_PATTERN = (
        r"^(🔎 Inline Menu|🌀 Habits|📅 Dailys|📝 Todos|✅ Completed Todos|💰 Rewards|📊 Status|🎭 Avatar|🧪 Buy Potion|🔄 Refresh Day|🔁 Menu|⏰ Reminder Settings|🔈 Reminders are ON|🔈 Reminders are OFF|🧾 Status(?: ✔️)?|🔔 Notify Here|⬅️ Back)$"
    )

    app.add_handler(
        MessageHandler(filters.Regex(RK_BUTTONS_PATTERN), handle_reply_keyboard)
    )

    # Menus
    app.add_handler(CommandHandler("menu", menu_command_handler))
    app.add_handler(CommandHandler("menu_rk", menu_rk_command_handler))

    # Inline and callback handling
    app.add_handler(InlineQueryHandler(inline_query_handler))
    app.add_handler(CallbackQueryHandler(task_button_handler))

    # Direct command shortcuts
    app.add_handler(CommandHandler("habits", habits_command_handler))
    app.add_handler(CommandHandler("dailys", dailys_command_handler))
    app.add_handler(CommandHandler("todos", todos_command_handler))
    app.add_handler(
        CommandHandler("completedtodos", completed_todos_command_handler)
    )
    app.add_handler(CommandHandler("rewards", rewards_command_handler))
    app.add_handler(CommandHandler("status", get_status_command_handler))
    app.add_handler(CommandHandler("task_list", task_list_command_handler))
    app.add_handler(CommandHandler("buy_potion", buy_potion_command_handler))
    app.add_handler(CommandHandler("debug", debug_commands))
    app.add_handler(CommandHandler("refresh_day", refresh_day_command_handler))
    app.add_handler(CommandHandler("notify_here", notify_here_command_handler))
    app.add_handler(CommandHandler("sync_commands", sync_commands_command_handler))
    app.add_handler(CommandHandler("avatar", avatar_command_handler))
    app.add_handler(CommandHandler("reminder_status", reminder_status_command_handler))

    # Inline picker
    app.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND
            & filters.Regex(r"(?i)^(habits|tasks|hbt|todo|quest|menu)"),
            inline_picker_handler,
        )
    )
    app.add_handler(CommandHandler("inline", inline_picker_handler))  # optional slash command

    # "Hide menu" RK button
    app.add_handler(
        MessageHandler(
            filters.Regex(r"^(?:❌ )?Hide Menu$"),
            hide_menu_handler,
        )
    )

    # Global error handler
    app.add_error_handler(on_error)

    return app


if __name__ == "__main__":
    # Local / dev: still use polling
    application = build_application(register_commands=True)
    application.run_polling(bootstrap_retries=10)
