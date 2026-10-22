"""Interpretation failures identify the rule without exposing generated prose."""

import json

import pytest

from app.config.settings import get_settings
from app.prompts.m5 import INTERPRETATION_PROMPT
from app.providers.interfaces import LLMResult
from app.services.errors import ServiceError
from app.services.m5_ai_schemas import InterpretationProposal, validate_interpretation_language
from app.services.scientific import ScientificService


@pytest.mark.parametrize("summary,missing_sources,expected", [
    ("PRIVATE prose with 7.09", False, "interpretation_numeric_prose"),
    ("PRIVATE prose proves the hypothesis", False, "interpretation_overclaim"),
    ("PRIVATE cautious prose", True, "interpretation_source_set_mismatch"),
])
def test_interpretation_trace_names_rule_without_generated_text(monkeypatch, summary, missing_sources, expected):
    candidate = InterpretationProposal(
        summary=summary, hypothesis_relationship="Limited to this file.",
        uncertainty="Post hoc synthetic example.", limitations=[], alternative_explanations=[],
        statistical_result_ids=["result"], figure_artifact_ids=[])

    class Provider:
        def generate_structured(self, prompt, schema, context):
            return LLMResult(output=candidate, model_name=get_settings().qwen_model, attempt_count=1)

    def validate(proposal):
        validate_interpretation_language(proposal)
        if missing_sources:
            raise ValueError("Interpretation must reference the complete result and figure set")

    events, failures = [], []
    service = ScientificService(get_settings(), Provider(), trace_sink=events.append)
    monkeypatch.setattr(service, "_failed", lambda *args: failures.append(args))
    with pytest.raises(ServiceError, match="structured output is invalid"):
        service._generate("run", INTERPRETATION_PROMPT, InterpretationProposal, {}, validate,
                          trace_stage="result_interpretation_generation")
    rejected = [event for event in events if event.get("layer") == "domain_validation"]
    assert [event["validation_code"] for event in rejected] == [expected, expected]
    assert failures == [("run", "structured_output_invalid", 2)]
    serialized = json.dumps(events)
    assert "PRIVATE" not in serialized
    assert "7.09" not in serialized
