"""Qwen OpenAI-compatible chat adapter. Provider responses remain untrusted."""

import asyncio
import json
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from hashlib import sha256
from time import monotonic
from typing import Literal
from uuid import uuid4

import httpx
from pydantic import BaseModel, ValidationError

from app.providers.interfaces import LLMResult, OutputT
from app.providers.transport_diagnostics import TransportAttempt


DEFAULT_LLM_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
_trace_callback: ContextVar[Callable[[dict[str, object]], None] | None] = ContextVar("llm_trace_callback", default=None)


_invocation_events: ContextVar[bool] = ContextVar("llm_invocation_events", default=False)


@contextmanager
def trace_provider_calls(callback: Callable[[dict[str, object]], None], *, invocation_events: bool = False) -> Iterator[None]:
    token = _trace_callback.set(callback)
    invocation_token = _invocation_events.set(invocation_events)
    try:
        yield
    finally:
        _invocation_events.reset(invocation_token)
        _trace_callback.reset(token)


def _trace(event: dict[str, object]) -> None:
    callback = _trace_callback.get()
    if callback is not None:
        try:
            callback(event)
        except (Exception, asyncio.CancelledError):
            pass  # Telemetry cannot replace a provider result or transport failure.


def _validation_location(error: ValidationError) -> tuple[str, str]:
    first = error.errors(include_input=False)[0]
    return ".".join(str(part) for part in first["loc"]), str(first["type"])


class LLMProviderError(Exception):
    def __init__(self, message: str, attempt_count: int = 1, *,
                 category: str = "llm_failed", http_status: int | None = None) -> None:
        self.attempt_count = attempt_count
        self.category = category
        self.http_status = http_status
        super().__init__(message)


class LLMTimeout(LLMProviderError):
    pass


class LLMFormatError(LLMProviderError):
    pass


