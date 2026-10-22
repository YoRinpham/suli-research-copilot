"""DashScope-native adapter for the M2 1024-dimensional dense embedding space."""

import math
from collections.abc import Sequence

import httpx

from app.embedding_space import EMBEDDING_DIMENSIONS

DEFAULT_EMBEDDING_URL = "https://dashscope.aliyuncs.com/api/v1/services/embeddings/text-embedding/text-embedding"
RESEARCH_RETRIEVAL_INSTRUCTION = (
    "Given a scientific research query, retrieve relevant passages from research papers "
    "that help answer, investigate, compare, or evaluate the query."
)


class EmbeddingError(Exception):
    pass


class EmbeddingTimeout(EmbeddingError):
    pass


def validate_vector(value: object, dimensions: int = EMBEDDING_DIMENSIONS) -> list[float]:
    if not isinstance(value, list) or len(value) != dimensions:
        raise EmbeddingError("Embedding vector has the wrong dimension")
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item) for item in value):
        raise EmbeddingError("Embedding vector contains invalid values")
    vector = [float(item) for item in value]
    if not any(vector):
        raise EmbeddingError("Embedding vector has zero length")
    return vector


class DashScopeEmbeddingProvider:
    dimensions = EMBEDDING_DIMENSIONS

    def __init__(
        self, api_key: str, model_name: str, timeout_seconds: int, retry_count: int,
        *, base_url: str = DEFAULT_EMBEDDING_URL, client: httpx.Client | None = None,
    ) -> None:
        self.model_name = model_name
        self.timeout_seconds = timeout_seconds
        self.retry_count = retry_count
        self.base_url = base_url
        self.api_key = api_key
        self.client = client or httpx.Client()

    def _embed(self, texts: Sequence[str], *, text_type: str) -> list[list[float]]:
        if not texts:
            return []
        if not self.api_key:
            raise EmbeddingError("Embedding provider credential is not configured")
        result: list[list[float]] = []
        for offset in range(0, len(texts), 20):
            batch = list(texts[offset:offset + 20])
            parameters: dict[str, str | int] = {
                "text_type": text_type, "dimension": self.dimensions, "output_type": "dense",
            }
            if text_type == "query":
                parameters["instruct"] = RESEARCH_RETRIEVAL_INSTRUCTION
            for attempt in range(self.retry_count + 1):
                try:
                    response = self.client.post(
                        self.base_url,
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        json={"model": self.model_name, "input": {"texts": batch}, "parameters": parameters},
                        timeout=self.timeout_seconds,
                    )
                    response.raise_for_status()
                    payload = response.json()
                    if not isinstance(payload, dict):
                        raise EmbeddingError("Embedding provider returned an invalid response")
                    if payload.get("status_code", 200) != 200 or payload.get("code"):
                        raise EmbeddingError("Embedding provider request failed")
                    if payload.get("model", self.model_name) != self.model_name:
                        raise EmbeddingError("Embedding provider returned a different model")
                    output = payload["output"]
                    if not isinstance(output, dict):
                        raise EmbeddingError("Embedding provider returned an invalid response")
                    data = output["embeddings"]
                    if not isinstance(data, list) or len(data) != len(batch):
                        raise EmbeddingError("Embedding provider returned an incomplete batch")
                    if any(not isinstance(item, dict) or type(item.get("text_index")) is not int for item in data):
                        raise EmbeddingError("Embedding provider returned invalid indices")
                    ordered = sorted(data, key=lambda item: item["text_index"])
                    if [item["text_index"] for item in ordered] != list(range(len(batch))):
                        raise EmbeddingError("Embedding provider returned invalid indices")
                    result.extend(validate_vector(item["embedding"], self.dimensions) for item in ordered)
                    break
                except httpx.TimeoutException as exc:
                    if attempt == self.retry_count:
                        raise EmbeddingTimeout("Embedding provider timed out") from exc
                except httpx.HTTPStatusError as exc:
                    if attempt == self.retry_count or (exc.response.status_code < 500 and exc.response.status_code != 429):
                        raise EmbeddingError("Embedding provider request failed") from exc
                except (httpx.RequestError, ValueError, KeyError, TypeError) as exc:
                    if attempt == self.retry_count:
                        raise EmbeddingError("Embedding provider returned an invalid response") from exc
        return result

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._embed(texts, text_type="document")

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], text_type="query")[0]
