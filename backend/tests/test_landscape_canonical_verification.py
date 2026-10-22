"""Reference-only Landscape verification: deterministic scientific gates, no database."""

import copy
import hashlib
import json

import pytest
import httpx
from pydantic import ValidationError

from app.config.settings import get_settings
from app.prompts.scientific import LANDSCAPE_PROMPT
from app.providers.interfaces import LLMResult
from app.providers.llm import DashScopeLLMProvider
from app.services.errors import ServiceError
from app.services.scientific import LANDSCAPE_SCHEMA_VERSION, ScientificService
from app.services import scientific_schemas as schemas


SOURCE = "  合成研究：A→B，μ = 0.3\nNo empirical result; unresolved.\r\nPhysical conformity is required.  "


def item_verification(**changes):
    value = {"verdict": "not_conflict", "asserts_direct_conflict": False,
             "dimensions": {key: "unknown" for key in (
                 "dataset", "population", "task", "metric", "experimental_condition", "baseline")},
             "support_verdict": "supported", "flagged_statements": [], "qualifications": [],
             "evidence_refs": ["e1"], "justification": "Only a nominal synthetic schedule is stated."}
    return {**value, **changes}


def summary_verification(**changes):
    return {"verdict": "supported", "flagged_statements": [], "qualifications": [],
            "evidence_refs": ["e1"], "justification": "Corpus-bounded summary.", **changes}


def landscape(text="A nominal synthetic schedule is described.", category="method"):
    return schemas.LandscapeOutput.model_validate({
        "summary": "Within these sources, the empirical question remains unresolved.",
        "uncertainty": "No empirical results are reported.", "items": [
            {"text": text, "category": category, "uncertainty": "Physical conformity is required.",
             "claim_ids": ["c1"], "evidence_ids": ["e1"]}]})


def context():
    return {"research_question": "Compare schedules", "claims": [
        {"id": "c1", "text": "A nominal synthetic schedule.", "claim_type": "method",
         "evidence_for_ids": ["e1"], "evidence_against_ids": ["challenge"]},
        {"id": "c2", "text": "Independent source", "claim_type": "finding",
         "evidence_for_ids": ["e2"], "evidence_against_ids": []}],
        "evidence": [{"id": eid, "source_text": SOURCE if eid == "e1" else "Other canonical source.",
                      "paper_chunk_id": "chunk-" + eid, "paper_id": "paper-" + eid,
                      "paper_title": "Synthetic study", "page_number": 3}
                     for eid in ("e1", "e2", "challenge")]}


class Provider:
    def __init__(self, responses):
        self.responses = copy.deepcopy(responses)
        self.calls = []

    def generate_structured(self, prompt, output_schema, source_context):
        self.calls.append((prompt, output_schema.__name__, copy.deepcopy(source_context)))
        return LLMResult(self.responses.pop(0), "qwen3.8-max", 1)


def service(responses):
    provider = Provider(responses)
    failed, events = [], []
    subject = ScientificService(get_settings().model_copy(update={"qwen_model": "qwen3.8-max"}),
                                provider, trace_sink=events.append)
    subject._failed = lambda *args: failed.append(args)
    return subject, provider, failed, events


def verify(subject, output=None, source_context=None):
    return subject._verify_landscape("run", output or landscape(), source_context or context(),
                                     {"c1", "c2"}, {"e1", "e2", "challenge"},
                                     {"c1": {"e1", "challenge"}, "c2": {"e2"}}, 1)


def test_reference_protocol_binds_whole_unicode_source_and_provenance():
    subject, provider, failed, events = service([item_verification(), summary_verification()])
    output, items, summaries, attempts = verify(subject)
    assert attempts == 3 and not failed
    assert provider.calls[0][1] == "LandscapeItemVerification"
    assert provider.calls[1][1] == "LandscapeSummaryVerification"
    assert items[0]["source_binding_origin"] == "server_bound_canonical_source_text"
    assert items[0]["source_binding_schema_version"] == LANDSCAPE_SCHEMA_VERSION
    for assessment in (items[0], summaries[0]):
        quote = assessment["support_excerpts"][0]
        assert quote["excerpt"] == SOURCE
        assert quote["source_hash"] == hashlib.sha256(SOURCE.encode("utf-8")).hexdigest()
        assert (quote["paper_chunk_id"], quote["paper_id"], quote["page_number"]) == ("chunk-e1", "paper-e1", 3)
    from app.services.landscape_budget import decode_assessment
    assert decode_assessment(provider.calls[1][2]["item_assessments"][0])["support_verdict"] == items[0]["support_verdict"]
    assert SOURCE not in json.dumps(events, ensure_ascii=False)
    assert LANDSCAPE_PROMPT.version == "m3-v10" and LANDSCAPE_SCHEMA_VERSION == "m3-landscape-v9"


@pytest.mark.parametrize("name,factory", [("LandscapeItemVerification", item_verification),
                                          ("LandscapeSummaryVerification", summary_verification)])
