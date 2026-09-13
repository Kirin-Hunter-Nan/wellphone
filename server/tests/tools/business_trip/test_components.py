"""Business-trip schemas, validation, submission, and artifact tests."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.agent.tools import LoopToolContext
from app.tasks.models import ServerTask
from app.tools.business_trip.artifacts import build_business_trip_outcome
from app.tools.business_trip.models import (
    BusinessTripCommitmentsLockArguments,
    BusinessTripInput,
    BusinessTripPlanOutput,
    BusinessTripSubmitArguments,
    GmailSearchArguments,
    build_business_trip_gmail_query,
)
from app.tools.business_trip.tools import (
    BusinessTripCommitmentsLockTool,
    BusinessTripSubmitTool,
    GmailSearchTool,
)
from app.tools.business_trip.validation import (
    detect_commitment_conflicts,
    enrich_business_trip_plan,
    validate_business_trip_plan,
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
