from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse

from dotenv import load_dotenv


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Settings:
    api_key: str
    base_url: str
    model: str
    host: str = "127.0.0.1"
    port: int = 8787
    max_request_bytes: int = 26_214_400
    chat_request_lease_seconds: int = 150
    conversation_context_messages: int = 40
    conversation_context_characters: int = 32_000
    continuation_lease_seconds: int = 150
    task_worker_lease_seconds: int = 300
    task_worker_poll_seconds: int = 2
    task_worker_max_attempts: int = 3
    apple_maps_token: str | None = None
    database_url: str = "postgresql://wellphone:wellphone-local-dev@127.0.0.1:5432/wellphone"


def load_settings(environment: Mapping[str, str] | None = None) -> Settings:
    if environment is None:
        load_dotenv(Path(__file__).resolve().parents[1] / ".env")
        environment = os.environ

    api_key = _required("DASHSCOPE_API_KEY", environment)
    base_url = _required("QWEN_BASE_URL", environment).rstrip("/") + "/"
    parsed_url = urlparse(base_url)
    if parsed_url.scheme != "https" or not parsed_url.netloc:
        raise ConfigurationError("QWEN_BASE_URL must be an absolute HTTPS URL")

    return Settings(
        api_key=api_key,
        base_url=base_url,
        model=_required("QWEN_MODEL", environment),
        host=environment.get("HOST", "").strip() or "127.0.0.1",
        port=_positive_integer(environment.get("PORT"), 8787, "PORT"),
        max_request_bytes=_positive_integer(
            environment.get("MAX_REQUEST_BYTES"),
            26_214_400,
            "MAX_REQUEST_BYTES",
        ),
        chat_request_lease_seconds=_positive_integer(
            environment.get("CHAT_REQUEST_LEASE_SECONDS"),
            150,
            "CHAT_REQUEST_LEASE_SECONDS",
        ),
        conversation_context_messages=_positive_integer(
            environment.get("CONVERSATION_CONTEXT_MESSAGES"),
            40,
            "CONVERSATION_CONTEXT_MESSAGES",
        ),
        conversation_context_characters=_positive_integer(
            environment.get("CONVERSATION_CONTEXT_CHARACTERS"),
            32_000,
            "CONVERSATION_CONTEXT_CHARACTERS",
        ),
        continuation_lease_seconds=_positive_integer(
            environment.get("CONTINUATION_LEASE_SECONDS"),
            150,
            "CONTINUATION_LEASE_SECONDS",
        ),
        task_worker_lease_seconds=_positive_integer(
            environment.get("TASK_WORKER_LEASE_SECONDS"),
            300,
            "TASK_WORKER_LEASE_SECONDS",
        ),
        task_worker_poll_seconds=_positive_integer(
            environment.get("TASK_WORKER_POLL_SECONDS"),
            2,
            "TASK_WORKER_POLL_SECONDS",
        ),
        task_worker_max_attempts=_positive_integer(
            environment.get("TASK_WORKER_MAX_ATTEMPTS"),
            3,
            "TASK_WORKER_MAX_ATTEMPTS",
        ),
        apple_maps_token=environment.get("APPLE_MAPS_TOKEN", "").strip() or None,
        database_url=environment.get("DATABASE_URL", "").strip()
        or "postgresql://wellphone:wellphone-local-dev@127.0.0.1:5432/wellphone",
    )


def _required(name: str, environment: Mapping[str, str]) -> str:
    value = environment.get(name, "").strip()
    if not value:
        raise ConfigurationError(f"Missing required environment variable: {name}")
    return value


def _positive_integer(value: str | None, fallback: int, name: str) -> int:
    if value is None or value == "":
        return fallback
    try:
        parsed = int(value)
    except ValueError as error:
        raise ConfigurationError(f"{name} must be a positive integer") from error
    if parsed <= 0:
        raise ConfigurationError(f"{name} must be a positive integer")
    return parsed
