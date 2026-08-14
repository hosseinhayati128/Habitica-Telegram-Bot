"""Framework-independent Habitica gameplay services for the Mini App.

The module keeps cron decisions, yesterday-review eligibility, Health Potion
metadata, and mutating orchestration away from Flask and Telegram handlers.
Only normalized dataclasses cross this boundary; raw Habitica responses remain
server-side.  Every injected mutation is invoked at most once.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import math
from typing import Any, Generic, TypeVar

from Habitica_API import (
    HabiticaErrorKind,
    HabiticaResult,
    buy_health_potion_result,
    get_content_result,
    get_task_result,
    get_tasks_result,
    get_user_result,
    run_cron_result,
    score_task_result,
)


T = TypeVar("T")
DAY_KEYS = ("su", "m", "t", "w", "th", "f", "s")
MAX_TITLE_LENGTH = 500
MAX_NOTES_LENGTH = 2_000
MAX_CHECKLIST_ITEMS = 100
MAX_CHECKLIST_TEXT_LENGTH = 500
MAX_IDENTIFIER_LENGTH = 200
# Process at most eight selected Dailies in one request. A full batch uses 23
# Habitica calls: four fixed reads, two calls per Daily, then the pre-cron read,
# cron POST, and final read. Even a failed score's two reconciliation reads
# keep one request below Habitica's shared 30-requests-per-minute quota. The
# submitted selection itself is not count-limited: larger selections return a
# retry-safe continuation before cron, so the UI can preserve every choice.
MAX_DAILY_BATCH_SIZE = 8
BATCH_CONTINUATION_RETRY_SECONDS = 60
MAX_CHARACTER_CLASS_LENGTH = 64


@dataclass(frozen=True)
class GameplayError:
    """Sanitized service failure safe for an HTTP adapter."""

    code: str
    upstream_kind: str | None = None
    retry_after: float | None = None
    outcome_unknown: bool = False
    unresolved_daily_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class GameplayResult(Generic[T]):
    """Typed gameplay result; partial failures may also carry safe data."""

    data: T | None = None
    error: GameplayError | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass(frozen=True)
class UserSnapshot:
    """Small character snapshot shared by Home and pinned profiles."""

    level: int | None
    character_class: str | None
    hp: int | float | None
    max_hp: int | float | None
    exp: int | float | None
    max_exp: int | float | None
    mp: int | float | None
    max_mp: int | float | None
    gold: int | float | None

    def to_payload(self) -> dict[str, Any]:
        return {
            "profile": {
                "level": self.level,
                "class": self.character_class,
            },
            "stats": {
                "hp": self.hp,
                "maxHp": self.max_hp,
                "exp": self.exp,
                "maxExp": self.max_exp,
                "mp": self.mp,
                "maxMp": self.max_mp,
                "gold": self.gold,
            },
        }


@dataclass(frozen=True)
class ReviewChecklistItem:
    id: str
    text: str
    completed: bool

    def to_payload(self) -> dict[str, Any]:
        return {"id": self.id, "text": self.text, "completed": self.completed}


@dataclass(frozen=True)
class ReviewDaily:
    id: str
    text: str
    notes: str
    priority: int | float | None
    checklist: tuple[ReviewChecklistItem, ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "notes": self.notes,
            "priority": self.priority,
            "checklist": [item.to_payload() for item in self.checklist],
            "completed": False,
        }


@dataclass(frozen=True)
class DayStatus:
    refresh_required: bool
    days_missed: int
    review_label: str | None
    dailies: tuple[ReviewDaily, ...]
    profile: UserSnapshot | None = None

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "refreshRequired": self.refresh_required,
            "profile": self.profile.to_payload() if self.profile is not None else None,
        }
        if self.refresh_required:
            payload.update(
                {
                    "daysMissed": self.days_missed,
                    "reviewLabel": self.review_label,
                    "dailies": [daily.to_payload() for daily in self.dailies],
                }
            )
        return payload


@dataclass(frozen=True)
class PotionMetadata:
    name: str
    price: int | float | None
    healing: int | float | None

    def to_payload(self) -> dict[str, Any]:
        return {"name": self.name, "price": self.price, "healing": self.healing}


@dataclass(frozen=True)
class PotionStatus:
    potion: PotionMetadata
    hp: int | float | None
    max_hp: int | float | None
    gold: int | float | None
    health_full: bool
    can_afford: bool | None

    def to_payload(self) -> dict[str, Any]:
        return {
            "potion": self.potion.to_payload(),
            "stats": {"hp": self.hp, "maxHp": self.max_hp, "gold": self.gold},
            "healthFull": self.health_full,
            "canAfford": self.can_afford,
        }


@dataclass(frozen=True)
class PotionPurchaseResult:
    profile: UserSnapshot | None
    potion: PotionMetadata
    sync_required: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "profile": self.profile.to_payload() if self.profile is not None else None,
            "potion": self.potion.to_payload(),
            "syncRequired": self.sync_required,
        }


@dataclass(frozen=True)
class DayRefreshResult:
    status: str
    refresh_required: bool
    profile: UserSnapshot | None
    scored_daily_ids: tuple[str, ...] = ()
    unresolved_daily_ids: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "day": {"refreshRequired": self.refresh_required},
            "profile": self.profile.to_payload() if self.profile is not None else None,
            "scoredDailyIds": list(self.scored_daily_ids),
            "unresolvedDailyIds": list(self.unresolved_daily_ids),
        }


def _safe_number(value: Any, *, minimum: float | None = None) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        finite = math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        return None
    if not finite or (minimum is not None and value < minimum):
        return None
    return value


def _safe_integer(value: Any, *, minimum: int = 0) -> int | None:
    number = _safe_number(value, minimum=minimum)
    if number is None or int(number) != number:
        return None
    return int(number)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _snapshot(user_or_stats: Mapping[str, Any]) -> UserSnapshot:
    stats_value = user_or_stats.get("stats")
    stats = _mapping(stats_value) if stats_value is not None else user_or_stats
    character_class = stats.get("class")
    return UserSnapshot(
        level=_safe_integer(stats.get("lvl")),
        character_class=(
            character_class[:MAX_CHARACTER_CLASS_LENGTH]
            if isinstance(character_class, str)
            else None
        ),
        hp=_safe_number(stats.get("hp"), minimum=0),
        max_hp=_safe_number(
            stats.get("maxHealth", stats.get("maxHp")), minimum=0
        ),
        exp=_safe_number(stats.get("exp"), minimum=0),
        max_exp=_safe_number(
            stats.get("toNextLevel", stats.get("maxExp")), minimum=0
        ),
        mp=_safe_number(stats.get("mp"), minimum=0),
        max_mp=_safe_number(stats.get("maxMP", stats.get("maxMp")), minimum=0),
        gold=_safe_number(stats.get("gp", stats.get("gold")), minimum=0),
    )


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            numeric = float(value)
            if not math.isfinite(numeric):
                return None
            if abs(numeric) > 100_000_000_000:
                numeric /= 1_000
            parsed = datetime.fromtimestamp(numeric, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, str) and value.strip():
        candidate = value.strip()
        if candidate.endswith("Z"):
            candidate = f"{candidate[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _utc_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    parsed = _parse_datetime(now)
    if parsed is None:
        raise ValueError("now must be a valid date-time")
    return parsed


def _day_start(preferences: Mapping[str, Any]) -> float:
    value = _safe_number(preferences.get("dayStart"), minimum=0)
    if value is None or value > 24:
        return 0.0
    return float(value)


def _utc_offset_from_preferences(
    preferences: Mapping[str, Any],
    *,
    at_last_cron: bool = False,
) -> int:
    field = "timezoneOffsetAtLastCron" if at_last_cron else "timezoneOffset"
    stored = _safe_number(preferences.get(field))
    if stored is None and at_last_cron:
        return _utc_offset_from_preferences(preferences)
    utc_offset = -int(stored or 0)
    if utc_offset < -720 or utc_offset > 840:
        return 0
    return utc_offset


def _start_of_habitica_day(
    instant: datetime,
    *,
    timezone_utc_offset: int,
    day_start: float,
) -> datetime:
    zone = timezone(timedelta(minutes=timezone_utc_offset))
    local = instant.astimezone(zone)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    boundary = midnight + timedelta(hours=day_start)
    if local < boundary:
        boundary -= timedelta(days=1)
    return boundary


def _days_since(
    earlier: datetime,
    later: datetime,
    *,
    timezone_utc_offset: int,
    day_start: float,
) -> int:
    old_day = _start_of_habitica_day(
        earlier,
        timezone_utc_offset=timezone_utc_offset,
        day_start=day_start,
    ).date()
    new_day = _start_of_habitica_day(
        later,
        timezone_utc_offset=timezone_utc_offset,
        day_start=day_start,
    ).date()
    return max(0, (new_day - old_day).days)


def day_refresh_required(user: Mapping[str, Any]) -> bool:
    """Trust only Habitica's literal boolean ``needsCron`` signal."""
    return isinstance(user, Mapping) and user.get("needsCron") is True


