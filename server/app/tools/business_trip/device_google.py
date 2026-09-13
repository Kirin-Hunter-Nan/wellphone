"""Durable bridge to Google Workspace APIs executed on the user's phone."""

from __future__ import annotations

import asyncio
from time import monotonic
from uuid import UUID

from pydantic import ValidationError

from app.tasks.models import DeviceToolConflictError
from app.tasks.store import ServerTaskStore
from app.tools.business_trip.models import GmailMessage


class DeviceGoogleWorkspaceClient:
    def __init__(
        self,
        store: ServerTaskStore,
        *,
        timeout_seconds: float = 180,
        poll_seconds: float = 0.25,
    ) -> None:
        self._store = store
        self._timeout_seconds = timeout_seconds
        self._poll_seconds = poll_seconds

    async def search_gmail(
        self,
        query: str,
        max_results: int,
        *,
        task_id: UUID | None,
        tool_call_id: str | None,
    ) -> list[GmailMessage]:
        result = await self._execute(
            "google.gmail.search",
            {"query": query, "maxResults": max_results},
            task_id=task_id,
            tool_call_id=tool_call_id,
        )
        messages = result.get("messages")
        if not isinstance(messages, list):
            raise DeviceGoogleWorkspaceError("手机返回的 Gmail 结果缺少 messages。")
        try:
            return [GmailMessage.model_validate(item) for item in messages]
        except ValidationError as error:
            raise DeviceGoogleWorkspaceError("手机返回了无法解析的 Gmail 邮件。") from error

    async def upload_drive_text(
        self,
        *,
        name: str,
        content: str,
        mime_type: str,
        folder_id: str | None,
        task_id: UUID | None,
        tool_call_id: str | None,
        idempotency_key: str | None = None,
    ) -> dict[str, object]:
        arguments: dict[str, object] = {
            "name": name,
            "content": content,
            "mimeType": mime_type,
            "idempotencyKey": idempotency_key or (str(task_id) if task_id else ""),
        }
        if folder_id:
            arguments["folderId"] = folder_id
        result = await self._execute(
            "google.drive.upload-text",
            arguments,
            task_id=task_id,
            tool_call_id=tool_call_id,
        )
        if not isinstance(result.get("fileId"), str):
            raise DeviceGoogleWorkspaceError("Google Drive 上传回执缺少 fileId。")
        if result.get("verified") is not True:
            raise DeviceGoogleWorkspaceError("Google Drive 文件上传后未通过回读校验。")
        return result

    async def _execute(
        self,
        tool_name: str,
        arguments: dict[str, object],
        *,
        task_id: UUID | None,
        tool_call_id: str | None,
    ) -> dict[str, object]:
        if task_id is None or not tool_call_id:
            raise DeviceGoogleWorkspaceError("缺少 Google Workspace 设备工具上下文。")
        await self._store.enqueue_device_tool(
            task_id, tool_call_id, tool_name, arguments
        )
        deadline = monotonic() + self._timeout_seconds
        while monotonic() < deadline:
            call = await self._store.get_device_tool(task_id, tool_call_id)
            if call is None:
                raise DeviceGoogleWorkspaceError("Google Workspace 设备请求丢失。")
            if call.status == "completed":
                if not isinstance(call.result, dict):
                    raise DeviceGoogleWorkspaceError("手机返回了空的 Workspace 结果。")
                return call.result
            if call.status == "failed":
                raise DeviceGoogleWorkspaceError(
                    call.error_message or "手机上的 Google Workspace 操作失败。"
                )
            await asyncio.sleep(self._poll_seconds)

        try:
            await self._store.submit_device_tool_result(
                task_id,
                tool_call_id,
                result=None,
                error_code="device_tool_timeout",
                error_message="等待手机执行 Google Workspace 操作超时。",
            )
        except DeviceToolConflictError:
            call = await self._store.get_device_tool(task_id, tool_call_id)
            if call is not None and call.status == "completed" and isinstance(call.result, dict):
                return call.result
        raise DeviceGoogleWorkspaceError(
            "等待手机执行 Google Workspace 操作超时，请保持手机前台联网。"
        )


class DeviceGoogleWorkspaceError(RuntimeError):
    pass
