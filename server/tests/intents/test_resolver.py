"""Conversation intent resolution tests."""

import json

import pytest

from app.intents.models import ActionIntent, ChatIntent, ClarifyIntent
from app.intents.resolver import CLARIFY_TOOL_NAME, IntentResolutionError, IntentResolver
from app.tools.catalog import ModelToolCatalog


def resolver() -> IntentResolver:
    return IntentResolver(ModelToolCatalog())


def test_resolves_plain_model_text_as_chat() -> None:
    result = resolver().resolve_chat("这是一个普通回答。")

    assert isinstance(result, ChatIntent)
    assert result.kind == "chat"


def test_rejects_empty_chat_result() -> None:
    with pytest.raises(IntentResolutionError, match="requires assistant text"):
        resolver().resolve_chat("  ")


def test_resolves_registered_tool_as_action() -> None:
    result = resolver().resolve_tool(
        model_name="reminder_create",
        tool_call_id="call_1",
        arguments_json=json.dumps({
            "title": "提交报销",
            "dueAt": "2099-09-11T15:00:00+08:00",
        }),
    )

    assert isinstance(result, ActionIntent)
    assert result.kind == "action"
    assert result.request.capability == "reminder.create"


def test_resolves_missing_action_fields_as_clarification() -> None:
    result = resolver().resolve_tool(
        model_name=CLARIFY_TOOL_NAME,
        tool_call_id="call_2",
        arguments_json=json.dumps({
            "capability": "travel.plan",
            "question": "你想从哪一天旅行到哪一天？",
            "missingFields": ["startDate", "endDate", "startDate"],
        }),
    )

    assert isinstance(result, ClarifyIntent)
    assert result.kind == "clarify"
    assert result.capability == "travel.plan"
    assert result.missing_fields == ("startDate", "endDate")


def test_rejects_clarification_for_unsupported_capability() -> None:
    with pytest.raises(IntentResolutionError, match="Unsupported clarification"):
        resolver().resolve_tool(
            model_name=CLARIFY_TOOL_NAME,
            tool_call_id="call_3",
            arguments_json=json.dumps({
                "capability": "mail.send",
                "question": "收件人是谁？",
                "missingFields": ["recipient"],
            }),
        )


def test_rejects_blank_clarification_question() -> None:
    with pytest.raises(IntentResolutionError, match="Invalid clarification"):
        resolver().resolve_tool(
            model_name=CLARIFY_TOOL_NAME,
            tool_call_id="call_4",
            arguments_json=json.dumps({
                "capability": "travel.plan",
                "question": "   ",
                "missingFields": ["startDate"],
            }),
        )


def test_intent_definitions_include_clarification_contract() -> None:
    definitions = resolver().model_definitions()
    clarification = next(
        item["function"]
        for item in definitions
        if item["function"]["name"] == CLARIFY_TOOL_NAME
    )

    capability = clarification["parameters"]["properties"]["capability"]
    assert capability["enum"] == [
        "reminder.create", "travel.plan", "business-trip.plan"
    ]
