from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.embedding_space import MVP_EMBEDDING_MODEL


ROOT_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_ENV_FILE, extra="ignore")

    database_url: str
    qwen_api_key: SecretStr | None = None
    qwen_model: str = "qwen3.8-max"
    qwen_reasoning_effort: Literal["xhigh", "medium", "low"] | None = None
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    llm_max_context_chars: int = Field(default=30000, gt=0)
    llm_max_input_items: int = Field(default=20, gt=0)
    embedding_model: str = MVP_EMBEDDING_MODEL
    embedding_base_url: str = "https://dashscope.aliyuncs.com/api/v1/services/embeddings/text-embedding/text-embedding"
    chunk_size: int = Field(default=800, gt=0)
    chunk_overlap: int = Field(default=100, ge=0)
    retrieval_top_k: int = Field(default=8, gt=0)
    retrieval_max_top_k: int = Field(default=20, gt=0)
    upload_size_limit_mb: int = Field(default=25, gt=0)
    request_body_limit_mb: int = Field(default=30, gt=0)
    parse_stale_after_seconds: int = Field(default=600, gt=0)
    embedding_stale_after_seconds: int = Field(default=600, gt=0)
    page_limit: int = Field(default=100, gt=0)
    storage_root: Path = ROOT_ENV_FILE.parent / "data" / "pdfs"
    m5_storage_root: Path = ROOT_ENV_FILE.parent / "data" / "m5"
    csv_size_limit_mb: int = Field(default=5, gt=0)
    csv_row_limit: int = Field(default=50000, gt=0)
    csv_column_limit: int = Field(default=100, gt=0)
    min_extracted_chars: int = Field(default=30, ge=0)
    min_text_page_ratio: float = Field(default=0.5, ge=0, le=1)
    max_replacement_ratio: float = Field(default=0.05, ge=0, le=1)
    provider_timeout_seconds: int = Field(default=60, gt=0)
    retry_count: int = Field(default=2, ge=0)
    cors_origin: str = "http://localhost:3000"

    @field_validator("qwen_api_key", mode="before")
    @classmethod
    def empty_api_key_is_unset(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("database_url")
    @classmethod
    def require_sync_postgresql(cls, value: str) -> str:
        if not value.startswith("postgresql+psycopg://"):
            raise ValueError("DATABASE_URL must use postgresql+psycopg://")
        return value

    @field_validator("storage_root", "m5_storage_root")
    @classmethod
    def resolve_storage_root(cls, value: Path) -> Path:
        return (ROOT_ENV_FILE.parent / value).resolve() if not value.is_absolute() else value.resolve()

    @model_validator(mode="after")
    def validate_chunk_overlap(self) -> "Settings":
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("CHUNK_OVERLAP must be smaller than CHUNK_SIZE")
        if self.request_body_limit_mb <= self.upload_size_limit_mb:
            raise ValueError("REQUEST_BODY_LIMIT_MB must exceed UPLOAD_SIZE_LIMIT_MB for multipart overhead")
        if self.retrieval_top_k > self.retrieval_max_top_k:
            raise ValueError("RETRIEVAL_TOP_K must not exceed RETRIEVAL_MAX_TOP_K")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
