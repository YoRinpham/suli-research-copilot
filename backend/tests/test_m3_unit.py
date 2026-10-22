import asyncio
import pytest
import httpx
from pydantic import ValidationError

from app.prompts.scientific import CLAIM_PROMPT, LANDSCAPE_PROMPT, canonical_digest
from app.services.scientific_schemas import ClaimBatch, LandscapeOutput, ClaimVerification, ConflictVerification, SummaryVerification
from app.providers.llm import DashScopeLLMProvider, LLMFormatError, LLMProviderError, LLMTimeout
from app.providers.interfaces import LLMResult
from app.config.settings import get_settings
from app.services.scientific import ScientificService


def test_prompt_hash_and_input_digest_are_stable():
    assert CLAIM_PROMPT.digest == CLAIM_PROMPT.digest
    assert CLAIM_PROMPT.digest != LANDSCAPE_PROMPT.digest
    assert canonical_digest({"b": 2, "a": 1}) == canonical_digest({"a": 1, "b": 2})
    assert canonical_digest({"a": 1}) != canonical_digest({"a": 2})
    assert "untrusted scientific source data" in CLAIM_PROMPT.text
    assert "untrusted scientific source data" in LANDSCAPE_PROMPT.text
    assert LANDSCAPE_PROMPT.version == "m3-v10"
    assert "DO NOT create a conflict-category item" in LANDSCAPE_PROMPT.text


def test_claim_schema_rejects_extra_ids_and_invalid_grounding():
    valid = {"claims": [{"text": "Method A was used.", "claim_type": "method", "grounding_status": "supported",
                         "confidence": "medium", "uncertainty": "none_identified", "evidence_for_ids": ["e1"],
                         "evidence_against_ids": []}]}
    assert ClaimBatch.model_validate(valid).claims[0].text == "Method A was used."
    with pytest.raises(ValidationError):
        ClaimBatch.model_validate({"claims": [{**valid["claims"][0], "id": "fabricated"}]})
    with pytest.raises(ValidationError):
        ClaimBatch.model_validate({"claims": [{**valid["claims"][0], "grounding_status": "certain"}]})


def test_landscape_schema_requires_explicit_items():
    value = {"summary": "Two methods are used.", "uncertainty": "Small corpus", "items": [
        {"category": "method", "text": "Method A", "uncertainty": "none_identified", "claim_ids": ["c1"], "evidence_ids": ["e1"]}]}
    assert LandscapeOutput.model_validate(value).items[0].category == "method"
    with pytest.raises(ValidationError):
        LandscapeOutput.model_validate({**value, "items": [{**value["items"][0], "category": "gap"}]})


