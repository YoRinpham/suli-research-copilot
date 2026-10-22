"""Sanitized Qwen transport diagnostics without changing scientific authority."""

import asyncio
import json
import threading
import time

import httpx
import pytest

from app.providers.llm import DashScopeLLMProvider, LLMProviderError, LLMTimeout
from app.services.m5_ai_schemas import CleaningProposal


class TrickleStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        while True:
            await asyncio.sleep(0.02)
            yield b" "


def test_total_deadline_interrupts_continuous_response_chunks() -> None:
    async def trickle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=TrickleStream())

    provider = DashScopeLLMProvider("test-key", "qwen3.8-max", 0.2, 0,
        client=httpx.AsyncClient(transport=httpx.MockTransport(trickle)))
    started = time.monotonic()
    with pytest.raises(LLMTimeout) as caught:
        provider.generate_structured("Return JSON", CleaningProposal, {})
    assert caught.value.category == "provider_total_timeout"
    assert 0.15 <= time.monotonic() - started < 1.0


def test_repeated_total_timeouts_do_not_leave_workers_and_normal_call_succeeds() -> None:
    async def trickle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=TrickleStream())

    before = threading.active_count()
    provider = DashScopeLLMProvider("test-key", "qwen3.8-max", 0.08, 0,
        client=httpx.AsyncClient(transport=httpx.MockTransport(trickle)))
    for _ in range(5):
        with pytest.raises(LLMTimeout, match="timed out"):
            provider.generate_structured("Return JSON", CleaningProposal, {})
    assert threading.active_count() <= before + 1

    async def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"model": "qwen3.8-max", "choices": [{
            "finish_reason": "stop", "message": {"content": json.dumps({
                "operations": [{"op": "drop_exact_duplicates"}], "rationale": "Review first.",
            })},
        }]})
    provider.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    assert provider.generate_structured("Return JSON", CleaningProposal, {}).attempt_count == 1


def test_cleaning_request_uses_configured_model_and_json_schema() -> None:
    requests: list[dict] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"model": "qwen3.8-max", "choices": [{
            "finish_reason": "stop", "message": {"content": json.dumps({
                "operations": [{"op": "normalize_missing", "columns": ["score"], "tokens": ["0"]}],
                "rationale": "Treat the exact token as missing only after human review.",
            })},
        }]})

    provider = DashScopeLLMProvider("test-key", "qwen3.8-max", 5, 0,
        client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    output = provider.generate_structured("Return strict JSON", CleaningProposal,
        {"dataset_version": {"profile": {"row_count": 2, "columns": [{"name": "score"}]}}})
    assert requests[0]["model"] == "qwen3.8-max"
    assert requests[0]["response_format"]["type"] == "json_schema"
    assert requests[0]["response_format"]["json_schema"]["strict"] is True
    assert output.output.operations[0]["tokens"] == ["0"]


def test_connection_failure_has_distinct_sanitized_category() -> None:
    def disconnect(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("synthetic connection failure")

    provider = DashScopeLLMProvider("test-key", "qwen3.8-max", 5, 0,
        client=httpx.AsyncClient(transport=httpx.MockTransport(disconnect)))
    with pytest.raises(LLMProviderError) as caught:
        provider.generate_structured("Return JSON", CleaningProposal, {})
    assert caught.value.category == "provider_connect_error"
    assert caught.value.attempt_count == 1


def test_http_rejection_records_status_without_response_body() -> None:
    provider = DashScopeLLMProvider("test-key", "qwen3.8-max", 5, 0,
        client=httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(401, json={"error": {"message": "secret provider text"}})
        )))
    with pytest.raises(LLMProviderError) as caught:
        provider.generate_structured("Return JSON", CleaningProposal, {})
    assert caught.value.category == "provider_http_401"
    assert caught.value.http_status == 401
    assert "secret provider text" not in str(caught.value)


def test_read_timeout_has_sanitized_transport_category() -> None:
    def time_out(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("synthetic private transport detail")

    provider = DashScopeLLMProvider("test-key", "qwen3.8-max", 5, 0,
        client=httpx.AsyncClient(transport=httpx.MockTransport(time_out)))
    with pytest.raises(LLMTimeout) as caught:
        provider.generate_structured("Return JSON", CleaningProposal, {})
    assert caught.value.category == "provider_read_timeout"
    assert "private transport detail" not in str(caught.value)
