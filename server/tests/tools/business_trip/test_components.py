"""Business-trip schemas, validation, submission, and artifact tests."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.agent.tools import LoopToolContext
from app.tasks.models import ServerTask
from app.tools.business_trip.artifacts import (
    build_business_trip_outcome,
    business_trip_calendar_payload,
)
from app.tools.business_trip.models import (
    BusinessTripCommitmentsLockArguments,
    BusinessTripInput,
    BusinessTripPlanOutput,
    BusinessTripSubmitArguments,
    CalendarEvent,
    CalendarEventsSearchArguments,
    GmailSearchArguments,
    build_business_trip_gmail_query,
)
from app.tools.business_trip.tools import (
    BusinessTripCommitmentsLockTool,
    BusinessTripSubmitTool,
    CalendarEventsSearchTool,
    GmailSearchTool,
    RoutesSearchTool,
)
from app.tools.business_trip.validation import (
    detect_commitment_conflicts,
    enrich_business_trip_plan,
    validate_business_trip_plan,
)
from app.tools.business_trip.reimbursement import (
    build_reimbursement_report,
    render_reimbursement_markdown,
)


def trip_input(**overrides) -> BusinessTripInput:
    values = {
        "destination": "上海",
        "startDate": "2026-10-01",
        "endDate": "2026-10-01",
        "timeZone": "Asia/Shanghai",
        "commitments": [{
            "id": "meeting-1",
            "kind": "meeting",
            "title": "客户方案会",
            "startAt": "2026-10-01T14:00:00+08:00",
            "endAt": "2026-10-01T15:00:00+08:00",
            "location": "上海国际会议中心",
            "confirmationCode": "MEET-001",
        }],
    }
    values.update(overrides)
    return BusinessTripInput.model_validate(values)


def plan(**overrides) -> BusinessTripPlanOutput:
    values = {
        "title": "上海商务出差计划",
        "overview": "围绕客户会议安排的当日行程",
        "timeZone": "Asia/Shanghai",
        "days": [{
            "date": "2026-10-01",
            "theme": "客户会议",
            "items": [{
                "kind": "fixed",
                "name": "客户方案会",
                "startAt": "2026-10-01T14:00:00+08:00",
                "endAt": "2026-10-01T15:00:00+08:00",
                "sourceCommitmentId": "meeting-1",
                "location": "上海国际会议中心",
            }],
        }],
        "checklist": ["准备演示材料"],
    }
    values.update(overrides)
    return BusinessTripPlanOutput.model_validate(values)


def loop_context(requested: BusinessTripInput, state=None) -> LoopToolContext:
    now = datetime.now(timezone.utc)
    task = ServerTask(
        id=uuid4(),
        conversationId=uuid4(),
        capability="business-trip.plan",
        title="整理上海商务出差",
        input=requested.model_dump(mode="json", by_alias=True),
        status="running",
        phase="usingTools",
        progress=0.5,
        requiresConfirmation=False,
        attemptCount=1,
        createdAt=now,
        updatedAt=now,
    )
    return LoopToolContext(task=task, state=state or {})


def test_input_requires_unique_commitments_and_valid_calendar_consent() -> None:
    commitment = trip_input().commitments[0].model_dump(mode="json", by_alias=True)
    with pytest.raises(ValidationError, match="commitment ids must be unique"):
        trip_input(commitments=[commitment, commitment])
    with pytest.raises(ValidationError, match="requires addToCalendar"):
        trip_input(addCalendarAlerts=True)
    with pytest.raises(ValidationError, match="commitment or searchGmail"):
        trip_input(commitments=[])
    gmail_only = trip_input(commitments=[], searchGmail=True)
    assert gmail_only.commitments == []
    assert gmail_only.gmail_query == (
        'after:2026/04/04 before:2026/10/09 "上海" '
        '{机票 航班 flight 酒店 hotel 会议 meeting 预订 booking 行程 itinerary}'
    )


def test_gmail_query_is_canonical_and_cannot_inherit_model_operators() -> None:
    query = build_business_trip_gmail_query(
        "上海 from:me to:me",
        datetime.fromisoformat("2026-10-01").date(),
        datetime.fromisoformat("2026-10-03").date(),
    )
    requested = trip_input(
        commitments=[],
        endDate="2026-10-03",
        gmailQuery="from:me to:me after:2026/09/30 before:2026/10/04",
    )

    assert "from:" not in query
    assert "to:" not in query
    assert query.startswith("after:2026/04/04 before:2026/10/11")
    assert requested.search_gmail is True
    assert "from:" not in (requested.gmail_query or "")
    assert "to:" not in (requested.gmail_query or "")
    assert (requested.gmail_query or "").startswith(
        "after:2026/04/04 before:2026/10/11"
    )


def test_detects_conflicts_without_changing_fixed_commitments() -> None:
    requested = trip_input(commitments=[
        {
            "id": "meeting-1",
            "kind": "meeting",
            "title": "客户方案会",
            "startAt": "2026-10-01T14:00:00+08:00",
            "endAt": "2026-10-01T15:00:00+08:00",
        },
        {
            "id": "meeting-2",
            "kind": "meeting",
            "title": "内部复盘",
            "startAt": "2026-10-01T14:30:00+08:00",
            "endAt": "2026-10-01T15:30:00+08:00",
        },
    ])

    conflicts = detect_commitment_conflicts(requested)

    assert conflicts == [{
        "firstCommitmentId": "meeting-1",
        "secondCommitmentId": "meeting-2",
        "message": "客户方案会 与 内部复盘 时间重叠",
    }]


def test_hotel_occupancy_does_not_create_false_schedule_conflicts() -> None:
    requested = trip_input(commitments=[
        {
            "id": "hotel-1",
            "kind": "hotel",
            "title": "上海酒店入住",
            "startAt": "2026-10-01T12:00:00+08:00",
            "endAt": "2026-10-02T10:00:00+08:00",
        },
        {
            "id": "meeting-1",
            "kind": "meeting",
            "title": "客户方案会",
            "startAt": "2026-10-01T14:00:00+08:00",
            "endAt": "2026-10-01T15:00:00+08:00",
        },
    ], endDate="2026-10-02")

    assert detect_commitment_conflicts(requested) == []


def test_validation_preserves_fixed_items_and_rejects_flexible_overlap() -> None:
    requested = trip_input()
    invalid = plan(days=[{
        "date": "2026-10-01",
        "theme": "客户会议",
        "items": [
            {
                "kind": "recommended",
                "name": "上海咖啡店",
                "placeName": "上海咖啡店",
                "startAt": "2026-10-01T13:30:00+08:00",
                "endAt": "2026-10-01T14:30:00+08:00",
            },
            {
                "kind": "fixed",
                "name": "被修改的会议",
                "startAt": "2026-10-01T14:00:00+08:00",
                "endAt": "2026-10-01T15:00:00+08:00",
                "sourceCommitmentId": "meeting-1",
            },
        ],
    }])

    issues = validate_business_trip_plan(invalid, requested, {})

    assert any("must keep title" in issue for issue in issues)
    assert any("call places_search" in issue for issue in issues)
    assert any("overlaps" in issue for issue in issues)


def test_transfer_overlap_explains_how_to_escape_an_impossible_fixed_conflict() -> None:
    requested = trip_input()
    current = plan(days=[{
        "date": "2026-10-01",
        "theme": "客户会议",
        "items": [
            {
                "kind": "transfer",
                "name": "前往机场",
                "startAt": "2026-10-01T13:30:00+08:00",
                "endAt": "2026-10-01T14:01:00+08:00",
                "routeId": "route-airport",
                "originPlaceName": "上海市区",
                "destinationPlaceName": "上海虹桥国际机场",
            },
            {
                "kind": "fixed",
                "name": "客户方案会",
                "startAt": "2026-10-01T14:00:00+08:00",
                "endAt": "2026-10-01T15:00:00+08:00",
                "sourceCommitmentId": "meeting-1",
                "location": "上海国际会议中心",
            },
        ],
    }])

    issues = validate_business_trip_plan(current, requested, {})

    overlap = next(issue for issue in issues if issue.startswith("前往机场 overlaps"))
    assert "rerun routes_search with an earlier departureAt" in overlap
    assert "omit this transfer" in overlap


def test_fixed_ambiguous_place_falls_back_to_source_location() -> None:
    requested = trip_input(commitments=[{
        "id": "meeting-ifc",
        "kind": "meeting",
        "title": "内部项目复盘",
        "startAt": "2026-10-01T14:30:00+08:00",
        "endAt": "2026-10-01T15:30:00+08:00",
        "location": "上海国金中心",
    }])
    current = plan(days=[{
        "date": "2026-10-01",
        "theme": "项目复盘",
        "items": [{
            "kind": "fixed",
            "name": "内部项目复盘",
            "startAt": "2026-10-01T14:30:00+08:00",
            "endAt": "2026-10-01T15:30:00+08:00",
            "sourceCommitmentId": "meeting-ifc",
            "location": "上海国金中心",
            "placeName": "上海国金中心",
        }],
    }])

    assert validate_business_trip_plan(current, requested, {}) == []
    enriched = enrich_business_trip_plan(current, requested, {})
    item = enriched["days"][0]["items"][0]
    assert item["location"] == "上海国金中心"
    assert item["placeName"] is None
    assert item["place"] is None


def test_fixed_item_must_keep_source_location() -> None:
    requested = trip_input()
    current = plan(days=[{
        "date": "2026-10-01",
        "theme": "客户会议",
        "items": [{
            "kind": "fixed",
            "name": "客户方案会",
            "startAt": "2026-10-01T14:00:00+08:00",
            "endAt": "2026-10-01T15:00:00+08:00",
            "sourceCommitmentId": "meeting-1",
            "location": "被修改的地点",
        }],
    }])

    assert validate_business_trip_plan(current, requested, {}) == [
        "fixed commitment meeting-1 must keep location 上海国际会议中心"
    ]


async def test_submit_enriches_verified_places_and_injects_conflicts(
    anyio_backend,
) -> None:
    requested = trip_input()
    current = plan(days=[{
        "date": "2026-10-01",
        "theme": "客户会议",
        "items": [
            {
                "kind": "recommended",
                "name": "午间咖啡",
                "placeName": "星巴克臻选上海烘焙工坊",
                "startAt": "2026-10-01T11:30:00+08:00",
                "endAt": "2026-10-01T12:30:00+08:00",
            },
            {
                "kind": "fixed",
                "name": "客户方案会",
                "startAt": "2026-10-01T14:00:00+08:00",
                "endAt": "2026-10-01T15:00:00+08:00",
                "sourceCommitmentId": "meeting-1",
                "location": "上海国际会议中心",
            },
        ],
    }])
    place = {
        "name": "星巴克臻选上海烘焙工坊",
        "formatted_address": "上海市静安区南京西路789号",
        "map_url": "https://maps.apple.com/?q=coffee",
        "verified": True,
    }
    result = await BusinessTripSubmitTool().execute(
        BusinessTripSubmitArguments(plan=current),
        loop_context(requested, {"places": {"coffee": place}}),
    )

    assert result.observation == {"ok": True, "status": "accepted"}
    assert result.final_output is not None
    assert result.final_output["days"][0]["items"][0]["place"] == place
    assert result.final_output["conflicts"] == []


class FakeGoogleWorkspace:
    def __init__(self) -> None:
        self.gmail_query = None
        self.upload = None
        self.uploads = []

    async def search_gmail(self, query, max_results, **_context):
        from app.tools.business_trip.models import GmailMessage

        self.gmail_query = (query, max_results)
        return [GmailMessage(
            id="gmail-flight-1",
            threadId="thread-1",
            subject="上海机票确认",
            sender="airline@example.com",
            date="2026-09-20",
            snippet="10 月 1 日前往上海",
            bodyText="航班 MU5101，10 月 1 日 09:00 至 11:00。",
        )]

    async def upload_drive_text(self, **values):
        self.upload = values
        self.uploads.append(values)
        return {
            "fileId": "drive-file-1",
            "name": values["name"],
            "webViewLink": "https://drive.google.com/file/d/drive-file-1/view",
            "verified": True,
        }


async def test_gmail_evidence_must_be_searched_and_cited(anyio_backend) -> None:
    requested = trip_input(
        commitments=[], searchGmail=True
    )
    context = loop_context(requested)
    google = FakeGoogleWorkspace()

    searched = await GmailSearchTool(google).execute(  # type: ignore[arg-type]
        GmailSearchArguments(maxResults=10), context
    )
    context.state.update(searched.state_updates)
    commitment = {
        "id": "flight-1",
        "kind": "flight",
        "title": "MU5101 前往上海",
        "startAt": "2026-10-01T09:00:00+08:00",
        "endAt": "2026-10-01T11:00:00+08:00",
        "sourceMessageId": "gmail-flight-1",
    }
    locked = await BusinessTripCommitmentsLockTool().execute(
        BusinessTripCommitmentsLockArguments(commitments=[commitment]), context
    )

    assert google.gmail_query == (
        'after:2026/04/04 before:2026/10/09 "上海" '
        '{机票 航班 flight 酒店 hotel 会议 meeting 预订 booking 行程 itinerary}',
        10,
    )
    assert locked.observation == {"ok": True, "commitmentCount": 1}
    assert locked.state_updates["lockedCommitments"][0]["sourceMessageId"] == (
        "gmail-flight-1"
    )

    invalid = dict(commitment, sourceMessageId="invented-message")
    rejected = await BusinessTripCommitmentsLockTool().execute(
        BusinessTripCommitmentsLockArguments(commitments=[invalid]), context
    )
    assert rejected.observation["error"]["code"] == "invalid_commitment_provenance"

    submit_without_lock = await BusinessTripSubmitTool().execute(
        BusinessTripSubmitArguments(plan=plan()),
        loop_context(requested),
    )
    assert submit_without_lock.observation["error"]["code"] == (
        "gmail_commitments_not_locked"
    )


async def test_gmail_lock_ignores_commitments_outside_trip_dates(
    anyio_backend,
) -> None:
    requested = trip_input(commitments=[], searchGmail=True)
    context = loop_context(requested, {
        "gmailMessages": {
            "gmail-september": {"id": "gmail-september"},
            "gmail-october": {"id": "gmail-october"},
        }
    })
    result = await BusinessTripCommitmentsLockTool().execute(
        BusinessTripCommitmentsLockArguments(commitments=[
            {
                "id": "old-meeting",
                "kind": "meeting",
                "title": "九月会议",
                "startAt": "2026-09-17T09:30:00+08:00",
                "endAt": "2026-09-17T10:30:00+08:00",
                "sourceMessageId": "gmail-september",
            },
            {
                "id": "current-meeting",
                "kind": "meeting",
                "title": "十月会议",
                "startAt": "2026-10-01T14:00:00+08:00",
                "endAt": "2026-10-01T15:00:00+08:00",
                "sourceMessageId": "gmail-october",
            },
        ]),
        context,
    )

    assert result.observation == {
        "ok": True,
        "commitmentCount": 1,
        "ignoredOutOfScopeCommitmentIds": ["old-meeting"],
    }
    assert [item["id"] for item in result.state_updates["lockedCommitments"]] == [
        "current-meeting"
    ]


async def test_submit_uploads_validated_markdown_to_drive(anyio_backend) -> None:
    requested = trip_input(uploadToDrive=True, driveFolderId="folder-1")
    google = FakeGoogleWorkspace()
    result = await BusinessTripSubmitTool(google).execute(  # type: ignore[arg-type]
        BusinessTripSubmitArguments(plan=plan()),
        loop_context(requested),
    )

    assert result.final_output["driveFile"]["verified"] is True
    assert google.upload["folder_id"] == "folder-1"
    assert google.upload["name"] == "上海商务出差计划.pdf"
    assert google.upload["mime_type"] == "application/pdf"
    assert "# 上海商务出差计划" in google.upload["content"]


async def test_receipts_are_deduplicated_and_uploaded_as_a_second_pdf(
    anyio_backend,
) -> None:
    requested = trip_input(
        uploadToDrive=True,
        receipts=[
            {
                "id": "taxi-1",
                "category": "taxi",
                "merchant": "上海强生出租车",
                "transactionDate": "2026-10-01",
                "amount": "86.50",
                "currency": "CNY",
                "invoiceNumber": "INV-001",
                "sourceLabel": "相册图片 1",
            },
            {
                "id": "taxi-copy",
                "category": "taxi",
                "merchant": "上海强生出租车",
                "transactionDate": "2026-10-01",
                "amount": "86.50",
                "currency": "cny",
                "invoiceNumber": "INV 001",
                "sourceLabel": "相册图片 2",
            },
            {
                "id": "meal-1",
                "category": "meal",
                "merchant": "上海餐厅",
                "transactionDate": "2026-10-01",
                "amount": "128.00",
                "currency": "CNY",
                "orderNumber": "MEAL-001",
                "sourceLabel": "相册图片 3",
            },
        ],
    )
    google = FakeGoogleWorkspace()
    result = await BusinessTripSubmitTool(google).execute(  # type: ignore[arg-type]
        BusinessTripSubmitArguments(plan=plan()),
        loop_context(requested),
    )

    assert result.final_output is not None
    reimbursement = result.final_output["reimbursement"]
    assert reimbursement["receiptCount"] == 2
    assert reimbursement["duplicateCount"] == 1
    assert reimbursement["totalsByCurrency"] == [
        {"currency": "CNY", "amount": "214.50"}
    ]
    assert len(google.uploads) == 2
    assert google.uploads[0]["idempotency_key"].endswith(":itinerary")
    assert google.uploads[1]["idempotency_key"].endswith(":reimbursement")
    assert google.uploads[1]["name"].endswith("-报销清单.pdf")
    assert len(result.final_output["driveFiles"]) == 2


def test_reimbursement_marks_missing_ids_and_out_of_trip_dates() -> None:
    requested = trip_input(receipts=[{
        "id": "meal-outside",
        "category": "meal",
        "merchant": "测试餐厅",
        "transactionDate": "2026-09-30",
        "amount": "20",
        "sourceLabel": "相册图片 1",
    }])

    report = build_reimbursement_report(requested)
    markdown = render_reimbursement_markdown(report, requested)

    assert report["missingIdentifierReceiptIds"] == ["meal-outside"]
    assert report["outsideTripDateReceiptIds"] == ["meal-outside"]
    assert "CNY 20.00" in markdown
    assert "日期不在本次出差范围内" in markdown


def test_outcome_contains_report_calendar_and_requested_alerts() -> None:
    requested = trip_input(addToCalendar=True, addCalendarAlerts=True)
    output = plan().model_dump(mode="json", by_alias=True)
    output["days"][0]["items"][0]["place"] = None
    output["conflicts"] = []
    context = loop_context(requested)

    outcome = build_business_trip_outcome(context.task, output)

    assert outcome.summary == (
        "上海商务出差计划已完成，整理 1 项固定安排，未发现时间冲突。"
    )
    assert [artifact.content_type for artifact in outcome.artifacts] == [
        "application/vnd.wellphone.business-trip+json",
        "text/markdown",
        "application/vnd.wellphone.business-trip-report+json",
        "application/vnd.wellphone.calendar-events+json",
    ]
    assert "确认号：MEET-001" in outcome.artifacts[3].payload["events"][0]["notes"]
    assert outcome.artifacts[3].payload["events"][0]["alertsBeforeMinutes"] == [30]
    assert outcome.artifacts[3].payload["events"][0]["startAt"].endswith("+08:00")


def test_flight_and_transfer_use_calendar_checkin_and_departure_alerts() -> None:
    requested = trip_input(
        addToCalendar=True,
        addCalendarAlerts=True,
        commitments=[{
            "id": "flight-1",
            "kind": "flight",
            "title": "MU5101 前往上海",
            "startAt": "2026-10-01T09:00:00+08:00",
            "endAt": "2026-10-01T11:00:00+08:00",
        }],
    )
    output = plan(days=[{
        "date": "2026-10-01",
        "theme": "抵达上海",
        "items": [
            {
                "kind": "fixed",
                "name": "MU5101 前往上海",
                "startAt": "2026-10-01T09:00:00+08:00",
                "endAt": "2026-10-01T11:00:00+08:00",
                "sourceCommitmentId": "flight-1",
            },
            {
                "kind": "transfer",
                "name": "前往酒店",
                "startAt": "2026-10-01T11:10:00+08:00",
                "endAt": "2026-10-01T12:00:00+08:00",
            },
        ],
    }]).model_dump(mode="json", by_alias=True)
    output["days"][0]["items"][1]["route"] = {
        "originName": "上海虹桥国际机场 T2",
        "destinationName": "上海浦东嘉里大酒店",
        "expectedTravelTimeMinutes": 50,
        "mapURL": "https://maps.apple.com/?saddr=SHA&daddr=hotel",
    }

    calendar = business_trip_calendar_payload(output, requested)

    assert calendar["events"][0]["alertsBeforeMinutes"] == [1_440, 180]
    assert calendar["events"][1]["alertsBeforeMinutes"] == [15]
    assert calendar["events"][1]["location"] == (
        "上海虹桥国际机场 T2 → 上海浦东嘉里大酒店"
    )
    assert calendar["events"][1]["url"].startswith("https://maps.apple.com/")
    assert "MapKit 预计交通时间：50 分钟" in calendar["events"][1]["notes"]


class FakeCalendar:
    def __init__(self) -> None:
        self.search = None

    async def search_events(
        self, start_at, end_at, max_results, **_context
    ) -> list[CalendarEvent]:
        self.search = (start_at, end_at, max_results)
        return [CalendarEvent.model_validate({
            "id": "calendar-team-sync",
            "title": "产品团队周会",
            "startAt": "2026-10-01T06:30:00Z",
            "endAt": "2026-10-01T07:30:00Z",
            "calendarTitle": "工作",
            "isAllDay": False,
        })]


async def test_calendar_search_requires_authorization_and_uses_task_range(
    anyio_backend,
) -> None:
    calendar = FakeCalendar()
    tool = CalendarEventsSearchTool(calendar)  # type: ignore[arg-type]
    denied = await tool.execute(
        CalendarEventsSearchArguments(), loop_context(trip_input())
    )
    assert denied.observation["error"]["code"] == "calendar_not_authorized"
    assert calendar.search is None

    context = loop_context(trip_input(checkCalendar=True))
    searched = await tool.execute(CalendarEventsSearchArguments(), context)
    assert searched.observation["ok"] is True
    assert searched.state_updates["calendarEvents"][0]["title"] == "产品团队周会"
    assert searched.state_updates["calendarEvents"][0]["startAt"] == (
        "2026-10-01T14:30:00+08:00"
    )
    assert searched.observation["timeZone"] == "Asia/Shanghai"
    assert searched.observation["allDayEventsAreInformational"] is True
    assert calendar.search[0].isoformat() == "2026-10-01T00:00:00+08:00"
    assert calendar.search[1].isoformat() == "2026-10-02T00:00:00+08:00"


def test_calendar_events_create_conflicts_without_becoming_commitments() -> None:
    requested = trip_input(checkCalendar=True)
    event = CalendarEvent.model_validate({
        "id": "calendar-team-sync",
        "title": "产品团队周会",
        "startAt": "2026-10-01T14:30:00+08:00",
        "endAt": "2026-10-01T15:30:00+08:00",
        "calendarTitle": "工作",
    })

    conflicts = detect_commitment_conflicts(requested, [event])

    assert conflicts == [{
        "firstCommitmentId": "meeting-1",
        "secondCalendarEventId": "calendar-team-sync",
        "message": "客户方案会 与日历事件「产品团队周会」时间重叠",
    }]
    output = enrich_business_trip_plan(
        plan(), requested, {"calendarEvents": [event.model_dump(by_alias=True)]}
    )
    assert output["calendarEventCount"] == 1
    assert output["commitmentCount"] == 1


def test_matching_calendar_copy_is_not_reported_as_a_conflict() -> None:
    requested = trip_input(checkCalendar=True)
    copy = CalendarEvent.model_validate({
        "id": "calendar-copy",
        "title": "客户方案会",
        "startAt": "2026-10-01T14:00:00+08:00",
        "endAt": "2026-10-01T15:00:00+08:00",
    })

    assert detect_commitment_conflicts(requested, [copy]) == []


def test_flexible_items_must_avoid_existing_calendar_events() -> None:
    requested = trip_input(checkCalendar=True)
    current = plan(days=[{
        "date": "2026-10-01",
        "theme": "客户会议",
        "items": [
            {
                "kind": "fixed",
                "name": "客户方案会",
                "startAt": "2026-10-01T14:00:00+08:00",
                "endAt": "2026-10-01T15:00:00+08:00",
                "sourceCommitmentId": "meeting-1",
                "location": "上海国际会议中心",
            },
            {
                "kind": "preparation",
                "name": "整理会议纪要",
                "startAt": "2026-10-01T16:15:00+08:00",
                "endAt": "2026-10-01T16:45:00+08:00",
            },
        ],
    }])
    event = CalendarEvent.model_validate({
        "id": "calendar-review",
        "title": "内部评审",
        "startAt": "2026-10-01T16:00:00+08:00",
        "endAt": "2026-10-01T17:00:00+08:00",
    })

    issues = validate_business_trip_plan(
        current, requested, {"calendarEvents": [event.model_dump(by_alias=True)]}
    )

    assert "整理会议纪要 overlaps existing calendar event 内部评审" in issues


def test_all_day_calendar_events_are_informational_not_hard_conflicts() -> None:
    requested = trip_input(checkCalendar=True)
    current = plan(days=[{
        "date": "2026-10-01",
        "theme": "客户会议",
        "items": [
            {
                "kind": "fixed",
                "name": "客户方案会",
                "startAt": "2026-10-01T14:00:00+08:00",
                "endAt": "2026-10-01T15:00:00+08:00",
                "sourceCommitmentId": "meeting-1",
                "location": "上海国际会议中心",
            },
            {
                "kind": "preparation",
                "name": "整理会议纪要",
                "startAt": "2026-10-01T16:15:00+08:00",
                "endAt": "2026-10-01T16:45:00+08:00",
            },
        ],
    }])
    holiday = CalendarEvent.model_validate({
        "id": "calendar-national-day",
        "title": "国庆节（休）",
        "startAt": "2026-09-30T16:00:00Z",
        "endAt": "2026-10-01T15:59:59Z",
        "calendarTitle": "中国大陆节假日",
        "isAllDay": True,
    })
    state = {"calendarEvents": [holiday.model_dump(by_alias=True)]}

    assert validate_business_trip_plan(current, requested, state) == []
    assert detect_commitment_conflicts(requested, [holiday]) == []


class FakeRouteMaps:
    async def directions(
        self, origin, destination, departure_at, transport_type, **_context
    ):
        from app.tools.travel.models import AppleMapsRoute

        return AppleMapsRoute.model_validate({
            "id": "route-airport-meeting",
            "originName": origin.name,
            "destinationName": destination.name,
            "transportType": transport_type,
            "distanceMeters": 25000,
            "expectedTravelTimeMinutes": 40,
            "departureAt": departure_at,
            "expectedArrivalAt": "2026-10-01T12:40:00+08:00",
            "mapURL": "https://maps.apple.com/directions",
        })


async def test_route_search_and_transfer_duration_are_deterministic(
    anyio_backend,
) -> None:
    requested = trip_input()
    places = {
        "airport": {
            "name": "上海虹桥国际机场",
            "formatted_address": "上海市长宁区虹桥路2550号",
            "latitude": 31.196,
            "longitude": 121.34,
            "map_url": "https://maps.apple.com/airport",
            "verified": True,
        },
        "meeting": {
            "name": "上海国际会议中心",
            "formatted_address": "上海市浦东新区滨江大道2727号",
            "latitude": 31.239,
            "longitude": 121.497,
            "map_url": "https://maps.apple.com/meeting",
            "verified": True,
        },
    }
    context = loop_context(requested, {"places": places})
    from app.tools.travel.models import RouteSearchArguments

    searched = await RoutesSearchTool(FakeRouteMaps()).execute(  # type: ignore[arg-type]
        RouteSearchArguments.model_validate({
            "originPlaceName": "上海虹桥国际机场",
            "destinationPlaceName": "上海国际会议中心",
            "departureAt": "2026-10-01T12:00:00+08:00",
            "transportType": "automobile",
        }),
        context,
    )
    context.state.update(searched.state_updates)
    current = plan(days=[{
        "date": "2026-10-01",
        "theme": "客户会议",
        "items": [
            {
                "kind": "transfer",
                "name": "机场前往会议地点",
                "startAt": "2026-10-01T12:00:00+08:00",
                "endAt": "2026-10-01T12:45:00+08:00",
                "routeId": "route-airport-meeting",
                "originPlaceName": "上海虹桥国际机场",
                "destinationPlaceName": "上海国际会议中心",
            },
            {
                "kind": "fixed",
                "name": "客户方案会",
                "startAt": "2026-10-01T14:00:00+08:00",
                "endAt": "2026-10-01T15:00:00+08:00",
                "sourceCommitmentId": "meeting-1",
                "location": "上海国际会议中心",
            },
        ],
    }])

    assert searched.observation["route"]["expectedTravelTimeMinutes"] == 40
    assert validate_business_trip_plan(current, requested, context.state) == []


async def test_submit_requires_calendar_search_when_requested(anyio_backend) -> None:
    result = await BusinessTripSubmitTool().execute(
        BusinessTripSubmitArguments(plan=plan()),
        loop_context(trip_input(checkCalendar=True)),
    )

    assert result.observation["error"]["code"] == "calendar_search_required"
