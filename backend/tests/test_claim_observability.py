"""Claim trace events identify failed layers without retaining scientific content."""

import json

import httpx
import pytest

from app.config.settings import get_settings
from app.prompts.scientific import CLAIM_PROMPT
from app.providers.interfaces import LLMResult
from app.providers.llm import DashScopeLLMProvider, trace_provider_calls
from app.services.scientific import ScientificService
from app.services.errors import ServiceError
from app.services.scientific_schemas import ClaimBatch, ClaimVerification


def proposal(evidence_id="e1"):
    return {"text": "PRIVATE GENERATED CLAIM", "claim_type": "finding",
            "grounding_status": "supported", "confidence": "medium", "uncertainty": "Limited",
            "evidence_for_ids": [evidence_id], "evidence_against_ids": []}


def test_provider_trace_separates_transport_retry_decode_and_pydantic():
    events = []
    requests = []

    def respond(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(503, text="PRIVATE PROVIDER BODY")
        return httpx.Response(200, json={"model": "qwen3.8-max", "choices": [{"finish_reason": "stop",
            "message": {"content": json.dumps({"claims": [proposal()]})}}]})

    provider = DashScopeLLMProvider("PRIVATE API SECRET", "qwen3.8-max", 60, 2,
        client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    with trace_provider_calls(events.append):
        result = provider.generate_structured("PRIVATE PROMPT", ClaimBatch,
                                              {"evidence": "PRIVATE EVIDENCE"})
    assert result.attempt_count == 2
    assert len([event for event in events if event["layer"] == "transport"]) == 2
    transport_events = events
    events = [event for event in events if event["layer"] != "transport"]
    assert [(event["layer"], event.get("attempt"), event["outcome"]) for event in events] == [
        ("provider", 1, "retry"), ("provider", 2, "pass"),
        ("decode", 2, "pass"), ("pydantic", 2, "pass")]
    assert events[0]["http_status"] == 503
    assert events[1]["http_status"] == 200
    assert events[1]["response_sha256"] and events[1]["response_length"] > 0
    serialized = json.dumps(transport_events)
    for secret in ("PRIVATE API SECRET", "PRIVATE PROMPT", "PRIVATE EVIDENCE",
                   "PRIVATE GENERATED CLAIM", "PRIVATE PROVIDER BODY"):
        assert secret not in serialized


def test_provider_trace_identifies_json_decode_failure():
    events = []

    def respond(request):
        return httpx.Response(200, json={"model": "qwen3.8-max", "choices": [{"finish_reason": "stop",
            "message": {"content": "PRIVATE INVALID JSON"}}]})

    provider = DashScopeLLMProvider("key", "qwen3.8-max", 60, 0,
        client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    with trace_provider_calls(events.append):
        try:
            provider.generate_structured("prompt", ClaimBatch, {})
        except Exception:
            pass
    assert [(event["layer"], event["outcome"]) for event in events] == [
        ("transport", "pass"), ("provider", "pass"), ("decode", "fail")]
    assert events[-1]["error_type"] == "JSONDecodeError"
    assert "PRIVATE INVALID JSON" not in json.dumps(events)


def test_provider_trace_identifies_pydantic_path_without_response_prose():
    events = []

    def respond(request):
        invalid = {"claims": [{**proposal(), "claim_type": "PRIVATE INVALID TYPE"}]}
        return httpx.Response(200, json={"model": "qwen3.8-max", "choices": [{"finish_reason": "stop",
            "message": {"content": json.dumps(invalid)}}]})

    provider = DashScopeLLMProvider("key", "qwen3.8-max", 60, 0,
        client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    with trace_provider_calls(events.append), pytest.raises(Exception):
        provider.generate_structured("prompt", ClaimBatch, {})
    assert [(event["layer"], event["outcome"]) for event in events] == [
        ("transport", "pass"), ("provider", "pass"), ("decode", "pass"), ("pydantic", "fail")]
    assert events[-1]["error_path"] == "claims.0.claim_type"
    assert events[-1]["error_type"] == "literal_error"
    assert "PRIVATE INVALID TYPE" not in json.dumps(events)


def test_claim_batch_trace_names_domain_failure_and_repair_round():
    class Provider:
        def __init__(self):
            self.calls = 0

        def generate_structured(self, prompt, output_schema, context):
            self.calls += 1
            value = {"claims": [proposal("foreign" if self.calls == 1 else "e1")]}
            return LLMResult(output=output_schema.model_validate(value),
                             model_name="qwen3.8-max", attempt_count=1)

    events = []
    service = ScientificService(get_settings().model_copy(update={"qwen_model": "qwen3.8-max"}),
                                Provider(), trace_sink=events.append)
    output, attempts = service._generate("run-1", CLAIM_PROMPT, ClaimBatch, {},
        lambda batch: service._validate_claim_output(batch, {"e1"}), trace_stage="claim_batch",
        trace_validator="_validate_claim_output")
    assert attempts == 2 and output.claims[0].evidence_for_ids == ["e1"]
    failures = [event for event in events if event["layer"] == "domain_validation" and event["outcome"] == "fail"]
    assert len(failures) == 1
    assert failures[0]["stage"] == "claim_batch" and failures[0]["repair_round"] == 0
    assert failures[0]["validator"] == "_validate_claim_output"
    assert any(event["layer"] == "repair" and event["repair_round"] == 1 for event in events)
    assert any(event["layer"] == "domain_validation" and event["outcome"] == "pass" and
               event["repair_round"] == 1 for event in events)
    assert "PRIVATE GENERATED CLAIM" not in json.dumps(events)


def test_claim_verification_trace_identifies_candidate_index():
    class Provider:
        def generate_structured(self, prompt, output_schema, context):
            if context["claim_text"] == "candidate zero":
                evidence_id = "e1"
            else:
                evidence_id = "foreign"
            value = {"verdict": "unsupported", "evidence_refs": [evidence_id],
                     "risk_flags": [], "justification": "PRIVATE VERIFIER PROSE"}
            return LLMResult(output=ClaimVerification.model_validate(value),
                             model_name="qwen3.8-max", attempt_count=1)

    events = []
    service = ScientificService(get_settings().model_copy(update={"qwen_model": "qwen3.8-max"}),
                                Provider(), trace_sink=events.append)
    service._failed = lambda *args: None
    batch = ClaimBatch.model_validate({"claims": [
        {**proposal(), "text": "candidate zero"}, {**proposal(), "text": "candidate one"}]})
    sources = [{"id": "e1", "source_text": "PRIVATE EVIDENCE"}]
    with pytest.raises(ServiceError) as error:
        service._verify_claim_batch("run-2", batch, sources, 1)
    assert error.value.code == "structured_output_invalid"
    failed = [event for event in events if event["layer"] == "domain_validation" and event["outcome"] == "fail"]
    assert failed and all(event["stage"] == "claim_verification[1]" for event in failed)
    assert all(event["candidate_index"] == 1 and event["provider_attempt"] == 1 for event in failed)
    assert failed[-1]["repair_round"] == 1
    assert "PRIVATE VERIFIER PROSE" not in json.dumps(events)
    assert "PRIVATE EVIDENCE" not in json.dumps(events)