class DashScopeLLMProvider:
    def __init__(
        self, api_key: str, model_name: str, timeout_seconds: int, retry_count: int,
        *, base_url: str = DEFAULT_LLM_URL, client: httpx.AsyncClient | None = None,
        reasoning_effort: Literal["xhigh", "medium", "low"] | None = None,
    ) -> None:
        self.api_key = api_key
        self.model_name = model_name
        self.timeout_seconds = timeout_seconds
        self.retry_count = retry_count
        self.base_url = base_url
        self.client = client
        self.reasoning_effort = reasoning_effort

    async def _post(self, request: dict[str, object], *, diagnostic_call_id: str | None = None,
                    diagnostic_attempt: int = 1) -> httpx.Response:
        try:
            diagnostic = TransportAttempt(diagnostic_call_id, diagnostic_attempt)
        except (Exception, asyncio.CancelledError):
            diagnostic = None
        deadline = asyncio.timeout(self.timeout_seconds)
        owned_client = None
        outcome = "transport_complete"
        failure = None
        try:
            async with deadline:
                if self.client is not None:
                    return await self.client.post(
                        self.base_url, headers={"Authorization": f"Bearer {self.api_key}"},
                        json=request, timeout=self.timeout_seconds,
                        extensions={"trace": diagnostic.trace} if diagnostic is not None else None,
                    )
                async with httpx.AsyncClient() as client:
                    owned_client = client
                    return await client.post(
                        self.base_url, headers={"Authorization": f"Bearer {self.api_key}"},
                        json=request, timeout=self.timeout_seconds,
                        extensions={"trace": diagnostic.trace} if diagnostic is not None else None,
                    )
        except BaseException as exc:
            failure = exc
            outcome = ("outer_deadline" if deadline.expired() else
                       "caller_cancelled" if isinstance(exc, asyncio.CancelledError) else
                       "phase_timeout" if isinstance(exc, httpx.TimeoutException) else "transport_error")
            raise
        finally:
            try:
                state = ("not_owned" if self.client is not None else
                         "closed" if owned_client is not None and owned_client.is_closed else "unknown")
                if diagnostic is not None:
                    record = diagnostic.finish(outcome, deadline.expired(), state, failure)
                    _trace({"layer": "transport", "outcome": "pass" if outcome == "transport_complete" else "fail",
                            "attempt": diagnostic_attempt, "logical_call_id": diagnostic.logical_call_id,
                            "transport_diagnostic": record})
            except (Exception, asyncio.CancelledError):
                pass  # Capture/validation/sink failure does not change the original path.

    def generate_structured(
        self, prompt: str, output_schema: type[OutputT], context: Mapping[str, object],
    ) -> LLMResult:
        logical_call_id = str(uuid4())
        if _invocation_events.get():
            _trace({"layer": "adapter", "outcome": "start", "logical_call_id": logical_call_id})
        if not self.api_key:
            raise LLMProviderError("Qwen credential is not configured", 0,
                                   category="provider_auth_missing")
        request = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": output_schema.__name__, "strict": True, "schema": output_schema.model_json_schema()},
            },
        }
        if self.reasoning_effort is not None:
            request["reasoning_effort"] = self.reasoning_effort
        for attempt in range(1, self.retry_count + 2):
            if _invocation_events.get():
                _trace({"layer": "adapter", "outcome": "attempt_started", "logical_call_id": logical_call_id, "attempt": attempt})
            started = monotonic()
            try:
                response = asyncio.run(self._post(request, diagnostic_call_id=logical_call_id,
                                                  diagnostic_attempt=attempt))
                response.raise_for_status()
            except TimeoutError as exc:
                _trace({"layer": "provider", "outcome": "fail" if attempt > self.retry_count else "retry",
                        "attempt": attempt, "duration_ms": round((monotonic() - started) * 1000),
                        "category": "provider_total_timeout"})
                if attempt > self.retry_count:
                    raise LLMTimeout("Qwen request timed out", attempt,
                                     category="provider_total_timeout") from exc
                continue
            except httpx.TimeoutException as exc:
                category = ("provider_connect_timeout" if isinstance(exc, httpx.ConnectTimeout) else
                            "provider_read_timeout" if isinstance(exc, httpx.ReadTimeout) else
                            "provider_write_timeout" if isinstance(exc, httpx.WriteTimeout) else
                            "provider_pool_timeout" if isinstance(exc, httpx.PoolTimeout) else
                            "provider_timeout")
                _trace({"layer": "provider", "outcome": "fail" if attempt > self.retry_count else "retry",
                        "attempt": attempt, "duration_ms": round((monotonic() - started) * 1000),
                        "category": category})
                if attempt > self.retry_count:
                    raise LLMTimeout("Qwen request timed out", attempt, category=category) from exc
                continue
            except httpx.HTTPStatusError as exc:
                will_retry = attempt <= self.retry_count and exc.response.status_code in (429, 500, 502, 503, 504)
                _trace({"layer": "provider", "outcome": "retry" if will_retry else "fail",
                        "attempt": attempt, "duration_ms": round((monotonic() - started) * 1000),
                        "http_status": exc.response.status_code,
                        "response_sha256": sha256(exc.response.content).hexdigest(),
                        "response_length": len(exc.response.content),
                        "category": f"provider_http_{exc.response.status_code}"})
                if attempt > self.retry_count or exc.response.status_code not in (429, 500, 502, 503, 504):
                    raise LLMProviderError("Qwen request failed", attempt,
                        category=f"provider_http_{exc.response.status_code}",
                        http_status=exc.response.status_code) from exc
                continue
            except httpx.RequestError as exc:
                category = ("provider_connect_error" if isinstance(exc, httpx.ConnectError) else
                            "provider_protocol_error" if isinstance(exc, httpx.RemoteProtocolError) else
                            "provider_network_error")
                _trace({"layer": "provider", "outcome": "fail" if attempt > self.retry_count else "retry",
                        "attempt": attempt, "duration_ms": round((monotonic() - started) * 1000),
                        "category": category})
                if attempt > self.retry_count:
                    raise LLMProviderError("Qwen request failed", attempt, category=category) from exc
                continue
            _trace({"layer": "provider", "outcome": "pass", "attempt": attempt,
                    "duration_ms": round((monotonic() - started) * 1000), "http_status": response.status_code,
                    "response_sha256": sha256(response.content).hexdigest(),
                    "response_length": len(response.content)})
            decode_phase = "response_json"
            try:
                payload = response.json()
                if not isinstance(payload, dict) or payload.get("model") != self.model_name:
                    _trace({"layer": "decode", "outcome": "fail", "attempt": attempt,
                            "error_type": "ModelIdentityMismatch"})
                    raise LLMProviderError("Qwen returned a different or missing model identifier", attempt,
                                           category="provider_model_mismatch")
                decode_phase = "envelope"
                choice = payload["choices"][0]
                if choice.get("finish_reason") != "stop":
                    _trace({"layer": "decode", "outcome": "fail", "attempt": attempt,
                            "error_type": "IncompleteResponse"})
                    raise LLMFormatError("Qwen structured output was not completed", attempt)
                content = choice["message"]["content"]
                if not isinstance(content, str):
                    _trace({"layer": "decode", "outcome": "fail", "attempt": attempt,
                            "error_type": "InvalidContentType"})
                    raise LLMFormatError("Qwen returned invalid structured content", attempt)
                decode_phase = "output_json"
                decoded = json.loads(content)
                _trace({"layer": "decode", "outcome": "pass", "attempt": attempt})
            except AttributeError:
                if _invocation_events.get():
                    _trace({"layer": "decode", "outcome": "fail", "attempt": attempt,
                            "decode_category": decode_phase})
                raise
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                _trace({"layer": "decode", "outcome": "fail", "attempt": attempt,
                        "error_type": type(exc).__name__,
                        **({"decode_category": decode_phase} if _invocation_events.get() else {})})
                raise LLMFormatError("Qwen returned invalid structured content", attempt) from exc
            try:
                output = output_schema.model_validate(decoded)
                _trace({"layer": "pydantic", "outcome": "pass", "attempt": attempt})
                return LLMResult(output=output, model_name=self.model_name, attempt_count=attempt)
            except ValidationError as exc:
                path, error_type = _validation_location(exc)
                _trace({"layer": "pydantic", "outcome": "fail", "attempt": attempt,
                        "error_path": path, "error_type": error_type})
                raise LLMFormatError("Qwen returned invalid structured content", attempt) from exc
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                _trace({"layer": "pydantic", "outcome": "fail", "attempt": attempt,
                        "error_type": type(exc).__name__})
                raise LLMFormatError("Qwen returned invalid structured content", attempt) from exc
        raise AssertionError("Unreachable provider retry state")