def test_reference_schema_has_no_quotes_and_positive_support_requires_clean_refs(name, factory):
    schema = getattr(schemas, name, None)
    assert schema is not None, "New reference-only schema is required"
    assert "support_excerpts" not in schema.model_json_schema()["properties"]
    for changes in ({"evidence_refs": []}, {"evidence_refs": ["e1", "e1"]},
                    {"flagged_statements": ["Missing material condition"]},
                    {"support_excerpts": [{"evidence_id": "e1", "excerpt": SOURCE}]}):
        with pytest.raises(ValidationError):
            schema.model_validate(factory(**changes))


@pytest.mark.parametrize("refs", [["e1", "e1"], ["foreign"], ["e2"], ["challenge"]])
def test_item_bad_or_wrong_role_refs_exhaust_only_structured_repair(refs):
    subject, provider, failed, _ = service([item_verification(evidence_refs=refs)] * 2)
    with pytest.raises(ServiceError) as caught:
        verify(subject)
    assert caught.value.code == "structured_output_invalid"
    assert len(provider.calls) == 2 and failed[-1][-1] == 3


@pytest.mark.parametrize("quote_field", ["support_excerpts", "excerpt", "source_text", "quote"])
def test_unknown_quote_fields_are_never_silently_rebound(quote_field):
    response = item_verification(**{quote_field: SOURCE})
    subject, provider, failed, _ = service([response] * 2)
    with pytest.raises(ServiceError) as caught:
        verify(subject)
    assert caught.value.code == "structured_output_invalid" and len(provider.calls) == 2


@pytest.mark.parametrize("assertion", [
    "A causes an empirical improvement over B.", "A is superior to B.",
    "A significantly improves reliability.", "This is globally novel.",
    "The schedule establishes a causal mechanism.",
    "C35 guarantees conformity without physical conformity conditions.",
    "C03 conclusively resolves the empirical question.",
    "The synthetic schedules directly conflict in measured empirical outcomes.",
])
@pytest.mark.parametrize("verdict", ["unsupported", "insufficient_evidence"])
def test_authentic_ids_do_not_override_independent_negative_support(assertion, verdict):
    # The deterministic verifier supplies the critique; code must gate it, not classify prose itself.
    rejected = item_verification(support_verdict=verdict, flagged_statements=[assertion],
                                 qualifications=["Synthetic only; physical conformity required; unresolved."])
    repaired = landscape("A rephrased unsupported result.")
    subject, provider, failed, events = service([rejected, repaired.model_dump(), rejected])
    with pytest.raises(ServiceError) as caught:
        verify(subject, landscape(assertion))
    assert caught.value.code == "scientific_verification_failed"
    assert [call[1] for call in provider.calls] == ["LandscapeItemVerification", "LandscapeOutput", "LandscapeItemVerification"]
    assert failed[-1][-1] == 4
    assert assertion not in json.dumps(events)


def test_semantic_repair_can_restore_source_qualifications_and_unresolved_status():
    response = item_verification(support_verdict="unsupported", flagged_statements=["Lost qualification"],
                                 qualifications=["Physical conformity is required; empirical question unresolved."])
    repaired = landscape("Within this synthetic source, physical conformity is required; empirical results remain unresolved.")
    subject, provider, failed, _ = service([response, repaired.model_dump(),
                                           item_verification(qualifications=["Synthetic; no empirical results."]),
                                           summary_verification()])
    output, items, summaries, attempts = verify(subject, landscape("Physical conformity is guaranteed."))
    assert output.items[0].text == repaired.items[0].text
    assert attempts == 5 and not failed
    assert "Physical conformity" in provider.calls[1][2]["rejected_conflict"]["qualifications"][0]


def test_direct_conflict_refs_must_cover_each_claim_independently():
    output = landscape("The reported findings directly conflict.", "finding").model_dump()
    output["items"][0].update(category="conflict", claim_ids=["c1", "c2"], evidence_ids=["e1", "e2"])
    response = item_verification(verdict="direct_conflict", asserts_direct_conflict=True,
                                 dimensions={key: "comparable" for key in item_verification()["dimensions"]})
    subject, provider, failed, _ = service([response] * 2)
    with pytest.raises(ServiceError) as caught:
        verify(subject, schemas.LandscapeOutput.model_validate(output))
    assert caught.value.code == "structured_output_invalid" and len(provider.calls) == 2


def test_summary_negative_scientific_verdict_has_one_repair_then_fails():
    rejected = summary_verification(verdict="unsupported_major_statement",
                                     flagged_statements=["Unsupported superiority"], qualifications=["Synthetic only"])
    subject, provider, failed, _ = service([item_verification(), rejected,
                                           {"summary": "Reworded unsupported superiority.", "uncertainty": "Limited"}, rejected])
    with pytest.raises(ServiceError) as caught:
        verify(subject)
    assert caught.value.code == "scientific_verification_failed"
    assert len(provider.calls) == 4 and failed[-1][-1] == 5
    assert provider.calls[2][2]["qualifications"] == ["Synthetic only"]