def compute_days_missed(
    user: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> int:
    """Mirror Habitica's custom-day/timezone transition missed-day rules."""
    if not isinstance(user, Mapping):
        return 0
    last_cron = _parse_datetime(user.get("lastCron"))
    if last_cron is None:
        return 0
    auth = _mapping(user.get("auth"))
    timestamps = _mapping(auth.get("timestamps"))
    logged_in = _parse_datetime(timestamps.get("loggedIn"))
    if logged_in is not None and logged_in < last_cron:
        last_cron = logged_in

    current_time = _utc_now(now)
    preferences = _mapping(user.get("preferences"))
    day_start = _day_start(preferences)
    current_offset = _utc_offset_from_preferences(preferences)
    old_offset = _utc_offset_from_preferences(preferences, at_last_cron=True)

    days_new_zone = _days_since(
        last_cron,
        current_time,
        timezone_utc_offset=current_offset,
        day_start=day_start,
    )
    if old_offset == current_offset:
        return days_new_zone

    comparison_time = current_time
    if old_offset > current_offset:
        comparison_time -= timedelta(minutes=old_offset - current_offset)
    days_old_zone = _days_since(
        last_cron,
        comparison_time,
        timezone_utc_offset=old_offset,
        day_start=day_start,
    )

    if old_offset > current_offset:
        if days_old_zone > 0 and days_new_zone > 0:
            return min(days_old_zone, days_new_zone)
        if days_new_zone > 0 and days_old_zone == 0:
            return 0
        return 0
    return days_new_zone


def _calendar_date(
    value: date | datetime,
    *,
    preferences: Mapping[str, Any],
) -> date:
    # Habitica treats visible calendar-only task dates literally.  Applying a
    # timezone or custom day start to a ``date`` would shift it for extreme
    # offsets and contradict the official shouldDo implementation.
    if not isinstance(value, datetime):
        return value
    instant = _parse_datetime(value)
    if instant is None:
        raise ValueError("invalid day")
    return _start_of_habitica_day(
        instant,
        timezone_utc_offset=_utc_offset_from_preferences(preferences),
        day_start=_day_start(preferences),
    ).date()


def _task_start_date(
    value: Any,
    *,
    preferences: Mapping[str, Any],
) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str) and len(value) == 10:
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    parsed = _parse_datetime(value)
    if parsed is None:
        return None
    zone = timezone(
        timedelta(minutes=_utc_offset_from_preferences(preferences))
    )
    return parsed.astimezone(zone).date()


