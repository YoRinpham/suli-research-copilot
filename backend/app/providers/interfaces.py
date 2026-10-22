from collections.abc import Mapping, Sequence
from typing import Protocol, TypeVar

from pydantic import BaseModel
from dataclasses import dataclass


OutputT = TypeVar("OutputT", bound=BaseModel)


@dataclass(frozen=True)
class LLMResult:
    output: BaseModel
    model_name: str
    attempt_count: int


class LLMProvider(Protocol):
    def generate_structured(
        self,
        prompt: str,
        output_schema: type[OutputT],
        context: Mapping[str, object],
    ) -> LLMResult: ...


class EmbeddingProvider(Protocol):
    @property
    def model_name(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...