def test_summary_scope_excludes_uncited_claims_and_challenge_only_support():
    subject, provider, failed, _ = service([item_verification(), summary_verification(evidence_refs=["e2"]),
                                           summary_verification(evidence_refs=["e2"])])
    with pytest.raises(ServiceError) as caught:
        verify(subject)
    assert caught.value.code == "structured_output_invalid"
    assert list(provider.calls[1][2]["claims"]) == ["c1"]


def test_legacy_bad_quote_still_fails_unchanged_exact_substring_validator():
    value = item_verification()
    for field in ("support_verdict", "flagged_statements", "qualifications"):
        value.pop(field)
    value["support_excerpts"] = [{"evidence_id": "e1", "excerpt": SOURCE.replace("μ", "u")}]
    with pytest.raises(ValueError, match="exact source substring"):
        ScientificService._validate_verifier_sources(schemas.ConflictVerification.model_validate(value), {"e1": SOURCE})


def test_unknown_quote_key_cannot_leak_private_text_through_trace_error_path():
    response = item_verification(**{SOURCE: "Unexpected field"})
    subject, _, _, events = service([response] * 2)
    with pytest.raises(ServiceError):
        verify(subject)
    assert "合成研究" not in json.dumps(events, ensure_ascii=False)


def test_actual_adapter_schema_error_cannot_leak_private_extra_field_name():
    def response(request):
        return httpx.Response(200, json={"model": "qwen3.8-max", "choices": [{"finish_reason": "stop",
            "message": {"content": json.dumps(item_verification(**{SOURCE: "Unexpected"}))}}]})

    subject, _, failed, events = service([])
    subject.provider = DashScopeLLMProvider("mock-key", "qwen3.8-max", 60, 2,
        client=httpx.AsyncClient(transport=httpx.MockTransport(response)))
    with pytest.raises(ServiceError) as caught:
        verify(subject)
    assert caught.value.code == "structured_output_invalid"
    assert failed[-1][-1] == 3
    provider_errors = [event for event in events if event.get("error_type") == "extra_forbidden"]
    assert provider_errors and all(event["error_path"] == "<extra_field>" for event in provider_errors)
    assert "合成研究" not in json.dumps(events, ensure_ascii=False)


def test_summary_context_passes_judgments_without_duplicating_canonical_passages():
    subject, provider, _, _ = service([item_verification(), summary_verification()])
    verify(subject)
    assessments = provider.calls[1][2]["item_assessments"]
    assert "support_excerpts" not in assessments[0]
    assert "source_binding_origin" not in assessments[0]
    assert "source_binding_schema_version" not in assessments[0]


def test_single_claim_advisory_conflict_verdict_is_not_a_cross_claim_conflict():
    subject, _, failed, _ = service([
        item_verification(verdict="direct_conflict", asserts_direct_conflict=True), summary_verification()])
    _, items, _, _ = verify(subject)
    assert not failed and items[0]["server_direct_conflict_role"] is False


@pytest.mark.parametrize("refs", [["challenge"], ["foreign"], ["e1", "e1"], []])
def test_summary_positive_refs_require_current_scope_and_support_role(refs):
    subject, provider, failed, _ = service([item_verification(), summary_verification(evidence_refs=refs),
                                           summary_verification(evidence_refs=refs)])
    with pytest.raises(ServiceError) as caught:
        verify(subject)
    assert caught.value.code == "structured_output_invalid" and len(provider.calls) == 3
    assert failed[-1][-1] == 4


def test_multi_claim_direct_conflict_binds_each_independent_source_and_preserves_comparison_rules():
    proposed = landscape("These findings directly conflict.").model_dump()
    proposed["items"][0].update(category="conflict", claim_ids=["c1", "c2"], evidence_ids=["e1", "e2"])
    response = item_verification(verdict="direct_conflict", asserts_direct_conflict=True,
                                 evidence_refs=["e1", "e2"],
                                 dimensions={key: "comparable" for key in item_verification()["dimensions"]})
    subject, _, failed, _ = service([response, summary_verification()])
    _, assessments, _, _ = verify(subject, schemas.LandscapeOutput.model_validate(proposed))
    assert not failed and assessments[0]["server_direct_conflict_role"] is True
    assert [entry["evidence_id"] for entry in assessments[0]["support_excerpts"]] == ["e1", "e2"]


def test_canonical_binding_runs_the_unchanged_legacy_validator_on_full_passages(monkeypatch):
    subject, _, _, _ = service([item_verification(), summary_verification()])
    seen = []
    original = ScientificService._validate_verifier_sources

    def inspect(assessment, sources):
        seen.append(assessment)
        original(assessment, sources)

    monkeypatch.setattr(subject, "_validate_verifier_sources", inspect)
    verify(subject)
    bound = [entry for entry in seen if entry.support_excerpts]
    assert any(isinstance(entry, schemas.ConflictVerification) for entry in bound)
    assert any(isinstance(entry, schemas.SummaryVerification) for entry in bound)
    assert all(entry.support_excerpts[0].excerpt == SOURCE for entry in bound)
