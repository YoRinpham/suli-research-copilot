"""Landscape diagnostics separate provider, semantic, and commit stages without prose."""

import json

import httpx
import pytest

from app.config.settings import get_settings
from app.prompts.scientific import LANDSCAPE_PROMPT
from app.providers.interfaces import LLMResult
from app.providers.llm import DashScopeLLMProvider
from app.services.errors import ServiceError
from app.services.scientific import ScientificService
from app.services.scientific_schemas import ConflictVerification, LandscapeOutput, SummaryRepair, SummaryVerification


CLAIM_TEXT = "PRIVATE CLAIM PROSE"
EVIDENCE_TEXT = "PRIVATE EVIDENCE EXCERPT"
LANDSCAPE_TEXT = "PRIVATE LANDSCAPE PROSE"
VERIFIER_TEXT = "PRIVATE VERIFIER PROSE"


def landscape(claim_id="c1", category="finding", text=LANDSCAPE_TEXT):
    return {"summary": LANDSCAPE_TEXT, "uncertainty": "Limited", "items": [
        {"category": category, "text": text, "uncertainty": "Limited",
         "claim_ids": [claim_id] if category != "conflict" else ["c1", "c2"],
         "evidence_ids": ["e1"] if category != "conflict" else ["e1", "e2"]}]}


def conflict(verdict="not_conflict", comparable=True):
    return {"verdict": verdict, "asserts_direct_conflict": verdict == "direct_conflict",
            "dimensions": {key: "comparable" if comparable else "unknown" for key in (
                "dataset", "population", "task", "metric", "experimental_condition", "baseline")},
            "evidence_refs": ["e1", "e2"] if verdict == "direct_conflict" else ["e1"], "support_verdict": "supported",
            "flagged_statements": [], "qualifications": [], "justification": VERIFIER_TEXT}


def summary(verdict="supported"):
    return {"verdict": verdict,
            "flagged_statements": [VERIFIER_TEXT] if verdict != "supported" else [],
            "evidence_refs": ["e1"], "qualifications": [], "justification": VERIFIER_TEXT}


class Provider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def generate_structured(self, prompt, output_schema, context):
        self.calls.append(output_schema)
        value = self.responses.pop(0)
        return LLMResult(output=output_schema.model_validate(value),
                         model_name="qwen3.8-max", attempt_count=1)


def service(provider, events):
    result = ScientificService(get_settings().model_copy(update={"qwen_model": "qwen3.8-max"}),
                               provider, trace_sink=events.append)
    result._failed = lambda *args: None
    return result


def context():
    return {"research_question": "PRIVATE PROMPT CONTEXT", "claims": [
        {"id": "c1", "text": CLAIM_TEXT, "claim_type": "finding", "evidence_for_ids": ["e1"],
         "evidence_against_ids": []},
        {"id": "c2", "text": CLAIM_TEXT, "claim_type": "finding", "evidence_for_ids": ["e2"],
         "evidence_against_ids": []}],
        "evidence": [{"id": "e1", "source_text": EVIDENCE_TEXT},
                     {"id": "e2", "source_text": EVIDENCE_TEXT}]}


def assert_sanitized(events):
    serialized = json.dumps(events)
    for private in (CLAIM_TEXT, EVIDENCE_TEXT, LANDSCAPE_TEXT, VERIFIER_TEXT,
                    "PRIVATE PROMPT CONTEXT", "PRIVATE API SECRET"):
        assert private not in serialized


def test_initial_landscape_trace_identifies_claim_whitelist_failure_and_structured_repair():
    events = []
    provider = Provider([landscape("foreign"), landscape("c1")])
    subject = service(provider, events)
    output, attempts = subject._generate("run", LANDSCAPE_PROMPT, LandscapeOutput, context(),
        lambda value: subject._validate_landscape_output(value, {"c1", "c2"}, {"e1", "e2"},
                                                         {"c1": {"e1"}, "c2": {"e2"}}),
        trace_stage="landscape_generation", trace_validator="_validate_landscape_output")
    assert attempts == 2 and output.items[0].claim_ids == ["c1"]
    assert any(e["layer"] == "claim_whitelist" and e["outcome"] == "fail" and
               e["validation_code"] == "source_not_allowed" for e in events)
    assert any(e["layer"] == "repair" and e["repair_round"] == 1 for e in events)
    assert any(e["provider_role"] == "structured_repair" for e in events if e["layer"] == "provider_round")
    assert_sanitized(events)


