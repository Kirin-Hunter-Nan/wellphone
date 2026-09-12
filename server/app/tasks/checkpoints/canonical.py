"""Canonical checkpoint serialization used for idempotency checks."""

import hashlib
import json

from app.api.protocol import TaskCheckpointSubmission


def checkpoint_fingerprint(checkpoint: TaskCheckpointSubmission) -> str:
    canonical = json.dumps(
        checkpoint.semantic_payload(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