def _javascript_weekday(day: date) -> int:
    return (day.weekday() + 1) % 7


def should_do(
    day: date | datetime,
    daily_task: Mapping[str, Any],
    *,
    preferences: Mapping[str, Any] | None = None,
) -> bool:
    """Return whether Habitica schedules a Daily on the supplied user day.

    The recurrence mirrors Habitica's daily, weekly, monthly and yearly model
    without inventing history.  ``yesterDaily`` remains the authoritative
    Record-Yesterday eligibility signal used by :func:`eligible_review_dailies`.
    """
    if not isinstance(daily_task, Mapping) or daily_task.get("type") != "daily":
        return False
    every_x = _safe_integer(daily_task.get("everyX"), minimum=1)
    if every_x is None or every_x > 9_999 or daily_task.get("startDate") is None:
        return False
    prefs = preferences if isinstance(preferences, Mapping) else {}
    try:
        current = _calendar_date(day, preferences=prefs)
    except (TypeError, ValueError):
        return False
    start = _task_start_date(daily_task.get("startDate"), preferences=prefs)
    if start is None or current < start:
        return False

    frequency = daily_task.get("frequency")
    if frequency == "daily":
        return (current - start).days % every_x == 0

    repeat = _mapping(daily_task.get("repeat"))
    active_weekdays = {
        index for index, key in enumerate(DAY_KEYS) if repeat.get(key) is True
    }
    if frequency == "weekly":
        if _javascript_weekday(current) not in active_weekdays:
            return False
        weeks = (current - start).days // 7
        return weeks % every_x == 0

    if frequency == "monthly":
        months = (current.year - start.year) * 12 + current.month - start.month
        if months % every_x != 0:
            return False
        weeks_of_month = daily_task.get("weeksOfMonth")
        if isinstance(weeks_of_month, list) and weeks_of_month:
            valid_weeks = {
                week
                for value in weeks_of_month
                if (week := _safe_integer(value, minimum=1)) is not None
                and week <= 5
            }
            return (
                _javascript_weekday(current) in active_weekdays
                and (current.day + 6) // 7 in valid_weeks
            )
        days_of_month = daily_task.get("daysOfMonth")
        if isinstance(days_of_month, list) and days_of_month:
            valid_days = {
                month_day
                for value in days_of_month
                if (month_day := _safe_integer(value, minimum=1)) is not None
                and month_day <= 31
            }
            return current.day in valid_days
        return current.day == start.day

    if frequency == "yearly":
        years = current.year - start.year
        return (
            years % every_x == 0
            and current.month == start.month
            and current.day == start.day
        )
    return False


def _review_daily(raw: Mapping[str, Any]) -> ReviewDaily | None:
    task_id = raw.get("id", raw.get("_id"))
    if (
        not isinstance(task_id, str)
        or not task_id
        or len(task_id) > MAX_IDENTIFIER_LENGTH
        or any(
            ord(character) < 32 or ord(character) == 127
            for character in task_id
        )
    ):
        return None
    text_value = raw.get("text")
    text = text_value if isinstance(text_value, str) and text_value else "Untitled Daily"
    notes_value = raw.get("notes")
    notes = notes_value if isinstance(notes_value, str) else ""
    priority = _safe_number(raw.get("priority"), minimum=0)
    checklist: list[ReviewChecklistItem] = []
    raw_checklist = raw.get("checklist")
    if isinstance(raw_checklist, list):
        for item in raw_checklist[:MAX_CHECKLIST_ITEMS]:
            if not isinstance(item, Mapping):
                continue
            item_id = item.get("id", item.get("_id"))
            item_text = item.get("text")
            completed = item.get("completed")
            if (
                isinstance(item_id, str)
                and item_id
                and len(item_id) <= MAX_IDENTIFIER_LENGTH
                and not any(
                    ord(character) < 32 or ord(character) == 127
                    for character in item_id
                )
                and isinstance(item_text, str)
                and isinstance(completed, bool)
            ):
                checklist.append(
                    ReviewChecklistItem(
                        id=item_id,
                        text=item_text[:MAX_CHECKLIST_TEXT_LENGTH],
                        completed=completed,
                    )
                )
    return ReviewDaily(
        id=task_id,
        text=text[:MAX_TITLE_LENGTH],
        notes=notes[:MAX_NOTES_LENGTH],
        priority=priority,
        checklist=tuple(checklist),
    )


