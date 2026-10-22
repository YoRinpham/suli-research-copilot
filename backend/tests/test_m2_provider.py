import json

import httpx
import pytest

from app.embedding_space import MVP_EMBEDDING_MODEL
from app.providers.embeddings import (
    RESEARCH_RETRIEVAL_INSTRUCTION,
    DashScopeEmbeddingProvider,
    EmbeddingError,
    EmbeddingTimeout,
    validate_vector,
)


def response_for(request: httpx.Request) -> httpx.Response:
    payload = json.loads(request.content)
    texts = payload["input"]["texts"]
    return httpx.Response(200, json={"status_code": 200, "output": {"embeddings": [
        {"text_index": index, "embedding": [float(index + 1)] * 1024}
        for index, _ in enumerate(texts)
    ]}})


def test_native_document_and_query_roles_and_instruction() -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, json.loads(request.content)))
        return response_for(request)

    provider = DashScopeEmbeddingProvider(
        "secret", MVP_EMBEDDING_MODEL, 5, 0,
        client=httpx.Client(transport=httpx.MockTransport(respond)),
    )
    assert provider.embed_documents(["one", "two"]) == [[1.0] * 1024, [2.0] * 1024]
    assert provider.embed_query("question") == [1.0] * 1024
    assert all(path.endswith("/api/v1/services/embeddings/text-embedding/text-embedding") for path, _ in calls)
    document, query = [payload for _, payload in calls]
    assert document["model"] == query["model"] == "qwen3.7-text-embedding"
    assert document["parameters"] == {
        "text_type": "document", "dimension": 1024, "output_type": "dense",
    }
    assert query["parameters"] == {
        "text_type": "query", "dimension": 1024, "output_type": "dense",
        "instruct": RESEARCH_RETRIEVAL_INSTRUCTION,
    }
    assert document["input"] == {"texts": ["one", "two"]}
    assert query["input"] == {"texts": ["question"]}


def test_native_response_indices_control_document_order() -> None:
    def shuffled(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"output": {"embeddings": [
            {"text_index": 1, "embedding": [2.0] * 1024},
            {"text_index": 0, "embedding": [1.0] * 1024},
        ]}})

    provider = DashScopeEmbeddingProvider(
        "secret", MVP_EMBEDDING_MODEL, 5, 0,
        client=httpx.Client(transport=httpx.MockTransport(shuffled)),
    )
    assert provider.embed_documents(["first", "second"]) == [[1.0] * 1024, [2.0] * 1024]


@pytest.mark.parametrize("entries", [
    [{"text_index": 0, "embedding": [1.0] * 1024}] * 2,
    [{"text_index": 0, "embedding": [1.0] * 1024}, {"text_index": 2, "embedding": [2.0] * 1024}],
    [{"embedding": [1.0] * 1024}, {"text_index": 1, "embedding": [2.0] * 1024}],
])
def test_malformed_native_indices_fail_safely(entries) -> None:
    provider = DashScopeEmbeddingProvider(
        "secret", MVP_EMBEDDING_MODEL, 5, 0,
        client=httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"output": {"embeddings": entries}})
        )),
    )
    with pytest.raises(EmbeddingError):
        provider.embed_documents(["first", "second"])


@pytest.mark.parametrize("vector", [
    [0.1], [1.0] * 1025, [float("nan")] * 1024, [float("inf")] * 1024,
])
def test_invalid_vector_is_explicit(vector) -> None:
    with pytest.raises(EmbeddingError):
        validate_vector(vector, 1024)


def test_provider_failure_is_bounded() -> None:
    attempts = 0

    def fail(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503)

    provider = DashScopeEmbeddingProvider(
        "secret", MVP_EMBEDDING_MODEL, 5, 1,
        client=httpx.Client(transport=httpx.MockTransport(fail)),
    )
    with pytest.raises(EmbeddingError):
        provider.embed_query("question")
    assert attempts == 2


def test_provider_timeout_is_bounded() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("synthetic timeout")

    provider = DashScopeEmbeddingProvider(
        "secret", MVP_EMBEDDING_MODEL, 5, 1,
        client=httpx.Client(transport=httpx.MockTransport(timeout)),
    )
    with pytest.raises(EmbeddingTimeout):
        provider.embed_query("question")


def test_provider_rejects_different_returned_model() -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
        "model": "different-model", "output": {"embeddings": [
            {"text_index": 0, "embedding": [1.0] * 1024},
        ]},
    })))
    provider = DashScopeEmbeddingProvider("secret", MVP_EMBEDDING_MODEL, 5, 0, client=client)
    with pytest.raises(EmbeddingError):
        provider.embed_query("question")
