"""Stable Tool Result serialization used for idempotency checks."""

import json

from app.api.protocol import ToolResultSubmission

def _canonical_payload(result: ToolResultSubmission) -> str:
    return _canonical_json(result.semantic_payload())


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
