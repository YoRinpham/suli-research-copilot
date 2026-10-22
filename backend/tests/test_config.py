import pytest
from pydantic import ValidationError

from app.config.settings import Settings


def test_configuration_loads_environment(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pass@localhost:5432/testdb")
    monkeypatch.setenv("CHUNK_SIZE", "640")
    monkeypatch.setenv("CHUNK_OVERLAP", "80")
    monkeypatch.setenv("QWEN_API_KEY", "")
    monkeypatch.setenv("REQUEST_BODY_LIMIT_MB", "32")
    monkeypatch.setenv("PARSE_STALE_AFTER_SECONDS", "900")
    settings = Settings(_env_file=None)
    assert settings.database_url.endswith("/testdb")
    assert settings.chunk_size == 640
    assert settings.chunk_overlap == 80
    assert settings.qwen_api_key is None
    assert settings.request_body_limit_mb == 32
    assert settings.parse_stale_after_seconds == 900


def test_default_embedding_configuration_uses_native_qwen37(monkeypatch) -> None:
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("EMBEDDING_BASE_URL", raising=False)
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://user:pass@localhost/testdb")
    assert settings.embedding_model == "qwen3.7-text-embedding"
    assert settings.embedding_base_url.endswith(
        "/api/v1/services/embeddings/text-embedding/text-embedding"
    )


def test_default_scientific_model_is_qwen38_max(monkeypatch) -> None:
    monkeypatch.delenv("QWEN_MODEL", raising=False)
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://user:pass@localhost/testdb")
    assert settings.qwen_model == "qwen3.8-max"


def test_qwen_reasoning_effort_is_optional_and_validated(monkeypatch) -> None:
    monkeypatch.delenv("QWEN_REASONING_EFFORT", raising=False)
    kwargs = {"_env_file": None, "database_url": "postgresql+psycopg://user:pass@localhost/testdb"}
    assert Settings(**kwargs).qwen_reasoning_effort is None
    for value in ("xhigh", "medium", "low"):
        monkeypatch.setenv("QWEN_REASONING_EFFORT", value)
        assert Settings(**kwargs).qwen_reasoning_effort == value
    monkeypatch.setenv("QWEN_REASONING_EFFORT", "invalid")
    with pytest.raises(ValidationError):
        Settings(**kwargs)


def test_storage_root_is_resolved_from_repository_root(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pass@localhost:5432/testdb")
    monkeypatch.setenv("STORAGE_ROOT", "data/pdfs")
    settings = Settings(_env_file=None)
    assert settings.storage_root.is_absolute()
    assert settings.storage_root.name == "pdfs"
    assert settings.storage_root.parent.name == "data"


def test_configuration_rejects_overlap_at_least_chunk_size(monkeypatch) -> None:
    monkeypatch.setenv("CHUNK_SIZE", "100")
    monkeypatch.setenv("CHUNK_OVERLAP", "100")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_configuration_rejects_default_top_k_above_maximum(monkeypatch) -> None:
    monkeypatch.setenv("RETRIEVAL_TOP_K", "21")
    monkeypatch.setenv("RETRIEVAL_MAX_TOP_K", "20")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