def test_landscape_trace_separates_transport_retry_from_structured_repair():
    events = []
    calls = 0

    def respond(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, text="PRIVATE PROVIDER BODY")
        generated = landscape("foreign" if calls == 2 else "c1")
        return httpx.Response(200, json={"model": "qwen3.8-max", "choices": [{"finish_reason": "stop",
            "message": {"content": json.dumps(generated)}}]})

    provider = DashScopeLLMProvider("PRIVATE API SECRET", "qwen3.8-max", 60, 2,
        client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    subject = service(provider, events)
    _, attempts = subject._generate("run", LANDSCAPE_PROMPT, LandscapeOutput, context(),
        lambda value: subject._validate_landscape_output(value, {"c1", "c2"}, {"e1", "e2"},
                                                         {"c1": {"e1"}, "c2": {"e2"}}),
        trace_stage="landscape_generation", trace_validator="_validate_landscape_output")
    assert attempts == 3
    provider_events = [e for e in events if e["layer"] == "provider"]
    assert [(e["provider_role"], e["repair_round"], e["attempt"], e["outcome"])
            for e in provider_events] == [
        ("initial_structured_generation", 0, 1, "retry"),
        ("initial_structured_generation", 0, 2, "pass"),
        ("structured_repair", 1, 1, "pass")]
    assert all(e["duration_ms"] >= 0 for e in provider_events)
    assert provider_events[0]["http_status"] == 503
    assert provider_events[-1]["http_status"] == 200
    assert_sanitized(events)


@pytest.mark.parametrize("first,expected_code", [
    (conflict("direct_conflict", False), "comparability_not_established"),
    (conflict("not_conflict", True), "conflict_not_supported"),
])
def test_conflict_trace_identifies_semantic_failure_and_repair(first, expected_code):
    events = []
    provider = Provider([first, landscape(text="Revised finding"), conflict(), summary()])
    subject = service(provider, events)
    original = LandscapeOutput.model_validate(landscape(category="conflict", text="Direct conflict between reports"))
    result = subject._verify_landscape("run", original, context(), {"c1", "c2"}, {"e1", "e2"},
                                       {"c1": {"e1"}, "c2": {"e2"}}, 1)
    assert result[-1] == 5
    assert any(e["stage"] == "conflict_verification[0]" and e["layer"] == "comparability_validation" and
               e["outcome"] == "fail" and e["validation_code"] == expected_code for e in events)
    assert any(e["stage"] == "conflict_verification[0].repair" and e["provider_role"] == "conflict_repair"
               for e in events if e["layer"] == "provider_round")
    assert_sanitized(events)


def test_summary_trace_identifies_unsupported_assertion_and_repair():
    events = []
    provider = Provider([conflict(), summary("unsupported_major_statement"),
                         {"summary": "Revised summary", "uncertainty": "Limited"}, summary()])
    subject = service(provider, events)
    output = LandscapeOutput.model_validate(landscape())
    result = subject._verify_landscape("run", output, context(), {"c1", "c2"}, {"e1", "e2"},
                                       {"c1": {"e1"}, "c2": {"e2"}}, 1)
    assert result[-1] == 5
    assert any(e["stage"] == "summary_verification" and e["layer"] == "domain_validation" and
               e.get("validation_code") == "summary_unsupported_assertion" for e in events)
    assert any(e["stage"] == "summary_verification.repair" and e["provider_role"] == "summary_repair"
               for e in events if e["layer"] == "provider_round")
    assert_sanitized(events)


def test_terminal_unsupported_conflict_has_index_and_no_prose():
    events = []
    provider = Provider([conflict("not_conflict"), landscape(category="conflict", text="Different wording"),
                         conflict("not_conflict")])
    subject = service(provider, events)
    output = LandscapeOutput.model_validate(landscape(category="conflict", text="Direct conflict between reports"))
    with pytest.raises(ServiceError) as error:
        subject._verify_landscape("run", output, context(), {"c1", "c2"}, {"e1", "e2"},
                                  {"c1": {"e1"}, "c2": {"e2"}}, 1)
    assert error.value.code == "scientific_verification_failed"
    assert any(e["stage"] == "conflict_verification[0]" and e["conflict_index"] == 0 and
               e.get("validation_code") == "conflict_not_supported" for e in events)
    assert_sanitized(events)


def test_conflict_repair_retaining_rejected_text_has_sanitized_failure():
    events = []
    provider = Provider([conflict("not_conflict"), landscape(category="conflict",
                         text="Direct conflict between reports")])
    subject = service(provider, events)
    output = LandscapeOutput.model_validate(landscape(category="conflict", text="Direct conflict between reports"))
    with pytest.raises(ServiceError) as error:
        subject._verify_landscape("run", output, context(), {"c1", "c2"}, {"e1", "e2"},
                                  {"c1": {"e1"}, "c2": {"e2"}}, 1)
    assert error.value.code == "scientific_verification_failed"
    assert any(e["stage"] == "conflict_verification[0].repair" and
               e.get("validation_code") == "rejected_conflict_retained" for e in events)
    assert_sanitized(events)
