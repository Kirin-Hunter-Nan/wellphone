"""Travel schemas, Maps adapter, validation, and artifact boundary tests."""

from datetime import datetime, timezone
from uuid import uuid4

import httpx

from app.agent.tools import LoopToolContext
from app.tasks.models import ServerTask
from app.tools.travel.artifacts import build_travel_outcome
from app.tools.travel.maps import AppleMapsSearchClient
from app.tools.travel.models import (
    ItinerarySubmitArguments,
    PlacesSearchArguments,
    TravelPlanInput,
    TravelPlanOutput,
)
from app.tools.travel.tools import ItinerarySubmitTool, PlacesSearchTool
from app.tools.travel.validation import validate_plan


def travel_input(**overrides) -> TravelPlanInput:
    values = {
        "destination": "上海",
        "startDate": "2026-10-01",
        "endDate": "2026-10-01",
        "pace": "relaxed",
    }
    values.update(overrides)
    return TravelPlanInput.model_validate(values)


def plan(**overrides) -> TravelPlanOutput:
    values = {
        "title": "上海一日游",
        "overview": "博物馆体验",
        "timeZone": "Asia/Shanghai",
        "days": [{
            "date": "2026-10-01",
            "theme": "文化",
            "items": [{
                "time": "09:00",
                "name": "上海博物馆",
                "durationMinutes": 120,
                "notes": "提前预约",
            }],
        }],
    }
    values.update(overrides)
    return TravelPlanOutput.model_validate(values)


def loop_context(requested: TravelPlanInput, state=None) -> LoopToolContext:
    now = datetime.now(timezone.utc)
    task = ServerTask(
        id=uuid4(), conversationId=uuid4(), capability="travel.plan",
        title="规划旅行", input=requested.model_dump(mode="json", by_alias=True),
        status="running", phase="usingTools", progress=0.5,
        requiresConfirmation=True, attemptCount=1, createdAt=now, updatedAt=now,
    )
    return LoopToolContext(task=task, state=state or {})


async def test_maps_fallback_is_stable_and_does_not_require_a_token(
    anyio_backend,
) -> None:
    maps = AppleMapsSearchClient(token=None)

    place = await maps.search("上海博物馆", "上海")

    assert place.name == "上海博物馆"
    assert place.verified is False
    assert place.map_url.startswith("https://maps.apple.com/?")
    assert "%E4%B8%8A%E6%B5%B7" in place.map_url
    await maps.close()


