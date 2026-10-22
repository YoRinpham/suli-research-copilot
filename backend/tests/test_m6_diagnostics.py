"""Report failures distinguish content constraints without retaining content."""

import json

import pytest

from app.config.settings import get_settings
from app.prompts.m6 import REPORT_PROMPT
from app.providers.interfaces import LLMResult
from app.services.errors import ServiceError
from app.services.m6_schemas import ReportNarrative, validate_narrative
from app.services.scientific import ScientificService


@pytest.mark.parametrize("text,refs,code", [
    ("PRIVATE numeric value 7.09", ["RESULT_1"], "report_numeric_prose"),
    ("PRIVATE source RESULT_1", ["RESULT_1"], "report_identity_in_prose"),
    ("PRIVATE prose proves the hypothesis", ["RESULT_1"], "report_overclaim"),
    ("PRIVATE cautious prose", ["UNKNOWN"], "report_source_not_allowed"),
])
def test_report_trace_identifies_constraint_without_text(monkeypatch, text, refs, code):
    narrative = ReportNarrative(blocks=[{"section": "conclusion", "text": text, "source_refs": refs}])

    class Provider:
        def generate_structured(self, prompt, schema, context):
            return LLMResult(output=narrative, model_name=get_settings().qwen_model, attempt_count=1)

    events, failures = [], []
    service = ScientificService(get_settings(), Provider(), trace_sink=events.append)
    monkeypatch.setattr(service, "_failed", lambda *args: failures.append(args))
    with pytest.raises(ServiceError, match="structured output is invalid"):
        service._generate("run", REPORT_PROMPT, ReportNarrative, {},
                          lambda output: validate_narrative(output, {"RESULT_1"}),
                          trace_stage="research_report_generation")
    rejected = [event for event in events if event.get("layer") == "domain_validation"]
    assert [event["validation_code"] for event in rejected] == [code, code]
    assert failures == [("run", "structured_output_invalid", 2)]
    assert "PRIVATE" not in json.dumps(events)
    assert "7.09" not in json.dumps(events)
