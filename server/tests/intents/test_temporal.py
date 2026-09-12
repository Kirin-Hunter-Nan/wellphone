"""Tests for deterministic grounding of relative weekdays."""

from datetime import date, datetime, timezone

from app.api.protocol import ToolRequest
from app.intents.temporal import ground_relative_weekday, next_weekday_date


REFERENCE_TIME = datetime(2026, 9, 12, 4, 0, tzinfo=timezone.utc)


def reminder_request(due_at: str) -> ToolRequest:
    return ToolRequest.model_validate({
        "toolCallId": "call_1",
        "capability": "reminder.create",
        "arguments": {
            "title": "要完成这个测试",
            "dueAt": due_at,
        },
    })


def test_next_weekday_uses_the_next_calendar_week() -> None:
    assert next_weekday_date(date(2026, 9, 12), 5) == date(2026, 9, 19)


def test_corrects_model_date_that_does_not_match_next_saturday() -> None:
    request = reminder_request("2026-09-20T10:00:00+08:00")

    grounded = ground_relative_weekday(
        request,
        user_text="下周六早上10:00提醒我要完成这个测试。",
        reference_time=REFERENCE_TIME,
        time_zone="Asia/Shanghai",
    )

    assert grounded.arguments["dueAt"] == "2026-09-19T10:00:00+08:00"


def test_preserves_model_date_when_it_already_matches_next_saturday() -> None:
    request = reminder_request("2026-09-19T10:00:00+08:00")

    grounded = ground_relative_weekday(
        request,
        user_text="下周六早上10:00提醒我。",
        reference_time=REFERENCE_TIME,
        time_zone="Asia/Shanghai",
    )

    assert grounded is request


def test_does_not_change_a_date_without_an_explicit_next_weekday() -> None:
    request = reminder_request("2026-09-20T10:00:00+08:00")

    grounded = ground_relative_weekday(
        request,
        user_text="下周早上10:00提醒我。",
        reference_time=REFERENCE_TIME,
        time_zone="Asia/Shanghai",
    )

    assert grounded is request


def test_next_sunday_remains_sunday() -> None:
    request = reminder_request("2026-09-20T10:00:00+08:00")

    grounded = ground_relative_weekday(
        request,
        user_text="下个星期天早上10:00提醒我。",
        reference_time=REFERENCE_TIME,
        time_zone="Asia/Shanghai",
    )

    assert grounded is request
