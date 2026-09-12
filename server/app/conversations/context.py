"""Conversation history reconstruction, fingerprinting, and context limits."""

from __future__ import annotations

import hashlib
import json

from app.api.protocol import ChatMessage, ChatRequest

def _request_fingerprint(request: ChatRequest) -> str:
    payload = request.model_dump(
        mode="json",
        by_alias=True,
        exclude={"request_id"},
    )
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _context_messages(
    rows: list[tuple[object, object]],
    fallback: ChatRequest,
) -> list[ChatMessage]:
    requests: list[tuple[ChatRequest, str | None]] = []
    for request_json, assistant_content in rows:
        if not isinstance(request_json, str):
            continue
        try:
            stored_request = ChatRequest.model_validate_json(request_json)
        except ValueError:
            continue
        requests.append((
            stored_request,
            assistant_content if isinstance(assistant_content, str) else None,
        ))
    if not requests:
        return list(fallback.messages)

    messages = list(requests[0][0].messages)
    if requests[0][1]:
        messages.append(ChatMessage(role="assistant", content=requests[0][1]))
    for stored_request, assistant_content in requests[1:]:
        messages.append(stored_request.messages[-1])
        if assistant_content:
            messages.append(ChatMessage(role="assistant", content=assistant_content))
    return messages


def _trim_messages(
    messages: list[ChatMessage],
    *,
    max_messages: int,
    max_characters: int,
) -> list[ChatMessage]:
    selected: list[ChatMessage] = []
    character_count = 0
    for message in reversed(messages):
        content = message.model_dump(mode="json")["content"]
        size = len(
            content
            if isinstance(content, str)
            else json.dumps(content, ensure_ascii=False, separators=(",", ":"))
        )
        if selected and (
            len(selected) >= max_messages
            or character_count + size > max_characters
        ):
            break
        selected.append(message)
        character_count += size
    return list(reversed(selected))
