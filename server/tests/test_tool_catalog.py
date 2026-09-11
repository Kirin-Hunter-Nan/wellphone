import json
from pathlib import Path

import pytest

from app.tools.catalog import ModelToolCatalog, ToolCatalogError


def test_maps_model_tool_to_platform_capability() -> None:
    catalog = ModelToolCatalog()
    request = catalog.normalize(
        model_name="reminder_create",
        tool_call_id="call_123",
        arguments_json=json.dumps({
            "title": " 提交报销 ",
            "dueAt": "2099-09-11T15:00:00+08:00",
        }),
    )

    assert request.tool_call_id == "call_123"
    assert request.capability == "reminder.create"
    assert request.arguments["title"] == "提交报销"
    assert request.arguments["dueAt"] == "2099-09-11T15:00:00+08:00"


def test_rejects_unknown_tools_and_offset_free_dates() -> None:
    catalog = ModelToolCatalog()
    with pytest.raises(ToolCatalogError, match="Unregistered"):
        catalog.normalize(
            model_name="calendar_delete",
            tool_call_id="call_123",
            arguments_json="{}",
        )

    with pytest.raises(ToolCatalogError, match="Invalid arguments"):
        catalog.normalize(
            model_name="reminder_create",
            tool_call_id="call_123",
            arguments_json='{"title":"x","dueAt":"2099-09-11T15:00:00"}',
        )


def test_model_schema_matches_shared_capability_contract() -> None:
    catalog_schema = ModelToolCatalog().model_definitions()[0]["function"]["parameters"]
    shared_path = (
        Path(__file__).resolve().parents[2]
        / "shared/schemas/capabilities/reminder.create.schema.json"
    )
    shared_schema = json.loads(shared_path.read_text())

    assert catalog_schema["additionalProperties"] is False
    assert catalog_schema["required"] == shared_schema["required"]
    assert set(catalog_schema["properties"]) == set(shared_schema["properties"])
    assert catalog_schema["properties"]["dueAt"]["format"] == "date-time"