def _review_date(
    user: Mapping[str, Any],
    *,
    now: datetime | None,
) -> date:
    """Return yesterday's calendar date in the user's current saved offset.

    This mirrors the official client's choice of the visible "yesterday"
    date.  ``_review_preferences`` separately supplies the last-cron offset
    when ``shouldDo`` converts a task's hidden start-date time component.
    """
    preferences = _mapping(user.get("preferences"))
    zone = timezone(
        timedelta(minutes=_utc_offset_from_preferences(preferences))
    )
    return _utc_now(now).astimezone(zone).date() - timedelta(days=1)


def _review_preferences(user: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """Return the best authoritative offset for the unrefreshed user day.

    Habitica's official client evaluates ``shouldDo`` with the reviewed
    calendar day's UTC offset.  A full user response does not include a time
    zone identifier from which arbitrary historical DST transitions can be
    reconstructed, but it does retain ``timezoneOffsetAtLastCron``.  While
    cron is still due, that is the closest server-owned offset for the day
    being reviewed.  Fall back to the current saved offset when it is absent
    or malformed.
    """
    current = _mapping(user.get("preferences")) if isinstance(user, Mapping) else {}
    at_last_cron = _safe_number(current.get("timezoneOffsetAtLastCron"))
    if (
        at_last_cron is None
        or int(at_last_cron) != at_last_cron
        or not -720 <= -int(at_last_cron) <= 840
    ):
        return current
    review = dict(current)
    review["timezoneOffset"] = at_last_cron
    return review


def _record_yesterday_dailies(
    dailies: Sequence[Any],
    *,
    user: Mapping[str, Any] | None = None,
    now: datetime | None = None,
    review_date: date | None = None,
    allow_completed: bool = False,
) -> tuple[ReviewDaily, ...]:
    """Normalize due personal ``yesterDaily`` candidates."""
    if isinstance(dailies, (str, bytes)) or not isinstance(dailies, Sequence):
        return ()
    preferences = _review_preferences(user)
    if review_date is None and isinstance(user, Mapping):
        review_date = _review_date(user, now=now)
    if review_date is None:
        return ()
    normalized: list[ReviewDaily] = []
    seen: set[str] = set()
    for raw in dailies:
        if not isinstance(raw, Mapping):
            continue
        if raw.get("type") not in (None, "daily"):
            continue
        completed = raw.get("completed")
        if raw.get("yesterDaily") is not True:
            continue
        if allow_completed:
            if completed is not False and completed is not True:
                continue
        elif completed is not False:
            continue
        if review_date is not None and not should_do(
            review_date,
            raw,
            preferences=preferences,
        ):
            continue
        # Habitica's official Record Yesterday flow excludes active group
        # Dailies.  A user's copy of a challenge Daily remains eligible, as do
        # broken group references whose object no longer has an id.
        group = _mapping(raw.get("group"))
        if group.get("id"):
            continue
        daily = _review_daily(raw)
        if daily is not None and daily.id not in seen:
            seen.add(daily.id)
            normalized.append(daily)
    return tuple(normalized)


def eligible_review_dailies(
    dailies: Sequence[Any],
    *,
    user: Mapping[str, Any] | None = None,
    now: datetime | None = None,
    review_date: date | None = None,
) -> tuple[ReviewDaily, ...]:
    """Normalize only due, incomplete personal ``yesterDaily`` candidates."""
    return _record_yesterday_dailies(
        dailies,
        user=user,
        now=now,
        review_date=review_date,
    )


def evaluate_day_status(
    user: Mapping[str, Any],
    dailies: Sequence[Any],
    *,
    now: datetime | None = None,
) -> GameplayResult[DayStatus]:
    """Evaluate an already-fetched user and Daily list without I/O."""
    if not isinstance(user, Mapping) or not isinstance(user.get("needsCron"), bool):
        return GameplayResult(error=GameplayError("invalid_response"))
    if not day_refresh_required(user):
        return GameplayResult(data=DayStatus(False, 0, None, (), _snapshot(user)))
    try:
        missed = max(1, compute_days_missed(user, now=now))
    except ValueError:
        return GameplayResult(error=GameplayError("invalid_response"))
    label = "Yesterday" if missed == 1 else f"{missed} missed days"
    return GameplayResult(
        data=DayStatus(
            True,
            missed,
            label,
            eligible_review_dailies(dailies, user=user, now=now),
            _snapshot(user),
        )
    )


def _upstream_error(
    result: HabiticaResult[Any],
    *,
    fallback: str = "habitica_unavailable",
    unresolved_daily_ids: tuple[str, ...] = (),
) -> GameplayError:
    upstream = result.error
    if upstream is None:
        return GameplayError(fallback, unresolved_daily_ids=unresolved_daily_ids)
    mapping = {
        HabiticaErrorKind.INVALID_CREDENTIALS: "habitica_unauthorized",
        HabiticaErrorKind.RATE_LIMITED: "rate_limited",
        HabiticaErrorKind.REQUEST_TIMEOUT: "request_timeout",
        HabiticaErrorKind.INVALID_RESPONSE: "invalid_response",
    }
    return GameplayError(
        code=mapping.get(upstream.kind, fallback),
        upstream_kind=upstream.kind.value,
        retry_after=upstream.retry_after,
        outcome_unknown=upstream.outcome_unknown,
        unresolved_daily_ids=unresolved_daily_ids,
    )


def fetch_day_status(
    user_id: str,
    api_key: str,
    *,
    now: datetime | None = None,
    fetch_user: Callable[[str, str], HabiticaResult[dict]] = get_user_result,
    fetch_dailies: Callable[..., HabiticaResult[list[dict]]] = get_tasks_result,
) -> GameplayResult[DayStatus]:
    """Fetch authoritative startup state, avoiding a task read when ready."""
    user_result = fetch_user(user_id, api_key)
    if not user_result.ok or not isinstance(user_result.data, Mapping):
        return GameplayResult(error=_upstream_error(user_result))
    if user_result.data.get("needsCron") is False:
        return GameplayResult(
            data=DayStatus(False, 0, None, (), _snapshot(user_result.data))
        )
    if user_result.data.get("needsCron") is not True:
        return GameplayResult(error=GameplayError("invalid_response"))
    dailies_result = fetch_dailies(user_id, api_key, "dailys", history=False)
    if not dailies_result.ok or not isinstance(dailies_result.data, list):
        return GameplayResult(error=_upstream_error(dailies_result))
    return evaluate_day_status(user_result.data, dailies_result.data, now=now)


def _potion_metadata(content: Mapping[str, Any]) -> PotionMetadata | None:
    potion = _mapping(content.get("potion"))
    price = _safe_number(potion.get("value"), minimum=0)
    if price is None:
        return None
    name_value = potion.get("text")
    name = name_value.strip() if isinstance(name_value, str) and name_value.strip() else "Health Potion"
    healing: int | float | None = None
    for field in ("healing", "heal", "health", "amount"):
        candidate = _safe_number(potion.get(field), minimum=0)
        if candidate is not None:
            healing = candidate
            break
    return PotionMetadata(name=name[:128], price=price, healing=healing)


def _potion_gate_error(user: Mapping[str, Any]) -> GameplayError | None:
    """Require a resolved, explicitly reported Habitica day for potion use."""
    if user.get("needsCron") is True:
        return GameplayError("day_refresh_required")
    if user.get("needsCron") is not False:
        return GameplayError("invalid_response")
    return None


def _valid_potion_snapshot(snapshot: UserSnapshot) -> bool:
    """Return whether the confirmation UI has all authoritative values."""
    return (
        snapshot.hp is not None
        and snapshot.max_hp is not None
        and snapshot.max_hp > 0
        and snapshot.gold is not None
    )


def _purchase_was_observed(
    before: PotionStatus,
    after: UserSnapshot,
) -> bool:
    """Conservatively detect a purchase after an uncertain POST response."""
    if (
        before.hp is None
        or before.gold is None
        or before.potion.price is None
        or after.hp is None
        or after.gold is None
    ):
        return False
    gold_spent = before.gold - after.gold
    return after.hp > before.hp and math.isclose(
        gold_spent,
        before.potion.price,
        rel_tol=0.0,
        abs_tol=1e-9,
    )


def get_health_potion_status(
    user_id: str,
    api_key: str,
    *,
    fetch_user: Callable[[str, str], HabiticaResult[dict]] = get_user_result,
    fetch_content: Callable[..., HabiticaResult[dict]] = get_content_result,
) -> GameplayResult[PotionStatus]:
    """Fetch a non-mutating, authoritative Health Potion confirmation model."""
    user_result = fetch_user(user_id, api_key)
    if not user_result.ok or not isinstance(user_result.data, Mapping):
        return GameplayResult(error=_upstream_error(user_result))
    gate_error = _potion_gate_error(user_result.data)
    if gate_error is not None:
        return GameplayResult(error=gate_error)
    snapshot = _snapshot(user_result.data)
    if not _valid_potion_snapshot(snapshot):
        return GameplayResult(error=GameplayError("invalid_response"))
    content_result = fetch_content(user_id, api_key, language="en")
    if not content_result.ok or not isinstance(content_result.data, Mapping):
        return GameplayResult(error=_upstream_error(content_result))
    metadata = _potion_metadata(content_result.data)
    if metadata is None:
        return GameplayResult(error=GameplayError("invalid_response"))
    full = (
        snapshot.hp is not None
        and snapshot.max_hp is not None
        and snapshot.hp >= snapshot.max_hp
    )
    affordable = (
        snapshot.gold >= metadata.price
        if snapshot.gold is not None and metadata.price is not None
        else None
    )
    return GameplayResult(
        data=PotionStatus(
            potion=metadata,
            hp=snapshot.hp,
            max_hp=snapshot.max_hp,
            gold=snapshot.gold,
            health_full=full,
            can_afford=affordable,
        )
    )


def purchase_health_potion(
    user_id: str,
    api_key: str,
    *,
    fetch_user: Callable[[str, str], HabiticaResult[dict]] = get_user_result,
    fetch_content: Callable[..., HabiticaResult[dict]] = get_content_result,
    buy_potion: Callable[[str, str], HabiticaResult[dict]] = buy_health_potion_result,
) -> GameplayResult[PotionPurchaseResult]:
    """Validate, buy once, and reconcile a Health Potion purchase."""
    status_result = get_health_potion_status(
        user_id,
        api_key,
        fetch_user=fetch_user,
        fetch_content=fetch_content,
    )
    if not status_result.ok or status_result.data is None:
        return GameplayResult(error=status_result.error)
    status = status_result.data
    if status.health_full:
        return GameplayResult(error=GameplayError("health_already_full"))
    if status.can_afford is False:
        return GameplayResult(error=GameplayError("not_enough_gold"))

    mutation = buy_potion(user_id, api_key)
    if not mutation.ok:
        if (
            mutation.error is not None
            and mutation.error.kind is HabiticaErrorKind.UNAUTHORIZED
        ):
            # This mutation also uses an ordinary NotAuthorized 401 for
            # gameplay rules (full HP, insufficient gold, or a dead user).
            # Re-read once rather than incorrectly asking the user to relink.
            reconciled = fetch_user(user_id, api_key)
            if reconciled.ok and isinstance(reconciled.data, Mapping):
                gate_error = _potion_gate_error(reconciled.data)
                if gate_error is not None:
                    return GameplayResult(error=gate_error)
                after = _snapshot(reconciled.data)
                if (
                    after.hp is not None
                    and after.max_hp is not None
                    and after.hp >= after.max_hp
                ):
                    return GameplayResult(
                        error=GameplayError("health_already_full")
                    )
                if (
                    after.gold is not None
                    and status.potion.price is not None
                    and after.gold < status.potion.price
                ):
                    return GameplayResult(error=GameplayError("not_enough_gold"))
            elif not reconciled.ok:
                return GameplayResult(error=_upstream_error(reconciled))
            else:
                return GameplayResult(error=GameplayError("invalid_response"))
            return GameplayResult(error=GameplayError("purchase_failed"))
        if mutation.error is not None and mutation.error.outcome_unknown:
            reconciled = fetch_user(user_id, api_key)
            if reconciled.ok and isinstance(reconciled.data, Mapping):
                after = _snapshot(reconciled.data)
                if _purchase_was_observed(status, after):
                    return GameplayResult(
                        data=PotionPurchaseResult(after, status.potion)
                    )
            # An immediate unchanged read is not proof that a timed-out POST
            # was rejected.  Preserve the unknown outcome so callers never
            # offer a blind retry.
            return GameplayResult(
                error=_upstream_error(mutation, fallback="purchase_failed")
            )
        return GameplayResult(error=_upstream_error(mutation, fallback="purchase_failed"))

    authoritative = fetch_user(user_id, api_key)
    snapshot: UserSnapshot | None = None
    if authoritative.ok and isinstance(authoritative.data, Mapping):
        snapshot = _snapshot(authoritative.data)
    if (snapshot is None or snapshot.hp is None or snapshot.gold is None) and isinstance(
        mutation.data,
        Mapping,
    ):
        mutation_snapshot = _snapshot(mutation.data)
        if mutation_snapshot.hp is not None and mutation_snapshot.gold is not None:
            snapshot = mutation_snapshot
    if snapshot is None or snapshot.hp is None or snapshot.gold is None:
        # The mutation's validated 2xx envelope confirms that the purchase was
        # applied.  Missing presentation stats require a safe read refresh, not
        # an error that could invite the mutating POST to be repeated.
        return GameplayResult(
            data=PotionPurchaseResult(None, status.potion, sync_required=True)
        )
    return GameplayResult(data=PotionPurchaseResult(snapshot, status.potion))


def _validated_selection(value: Any) -> tuple[str, ...] | None:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        return None
    selected: list[str] = []
    seen: set[str] = set()
    for task_id in value:
        if (
            not isinstance(task_id, str)
            or not task_id
            or not task_id.strip()
            or len(task_id) > MAX_IDENTIFIER_LENGTH
            or any(
                ord(character) < 32 or ord(character) == 127
                for character in task_id
            )
            or task_id in seen
        ):
            return None
        seen.add(task_id)
        selected.append(task_id)
    return tuple(selected)


def _already_refreshed(user: Mapping[str, Any]) -> GameplayResult[DayRefreshResult]:
    return GameplayResult(
        data=DayRefreshResult(
            status="already_refreshed",
            refresh_required=False,
            profile=_snapshot(user),
        )
    )


def _partial_refresh_failure(
    error: GameplayError,
    *,
    selected: Sequence[str],
    resolved: set[str],
    scored: Sequence[str],
) -> GameplayResult[DayRefreshResult]:
    """Return a retry-safe partial result with only unresolved selections."""
    unresolved = tuple(task_id for task_id in selected if task_id not in resolved)
    normalized_error = GameplayError(
        code=error.code,
        upstream_kind=error.upstream_kind,
        retry_after=error.retry_after,
        outcome_unknown=error.outcome_unknown,
        unresolved_daily_ids=unresolved,
    )
    return GameplayResult(
        data=DayRefreshResult(
            status="partial_failure",
            refresh_required=True,
            profile=None,
            scored_daily_ids=tuple(scored),
            unresolved_daily_ids=unresolved,
        ),
        error=normalized_error,
    )


def _batch_refresh_pending(
    *,
    selected: Sequence[str],
    resolved: set[str],
    scored: Sequence[str],
) -> GameplayResult[DayRefreshResult]:
    """Pause a large selection before cron without losing user intent.

    The next explicit submission revalidates the remaining IDs from scratch.
    Already-completed IDs are accepted as resolved, so resubmitting only this
    returned unresolved set cannot score a Daily twice.
    """
    unresolved = tuple(task_id for task_id in selected if task_id not in resolved)
    error = GameplayError(
        code="batch_incomplete",
        retry_after=BATCH_CONTINUATION_RETRY_SECONDS,
        unresolved_daily_ids=unresolved,
    )
    return GameplayResult(
        data=DayRefreshResult(
            status="batch_incomplete",
            refresh_required=True,
            profile=None,
            scored_daily_ids=tuple(scored),
            unresolved_daily_ids=unresolved,
        ),
        error=error,
    )


def _review_submission_ids(
    dailies: Sequence[Any],
    *,
    user: Mapping[str, Any],
    now: datetime | None = None,
) -> set[str]:
    """Allow an earlier successful score to be resolved on a safe retry.

    Only completion is relaxed.  The task must still be a due, personal,
    explicitly boolean ``yesterDaily`` Daily under the same official schedule
    rules used for the visible review list.
    """
    return {
        daily.id
        for daily in _record_yesterday_dailies(
            dailies,
            user=user,
            now=now,
            allow_completed=True,
        )
    }


def refresh_day(
    user_id: str,
    api_key: str,
    completed_daily_ids: Sequence[str],
    *,
    now: datetime | None = None,
    fetch_user: Callable[[str, str], HabiticaResult[dict]] = get_user_result,
    fetch_dailies: Callable[..., HabiticaResult[list[dict]]] = get_tasks_result,
    fetch_task: Callable[[str, str, str], HabiticaResult[dict]] = get_task_result,
    score_daily: Callable[[str, str, str, str], HabiticaResult[dict]] = score_task_result,
    run_cron: Callable[[str, str], HabiticaResult[dict]] = run_cron_result,
) -> GameplayResult[DayRefreshResult]:
    """Record selected yesterday Dailies, then safely run Habitica cron once.

    Reads are repeated only at race-sensitive boundaries.  Score and cron POST
    operations are never retried.  Unknown mutation outcomes are reconciled by
    safe reads, and cron never runs while a selected Daily remains unresolved.
    """
    selected = _validated_selection(completed_daily_ids)
    if selected is None:
        return GameplayResult(error=GameplayError("invalid_daily_selection"))

    first_user = fetch_user(user_id, api_key)
    if not first_user.ok or not isinstance(first_user.data, Mapping):
        return GameplayResult(error=_upstream_error(first_user))
    if first_user.data.get("needsCron") is False:
        return _already_refreshed(first_user.data)
    if first_user.data.get("needsCron") is not True:
        return GameplayResult(error=GameplayError("invalid_response"))

    dailies_result = fetch_dailies(user_id, api_key, "dailys", history=False)
    if not dailies_result.ok or not isinstance(dailies_result.data, list):
        return GameplayResult(error=_upstream_error(dailies_result))
    initially_valid = _review_submission_ids(
        dailies_result.data,
        user=first_user.data,
        now=now,
    )
    if any(task_id not in initially_valid for task_id in selected):
        return GameplayResult(error=GameplayError("invalid_daily_selection"))

    # Re-fetch candidates first, then confirm needsCron before deriving the
    # actionable set.  Each score below also gets its own immediately preceding
    # check.  Habitica provides no transaction that can close the smaller race
    # between that GET and its following score POST.
    refreshed_tasks = fetch_dailies(
        user_id,
        api_key,
        "dailys",
        history=False,
    )
    if not refreshed_tasks.ok or not isinstance(refreshed_tasks.data, list):
        return GameplayResult(error=_upstream_error(refreshed_tasks))
    pre_score_user = fetch_user(user_id, api_key)
    if not pre_score_user.ok or not isinstance(pre_score_user.data, Mapping):
        return GameplayResult(error=_upstream_error(pre_score_user))
    if pre_score_user.data.get("needsCron") is False:
        return _already_refreshed(pre_score_user.data)
    if pre_score_user.data.get("needsCron") is not True:
        return GameplayResult(error=GameplayError("invalid_response"))
    actionable = {
        daily.id: daily
        for daily in eligible_review_dailies(
            refreshed_tasks.data,
            user=pre_score_user.data,
            now=now,
        )
    }

    scored: list[str] = []
    # A submitted task can disappear from the second authoritative candidate
    # read because another client completed or changed it.  It is resolved for
    # this submission, but it was not scored by this service.
    resolved = {task_id for task_id in selected if task_id not in actionable}
    attempted_scores = 0
    for task_id in selected:
        if task_id in resolved:
            continue
        if attempted_scores >= MAX_DAILY_BATCH_SIZE:
            break
        attempted_scores += 1

        # Another Habitica client can run cron while a multi-Daily submission
        # is in progress.  Re-check immediately before every score so a later
        # selection is never knowingly scored into the new day.  The GET and
        # POST cannot be atomic, but this narrows the unavoidable race to one
        # request boundary instead of the entire score batch.
        score_gate = fetch_user(user_id, api_key)
        if not score_gate.ok or not isinstance(score_gate.data, Mapping):
            return _partial_refresh_failure(
                _upstream_error(score_gate),
                selected=selected,
                resolved=resolved,
                scored=scored,
            )
        if score_gate.data.get("needsCron") is False:
            return GameplayResult(
                data=DayRefreshResult(
                    "already_refreshed",
                    False,
                    _snapshot(score_gate.data),
                    tuple(scored),
                    (),
                )
            )
        if score_gate.data.get("needsCron") is not True:
            return _partial_refresh_failure(
                GameplayError("invalid_response"),
                selected=selected,
                resolved=resolved,
                scored=scored,
            )

        score = score_daily(user_id, api_key, task_id, "up")
        if score.ok:
            scored.append(task_id)
            resolved.add(task_id)
            continue

        # Reconcile a rejected or uncertain score with reads; never repeat the
        # mutating POST.  A completed task is resolved whether this request or
        # another client completed it.
        reconciliation_task = fetch_task(user_id, api_key, task_id)
        if reconciliation_task.ok and isinstance(
            reconciliation_task.data,
            Mapping,
        ):
            reconciled_state = reconciliation_task.data
            if reconciled_state.get("completed") is True:
                scored.append(task_id)
                resolved.add(task_id)
                continue
        else:
            reconciled_state = None

        reconciliation_user = fetch_user(user_id, api_key)
        if reconciliation_user.ok and isinstance(
            reconciliation_user.data,
            Mapping,
        ):
            if reconciliation_user.data.get("needsCron") is False:
                return GameplayResult(
                    data=DayRefreshResult(
                        "already_refreshed",
                        False,
                        _snapshot(reconciliation_user.data),
                        tuple(scored),
                        (),
                    )
                )
            if (
                reconciliation_user.data.get("needsCron") is True
                and reconciled_state is not None
                and not eligible_review_dailies(
                    [reconciled_state],
                    user=reconciliation_user.data,
                    now=now,
                )
            ):
                resolved.add(task_id)
                continue
        return _partial_refresh_failure(
            _upstream_error(score, fallback="daily_score_failed"),
            selected=selected,
            resolved=resolved,
            scored=scored,
        )

    if any(task_id not in resolved for task_id in selected):
        return _batch_refresh_pending(
            selected=selected,
            resolved=resolved,
            scored=scored,
        )

    before_cron = fetch_user(user_id, api_key)
    if not before_cron.ok or not isinstance(before_cron.data, Mapping):
        return GameplayResult(
            data=DayRefreshResult(
                "refresh_failed", True, None, tuple(scored), ()
            ),
            error=_upstream_error(before_cron),
        )
    if before_cron.data.get("needsCron") is False:
        already = _already_refreshed(before_cron.data)
        if already.data is not None:
            return GameplayResult(
                data=DayRefreshResult(
                    already.data.status,
                    False,
                    already.data.profile,
                    tuple(scored),
                    (),
                )
            )
    if before_cron.data.get("needsCron") is not True:
        return GameplayResult(
            data=DayRefreshResult(
                "refresh_failed", True, None, tuple(scored), ()
            ),
            error=GameplayError("invalid_response"),
        )

    cron = run_cron(user_id, api_key)
    if not cron.ok:
        reconciled = fetch_user(user_id, api_key)
        if (
            reconciled.ok
            and isinstance(reconciled.data, Mapping)
            and reconciled.data.get("needsCron") is False
        ):
            return GameplayResult(
                data=DayRefreshResult(
                    "refreshed", False, _snapshot(reconciled.data), tuple(scored), ()
                )
            )
        return GameplayResult(
            data=DayRefreshResult(
                "refresh_failed", True, None, tuple(scored), ()
            ),
            error=_upstream_error(cron, fallback="cron_failed"),
        )

    final_user = fetch_user(user_id, api_key)
    if not final_user.ok or not isinstance(final_user.data, Mapping):
        return GameplayResult(
            data=DayRefreshResult(
                "refreshed", False, None, tuple(scored), ()
            )
        )
    if final_user.data.get("needsCron") is True:
        return GameplayResult(
            data=DayRefreshResult(
                "refresh_failed", True, _snapshot(final_user.data), tuple(scored), ()
            ),
            error=GameplayError("cron_failed"),
        )
    if final_user.data.get("needsCron") is not False:
        return GameplayResult(
            data=DayRefreshResult(
                "refreshed", False, _snapshot(final_user.data), tuple(scored), ()
            )
        )
    return GameplayResult(
        data=DayRefreshResult(
            "refreshed", False, _snapshot(final_user.data), tuple(scored), ()
        )
    )


__all__ = [
    "DayRefreshResult",
    "DayStatus",
    "GameplayError",
    "GameplayResult",
    "PotionMetadata",
    "PotionPurchaseResult",
    "PotionStatus",
    "ReviewDaily",
    "UserSnapshot",
    "compute_days_missed",
    "day_refresh_required",
    "eligible_review_dailies",
    "evaluate_day_status",
    "fetch_day_status",
    "get_health_potion_status",
    "purchase_health_potion",
    "refresh_day",
    "should_do",
]