def test_qwen_adapter_uses_strict_schema_and_rejects_model_substitution():
    requests = []
    output = {"claims": [{"text": "Method A was used.", "claim_type": "method", "grounding_status": "supported",
                           "confidence": "medium", "uncertainty": "none_identified", "evidence_for_ids": ["e1"],
                           "evidence_against_ids": []}]}

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"model": "qwen3.8-max", "choices": [{"finish_reason": "stop", "message": {"content": __import__("json").dumps(output), "reasoning_content": "private thinking trace"}}]})

    provider = DashScopeLLMProvider("test-key", "qwen3.8-max", 5, 0, client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    result = provider.generate_structured("Return JSON", ClaimBatch, {"evidence": [{"id": "e1", "text": "source"}]})
    assert result.output.claims[0].evidence_for_ids == ["e1"]
    assert result.model_name == "qwen3.8-max"
    assert "private thinking trace" not in result.output.model_dump_json()
    body = __import__("json").loads(requests[0].content)
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert "reasoning_effort" not in body
    assert "source" in body["messages"][1]["content"]

    def wrong_model(request):
        return httpx.Response(200, json={"model": "other", "choices": [{"finish_reason": "stop", "message": {"content": __import__("json").dumps(output)}}]})
    provider.client = httpx.AsyncClient(transport=httpx.MockTransport(wrong_model))
    with pytest.raises(LLMProviderError):
        provider.generate_structured("Return JSON", ClaimBatch, {})

    def truncated(request):
        return httpx.Response(200, json={"model": "qwen3.8-max", "choices": [{"finish_reason": "length", "message": {"content": __import__("json").dumps(output)}}]})
    provider.client = httpx.AsyncClient(transport=httpx.MockTransport(truncated))
    with pytest.raises(LLMFormatError):
        provider.generate_structured("Return JSON", ClaimBatch, {})


@pytest.mark.parametrize("effort", ["medium", "low"])
def test_qwen_adapter_sends_configured_reasoning_effort(effort):
    requests = []

    def respond(request):
        requests.append(__import__("json").loads(request.content))
        output = {"claims": [{"text": "Method A was used.", "claim_type": "method",
            "grounding_status": "supported", "confidence": "medium", "uncertainty": "none_identified",
            "evidence_for_ids": ["e1"], "evidence_against_ids": []}]}
        return httpx.Response(200, json={"model": "qwen3.8-max", "choices": [{"finish_reason": "stop",
            "message": {"content": __import__("json").dumps(output)}}]})

    provider = DashScopeLLMProvider("test-key", "qwen3.8-max", 60, 2,
        reasoning_effort=effort, client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    result = provider.generate_structured("Return JSON", ClaimBatch, {"evidence": [{"id": "e1"}]})
    assert result.output.claims[0].evidence_for_ids == ["e1"]
    assert len(requests) == 1
    assert requests[0]["reasoning_effort"] == effort
    assert requests[0]["response_format"]["json_schema"]["strict"] is True


def test_qwen_adapter_retains_absolute_attempt_deadline():
    async def slow_response(request):
        await asyncio.sleep(0.05)
        return httpx.Response(200)

    provider = DashScopeLLMProvider("test-key", "qwen3.8-max", 0.01, 0,
        reasoning_effort="medium", client=httpx.AsyncClient(transport=httpx.MockTransport(slow_response)))
    with pytest.raises(LLMTimeout) as error:
        provider.generate_structured("Return JSON", ClaimBatch, {})
    assert error.value.category == "provider_total_timeout"
    assert error.value.attempt_count == 1


def test_qwen_adapter_retains_configured_retry_count():
    requests = []

    def unavailable(request):
        requests.append(request)
        return httpx.Response(503)

    provider = DashScopeLLMProvider("test-key", "qwen3.8-max", 60, 2,
        reasoning_effort="medium", client=httpx.AsyncClient(transport=httpx.MockTransport(unavailable)))
    with pytest.raises(LLMProviderError) as error:
        provider.generate_structured("Return JSON", ClaimBatch, {})
    assert error.value.attempt_count == 3
    assert error.value.http_status == 503
    assert len(requests) == 3


def test_conflict_needs_independent_source_relationships():
    item = {"category": "conflict", "text": "Results differ under the same conditions.",
            "uncertainty": "Replication details unknown", "claim_ids": ["c1", "c2"], "evidence_ids": ["e1"]}
    output = LandscapeOutput.model_validate({"summary": "Results differ", "uncertainty": "Small corpus", "items": [item]})
    with pytest.raises(ValueError):
        ScientificService._validate_landscape_output(output, {"c1", "c2"}, {"e1", "e2"}, {"c1": {"e1"}, "c2": {"e2"}})


def test_repaired_source_validation_counts_each_provider_attempt_once():
    class Provider:
        def __init__(self):
            self.calls = 0

        def generate_structured(self, prompt, output_schema, context):
            self.calls += 1
            evidence_id = "invented" if self.calls == 1 else "e1"
            output = {"claims": [{"text": "Method A was used.", "claim_type": "method",
                                  "grounding_status": "supported", "confidence": "medium",
                                  "uncertainty": "none_identified", "evidence_for_ids": [evidence_id],
                                  "evidence_against_ids": []}]}
            return LLMResult(output=output_schema.model_validate(output), model_name="qwen3.8-max", attempt_count=1)

    provider = Provider()
    service = ScientificService(get_settings().model_copy(update={"qwen_model": "qwen3.8-max"}), provider)
    output, attempts = service._generate("unused", CLAIM_PROMPT, ClaimBatch, {},
                                         lambda batch: service._validate_claim_output(batch, {"e1"}))
    assert provider.calls == 2
    assert attempts == 2
    assert output.claims[0].evidence_for_ids == ["e1"]


def test_claim_verifier_schema_owns_judgment_and_references_only():
    verification = ClaimVerification.model_validate({
        "verdict": "directly_supported", "evidence_refs": ["e1"],
        "risk_flags": [], "justification": "The source states an association."
    })
    ScientificService._validate_verifier_sources(verification, {"e1": "X was associated with Y."})
    assert "support_excerpts" not in ClaimVerification.model_json_schema()["properties"]
    with pytest.raises(ValidationError):
        ClaimVerification.model_validate({**verification.model_dump(), "support_excerpts": [
            {"evidence_id": "e1", "excerpt": "invented quote"}]})
    with pytest.raises(ValidationError):
        ClaimVerification.model_validate({**verification.model_dump(), "evidence_refs": []})
    with pytest.raises(ValueError):
        ScientificService._validate_verifier_sources(verification, {"e2": "X was associated with Y."})
    with pytest.raises(ValidationError):
        ConflictVerification.model_validate({"verdict": "direct_conflict", "dimensions": {}})
    with pytest.raises(ValidationError):
        SummaryVerification.model_validate({"verdict": "unsupported_major_statement", "flagged_statements": [],
                                            "evidence_refs": [], "support_excerpts": [], "justification": "No source"})


@pytest.mark.parametrize("risk", ["methodological_limitation", "statistical_evidence_limitation",
                                     "generalizability_limitation"])
def test_direct_support_accepts_evidence_limitations(risk):
    verification = ClaimVerification.model_validate({
        "verdict": "directly_supported", "evidence_refs": ["e1"],
        "risk_flags": [risk], "justification": "The bounded statement is directly reported."
    })
    ScientificService._validate_claim_risk_compatibility(verification)


@pytest.mark.parametrize("risk", ["causal_amplification", "statistical_significance_amplification",
                                     "unsupported_comparison", "unsupported_generalization",
                                     "condition_population_loss", "metric_mismatch", "other_qualification_loss"])
def test_direct_support_rejects_claim_support_defects(risk):
    verification = ClaimVerification.model_validate({
        "verdict": "directly_supported", "evidence_refs": ["e1"],
        "risk_flags": [risk], "justification": "The statement exceeds the source."
    })
    with pytest.raises(ValueError, match="Direct support cannot retain material scientific risk flags"):
        ScientificService._validate_claim_risk_compatibility(verification)


@pytest.mark.parametrize("verdict", ["inferentially_supported", "unsupported", "contradicted",
                                           "insufficient_evidence"])
def test_nondirect_verdicts_can_report_support_defects_and_limitations(verdict):
    verification = ClaimVerification.model_validate({
        "verdict": verdict, "evidence_refs": ["e1"],
        "risk_flags": ["causal_amplification", "methodological_limitation"],
        "justification": "Direct support is not asserted."
    })
    ScientificService._validate_claim_risk_compatibility(verification)


def test_qwen_claim_verifier_request_schema_matches_pydantic_contract():
    requests = []
    response = {"verdict": "directly_supported", "evidence_refs": ["e1"],
                "risk_flags": [], "justification": "Direct support"}

    def respond(request):
        requests.append(__import__("json").loads(request.content))
        return httpx.Response(200, json={"model": "qwen3.8-max", "choices": [{
            "finish_reason": "stop", "message": {"content": __import__("json").dumps(response)}}]})

    provider = DashScopeLLMProvider("test-key", "qwen3.8-max", 5, 0,
                                    client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    result = provider.generate_structured("Verify Claim", ClaimVerification, {"evidence": [{"id": "e1"}]})
    assert result.output.model_dump() == response
    schema = requests[0]["response_format"]["json_schema"]["schema"]
    assert schema == ClaimVerification.model_json_schema()
    assert set(schema["properties"]) == {"verdict", "evidence_refs", "risk_flags", "justification"}


def test_prompt_bundle_hash_covers_each_static_instruction():
    from app.prompts.scientific import Prompt
    assert CLAIM_PROMPT.digest == canonical_digest({"name": CLAIM_PROMPT.name, "version": CLAIM_PROMPT.version,
                                                     "bundle": CLAIM_PROMPT.bundle})
    modified = Prompt(CLAIM_PROMPT.name, CLAIM_PROMPT.version, CLAIM_PROMPT.text,
                      {**CLAIM_PROMPT.bundle, "semantic_verification": "changed"})
    assert modified.digest != CLAIM_PROMPT.digest


def test_evaluation_scaffold_records_metadata_and_keeps_human_labels_blank():
    from evaluation.m3_quality import evaluation_record
    record = evaluation_record({"id": "synthetic", "evidence": [{"id": "e1"}], "expected": "narrow claim"},
                               "claim", "qwen3.8-max")
    assert record["scenario_id"] == "synthetic"
    assert record["prompt_bundle_hashes"]["claim"] == CLAIM_PROMPT.digest
    assert record["provider_attempt_count"] == 0
    assert record["generator_proposal"] == {"claims": None, "landscape": None}
    assert record["ai_verifier_assessment"]["claim_grounding"] is None
    assert record["human_scientific_judgment"]["claim_grounding"] is None
    assert record["final_downstream_eligibility"] == {"claims": None, "landscape": None}
    assert record["generated_claim_count"] == 0
    assert record["generation_run_id"] is None
    assert record["human_evaluation"]["label_source"] == "HUMAN evaluation"
    assert all(value is None for value in record["human_evaluation"]["claim_counts"].values())
    assert all(value is None for value in record["human_evaluation"]["conflict_counts"].values())
