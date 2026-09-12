"""Deterministic grounding for relative weekday expressions in user actions."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.api.protocol import ToolRequest


_NEXT_WEEKDAY = re.compile(
    r"下(?:个)?(?:周|星期|礼拜)\s*(?P<weekday>[一二三四五六日天1-7])"
)
_WEEKDAYS = {
    "一": 0,
    "二": 1,
    "三": 2,
    "四": 3,
    "五": 4,
    "六": 5,
    "日": 6,
    "天": 6,
    "1": 0,
    "2": 1,
    "3": 2,
    "4": 3,
    "5": 4,
    "6": 5,
    "7": 6,
}


def ground_relative_weekday(
    request: ToolRequest,
    *,
    user_text: str,
    reference_time: datetime,
    time_zone: str,
) -> ToolRequest:
    """Correct a model-produced reminder date to an explicit next-week weekday."""

    if request.capability != "reminder.create":
        return request
    matches = {_WEEKDAYS[item] for item in _NEXT_WEEKDAY.findall(user_text)}
    if len(matches) != 1:
        return request
    due_at = request.arguments.get("dueAt")
    if not isinstance(due_at, str):
        return request
    try:
        zone = ZoneInfo(time_zone)
        parsed_due_at = datetime.fromisoformat(due_at.replace("Z", "+00:00"))
    except (ValueError, ZoneInfoNotFoundError):
        return request
    if parsed_due_at.tzinfo is None:
        return request

    expected_date = next_weekday_date(
        reference_time.astimezone(zone).date(),
        matches.pop(),
    )
    local_due_at = parsed_due_at.astimezone(zone)
    if local_due_at.date() == expected_date:
        return request
    corrected_due_at = datetime.combine(
        expected_date,
        time(
            local_due_at.hour,
            local_due_at.minute,
            local_due_at.second,
            local_due_at.microsecond,
        ),
        tzinfo=zone,
    )
    return request.model_copy(
        update={
            "arguments": {
                **request.arguments,
                "dueAt": corrected_due_at.isoformat(),
            }
        }
    )


def next_weekday_date(local_date: date, weekday: int) -> date:
    """Return weekday 0...6 in the next Monday-to-Sunday calendar week."""

    next_monday = local_date + timedelta(days=7 - local_date.weekday())
    return next_monday + timedelta(days=weekday)
