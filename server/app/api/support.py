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
    supported = {
        "travel.plan": ("旅行规划", "行程"),
        "business-trip.plan": ("商务出差任务", "出差计划"),
    }
    labels = supported.get(submission.capability)
    if labels is None:
        return None
    task_label, artifact_label = labels
    if submission.status == "verified":
        summary = f"{task_label}已经完成。"
        itinerary_text: str | None = None
        uploaded_files: list[tuple[str, str]] = []
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
                    reference = artifact.get("storageReference")
                    if isinstance(reference, str) and reference.startswith("https://"):
                        uploaded_files.append((
                            str(artifact.get("title") or "已上传文件"), reference
                        ))
        if itinerary_text:
            title, body = _split_markdown_title(itinerary_text, task_label)
            sections = [title, summary]
            if body:
                sections.append(body)
            if uploaded_files:
                sections.append(
                    "## 文件\n\n"
                    + "\n".join(
                        f"- [在 Google Drive 中打开已上传的 PDF：{title}]({reference})"
                        for title, reference in uploaded_files
                    )
                )
            return "\n\n".join(sections)
        return (
            f"{summary}\n\n当前结果没有可展示的文本{artifact_label}，"
            "请在任务详情中查看。"
        )
    if submission.status == "declined":
        return f"{task_label}已取消，没有生成或写入任何{artifact_label}。"
    message = submission.error.message if submission.error else f"{task_label}暂时未能完成。"
    return f"{task_label}失败：{message}"


def _split_markdown_title(markdown: str, fallback_title: str) -> tuple[str, str]:
    lines = markdown.splitlines()
    if lines and lines[0].startswith("# "):
        title = lines[0]
        body = "\n".join(lines[1:]).strip()
        return title, body
    return f"# {fallback_title}", markdown.strip()