async def test_maps_response_is_normalized_with_coordinates(anyio_backend) -> None:
    captured: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured
        captured = request
        return httpx.Response(200, json={"results": [{
            "name": "上海博物馆（人民广场馆）",
            "formattedAddress": "上海市黄浦区人民大道201号",
            "coordinate": {"latitude": 31.2304, "longitude": 121.4737},
        }]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    maps = AppleMapsSearchClient(token="maps-token", client=client)

    place = await maps.search("上海博物馆", "上海")

    assert captured is not None
    assert captured.headers["authorization"] == "Bearer maps-token"
    assert place.verified is True
    assert place.formatted_address == "上海市黄浦区人民大道201号"
    assert "ll=31.2304%2C121.4737" in place.map_url
    await client.aclose()


async def test_places_tool_rejects_destination_drift_without_calling_maps(
    anyio_backend,
) -> None:
    class FailingIfCalledMaps:
        async def search(self, *_args, **_kwargs):
            raise AssertionError("Maps must not be called for a mismatched destination")

    result = await PlacesSearchTool(FailingIfCalledMaps()).execute(  # type: ignore[arg-type]
        PlacesSearchArguments(query="故宫", destination="北京"),
        loop_context(travel_input()),
    )

    assert result.observation["ok"] is False
    assert result.observation["error"]["code"] == "destination_mismatch"
    assert result.state_updates == {}


def test_validation_reports_dates_pace_overlap_and_unsearched_places_together() -> None:
    requested = travel_input(endDate="2026-10-02", pace="relaxed")
    invalid = plan(days=[{
        "date": "2026-10-01",
        "theme": "overloaded",
        "items": [
            {"time": "09:00", "name": "A", "durationMinutes": 120},
            {"time": "10:00", "name": "B", "durationMinutes": 60},
            {"time": "11:00", "name": "C", "durationMinutes": 60},
            {"time": "12:00", "name": "D", "durationMinutes": 60},
        ],
    }])

    issues = validate_plan(invalid, requested, {})

    assert any("cover every requested date" in issue for issue in issues)
    assert any("relaxed pace allows 3" in issue for issue in issues)
    assert any("overlapping" in issue for issue in issues)
    assert sum("call places_search" in issue for issue in issues) == 4


def test_validation_rejects_a_place_that_was_searched_but_not_verified() -> None:
    unverified = {
        "name": "上海博物馆",
        "map_url": "https://maps.apple.com/search?query=test",
        "verified": False,
    }

    issues = validate_plan(
        plan(),
        travel_input(),
        {"places": {"上海博物馆": unverified}},
    )

    assert issues == [
        "places_search must verify 上海博物馆 before submitting"
    ]


def test_validation_matches_mapkit_names_across_query_alias_formatting() -> None:
    current_plan = plan(days=[{
        "date": "2026-10-01",
        "theme": "文化漫步",
        "items": [
            {
                "time": "09:00",
                "name": "上海博物馆(人民广场馆)",
                "durationMinutes": 120,
            },
            {
                "time": "13:00",
                "name": "龙美术馆(西岸馆)",
                "durationMinutes": 120,
            },
            {
                "time": "17:00",
                "name": "田子坊泰康路210弄",
                "durationMinutes": 90,
            },
        ],
    }])
    places = {
        "上海博物馆 人民广场": {
            "name": "上海博物馆（人民广场馆）",
            "verified": True,
        },
        "龙美术馆 西岸馆": {
            "name": "龙美术馆（西岸馆）",
            "verified": True,
        },
        "田子坊 泰康路": {
            "name": "田子坊",
            "verified": True,
        },
    }

    issues = validate_plan(
        current_plan,
        travel_input(),
        {"places": places},
    )

    assert issues == []


def test_validation_does_not_fuzzily_match_a_short_generic_alias() -> None:
    issues = validate_plan(
        plan(days=[{
            "date": "2026-10-01",
            "theme": "咖啡",
            "items": [{
                "time": "09:00",
                "name": "咖啡博物馆",
                "durationMinutes": 60,
            }],
        }]),
        travel_input(),
        {"places": {"咖啡": {"name": "另一家咖啡店", "verified": True}}},
    )

    assert issues == ["call places_search for 咖啡博物馆 before submitting"]


async def test_submit_tool_enriches_only_after_all_places_are_verified(
    anyio_backend,
) -> None:
    requested = travel_input()
    current_plan = plan()
    place = {
        "name": "上海博物馆",
        "formatted_address": "上海市黄浦区",
        "latitude": 31.23,
        "longitude": 121.47,
        "map_url": "https://maps.apple.com/?q=test",
        "verified": True,
    }
    context = loop_context(requested, {"places": {"上海博物馆": place}})

    result = await ItinerarySubmitTool().execute(
        ItinerarySubmitArguments(plan=current_plan), context
    )

    assert result.observation == {"ok": True, "status": "accepted"}
    assert result.final_output is not None
    assert result.final_output["days"][0]["items"][0]["place"] == place


def test_travel_outcome_contains_renderable_text_and_calendar_artifacts() -> None:
    requested = travel_input()
    current_plan = plan()
    output = current_plan.model_dump(mode="json", by_alias=True)
    output["days"][0]["items"][0]["place"] = {
        "name": "上海博物馆",
        "formatted_address": "上海市黄浦区",
        "map_url": "https://maps.apple.com/?q=museum",
        "verified": True,
    }
    context = loop_context(requested)

    outcome = build_travel_outcome(context.task, output)

    assert outcome.summary == "上海一日游已完成，共规划 1 天。"
    assert [artifact.kind for artifact in outcome.artifacts] == [
        "itinerary", "text", "json"
    ]
    assert "[在 Apple 地图中打开]" in outcome.artifacts[1].payload
    calendar = outcome.artifacts[2].payload
    assert calendar["timeZone"] == "Asia/Shanghai"
    assert calendar["events"][0] == {
        "title": "上海博物馆",
        "startLocal": "2026-10-01T09:00:00",
        "endLocal": "2026-10-01T11:00:00",
        "location": "上海市黄浦区",
        "url": "https://maps.apple.com/?q=museum",
        "notes": "提前预约",
    }
