import pytest

from app.config import ConfigurationError, load_settings


def test_loads_valid_qwen_configuration() -> None:
    settings = load_settings({
        "DASHSCOPE_API_KEY": "test-key",
        "QWEN_BASE_URL": "https://workspace.example.com/compatible-mode/v1",
        "QWEN_MODEL": "qwen-multimodal-test",
        "PORT": "9000",
        "CHAT_REQUEST_LEASE_SECONDS": "170",
        "CONTINUATION_LEASE_SECONDS": "180",
        "DATABASE_URL": "postgresql://test:test@localhost/test",
    })

    assert settings.api_key == "test-key"
    assert settings.base_url == "https://workspace.example.com/compatible-mode/v1/"
    assert settings.model == "qwen-multimodal-test"
    assert settings.host == "127.0.0.1"
    assert settings.port == 9000
    assert settings.chat_request_lease_seconds == 170
    assert settings.continuation_lease_seconds == 180
    assert settings.database_url == "postgresql://test:test@localhost/test"


def test_rejects_missing_secrets_and_insecure_upstream_urls() -> None:
    with pytest.raises(ConfigurationError, match="DASHSCOPE_API_KEY"):
        load_settings({})

    with pytest.raises(ConfigurationError, match="HTTPS"):
        load_settings({
            "DASHSCOPE_API_KEY": "test-key",
            "QWEN_BASE_URL": "http://example.com/v1",
            "QWEN_MODEL": "qwen-test",
        })
