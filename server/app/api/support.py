"""Cancellation-safe persistence and deterministic chat follow-ups."""

import asyncio
from collections.abc import Awaitable
from contextvars import Context
import json

from app.api.protocol import ToolResultSubmission


async def finish_store_operation(operation: Awaitable[None]) -> None:
    """Let persistence cleanup finish even when the request task is cancelled."""
    task = asyncio.create_task(operation, context=Context())
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        return


def assistant_content(events: list[str]) -> str | None:
    chunks: list[str] = []
    for event in events:
        for line in event.splitlines():
            if not line.startswith("data:"):
                continue
            try:
                payload = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            if payload.get("type") == "assistant.delta":
                text = payload.get("text")
                if isinstance(text, str):
                    chunks.append(text)
    content = "".join(chunks).strip()
    return content or None


def deterministic_tool_followup(submission: ToolResultSubmission) -> str | None:
    if submission.capability != "travel.plan":
        return None
    if submission.status == "verified":
        summary = "旅行规划已经完成。"
        itinerary_text: str | None = None
        if submission.result:
            supplied = submission.result.get("summary")
            if isinstance(supplied, str) and supplied.strip():
                summary = supplied.strip()
            artifacts = submission.result.get("artifacts")
            if isinstance(artifacts, list):
                for artifact in artifacts:
                    if not isinstance(artifact, dict):
                        continue
                    payload = artifact.get("payload")
                    if (
                        artifact.get("contentType") == "text/markdown"
                        and isinstance(payload, str)
                        and payload.strip()
                    ):
                        itinerary_text = payload.strip()
                        break
        if itinerary_text:
            return f"{summary}\n\n{itinerary_text}"
        return f"{summary}\n\n当前结果没有可展示的文本行程，请在任务详情中查看。"
    if submission.status == "declined":
        return "旅行规划任务已取消，没有生成或写入任何行程。"
    message = submission.error.message if submission.error else "旅行规划暂时未能完成。"
    return f"旅行规划失败：{message}"
