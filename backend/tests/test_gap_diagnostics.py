"""All responses, identifiers and prose here are synthetic engineering fixtures."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from uuid import NAMESPACE_URL, uuid5

import httpx
import pytest

from app.config.settings import get_settings
from app.prompts.m4 import GAP_PROMPT
from app.providers.interfaces import LLMResult
from app.providers.llm import DashScopeLLMProvider, trace_provider_calls, _trace
from app.services.errors import ServiceError
from app.services.gap_diagnostics import (
    GapCapture, GapEvent, MAX_EVENTS, _ACTIVE, bind_gap_run, diagnose_gap, safe_path,
)
from app.services.m4_schemas import GapBatch
from app.services.scientific import ScientificService

PROJECT = "00000000-0000-4000-8000-000000000001"
PRIVATE = "SYNTHETIC_PRIVATE_PROSE_KEY_FIELD"


def proposal():
    return {"title": PRIVATE, "description": PRIVATE, "gap_type": "missing_comparison",
            "why_it_matters": PRIVATE, "uncertainty": PRIVATE, "assumptions": [],
            "novelty_scope": "supplied_literature", "landscape_item_ids": [PROJECT],
            "claim_ids": [PROJECT], "evidence_for_ids": [PROJECT], "evidence_against_ids": []}


def envelope(value):
    return {"model": "qwen3.8-max", "choices": [{"finish_reason": "stop",
            "message": {"content": json.dumps(value)}}]}


class UnitGap(ScientificService):
    def __init__(self, provider, sink):
        super().__init__(get_settings().model_copy(update={"qwen_model": "qwen3.8-max"}), provider, trace_sink=sink)
        self.failed = []
        self.validator = lambda value: None

    def _failed(self, *args):
        self.failed.append(args)

    @diagnose_gap
    def execute(self, project_id, revision, input_id, stage):
        run_id = str(uuid5(NAMESPACE_URL, project_id))
        bind_gap_run(run_id)
        return self._generate(run_id, GAP_PROMPT, GapBatch, {}, self.validator,
                              trace_stage="gap_generation")


@pytest.fixture
def mock_provider():
    clients = []
    def create(handler, retries=0):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        clients.append(client)
        return DashScopeLLMProvider("synthetic-offline-key", "qwen3.8-max", 120, retries, client=client)
    yield create
    for client in clients:
        asyncio.run(client.aclose())


def invoke(provider, sink=None):
    events = []
    service = UnitGap(provider, events.append if sink is None else sink)
    return service, events


def assert_private_absent(events):
    serialized = json.dumps(events)
    assert PRIVATE not in serialized
    assert "synthetic-offline-key" not in serialized
    assert all(GapEvent.model_validate(event) for event in events)
    assert all(event["remote_authenticated_receipt"] == "unknown" for event in events)


def test_connection_exhaustion_has_one_invocation_three_attempts(mock_provider):
    def respond(request):
        raise httpx.ConnectError(PRIVATE, request=request)
    service, events = invoke(mock_provider(respond, 2))
    with pytest.raises(ServiceError) as error:
        service.execute(PROJECT, 1, PROJECT, "gap")
    assert error.value.code == "llm_failed"
    assert service.failed[-1][1:] == ("provider_connect_error", 3)
    summary = events[-1]
    assert (summary["logical_invocations"], summary["adapter_invocations"],
            summary["adapter_request_attempts"], summary["transport_records"],
            summary["retry_decisions"], summary["structured_repairs"]) == (1, 1, 3, 3, 2, 0)
    assert len({e["logical_invocation_id"] for e in events if "logical_invocation_id" in e}) == 1
    assert len({e["adapter_call_id"] for e in events if "adapter_call_id" in e}) == 1
    assert_private_absent(events)


@pytest.mark.parametrize("case,category", [
    ("outer_json", "response_json"), ("envelope", "envelope"),
    ("incomplete", "incomplete_response"), ("content_type", "invalid_content_type"),
    ("output_json", "output_json"),
])
def test_decode_branch_and_repair_exhaustion(mock_provider, case, category):
    def respond(request):
        value = envelope({"gaps": [proposal()]})
        if case == "outer_json":
            return httpx.Response(200, content=PRIVATE)
        if case == "envelope":
            value["choices"] = []
        elif case == "incomplete":
            value["choices"][0]["finish_reason"] = "length"
        elif case == "content_type":
            value["choices"][0]["message"]["content"] = 123
        elif case == "output_json":
            value["choices"][0]["message"]["content"] = PRIVATE
        return httpx.Response(200, json=value)
    service, events = invoke(mock_provider(respond))
    with pytest.raises(ServiceError) as error:
        service.execute(PROJECT, 1, PROJECT, "gap")
    assert error.value.code == "structured_output_invalid"
    assert service.failed[-1][2] == 2
    assert any(e["layer"] == "decode" and e["category"] == category for e in events)
    assert events[-1]["logical_invocations"] == 2 and events[-1]["structured_repairs"] == 1
    assert {e["repair_round"] for e in events if e["layer"] == "adapter" and "repair_round" in e} == {0, 1}
    assert any(e["layer"] == "repair" and e["repair_round"] == 1 for e in events)
    assert_private_absent(events)


@pytest.mark.parametrize("case,code,path", [
    ("missing", "missing", "gaps.0.title"),
    ("enum", "literal_error", "gaps.0.gap_type"),
    ("extra", "extra_forbidden", "<extra_field>"),
])
def test_schema_paths_are_known_fields_or_fixed_placeholders(mock_provider, case, code, path):
    def respond(request):
        value = proposal()
        if case == "missing":
            del value["title"]
        elif case == "enum":
            value["gap_type"] = PRIVATE
        else:
            value[PRIVATE] = {PRIVATE: PRIVATE}
        return httpx.Response(200, json=envelope({"gaps": [value]}))
    service, events = invoke(mock_provider(respond))
    with pytest.raises(ServiceError):
        service.execute(PROJECT, 1, PROJECT, "gap")
    assert any(e.get("validation_code") == code and e.get("error_path") == path for e in events)
    assert_private_absent(events)


def test_missing_model_envelope_preserves_nonrepairable_identity_failure(mock_provider):
    service, events = invoke(mock_provider(lambda request: httpx.Response(200, json={PRIVATE: PRIVATE})))
    with pytest.raises(ServiceError) as error:
        service.execute(PROJECT, 1, PROJECT, "gap")
    assert error.value.code == "llm_failed"
    assert service.failed[-1][1:] == ("provider_model_mismatch", 1)
    assert events[-1]["logical_invocations"] == 1 and events[-1]["structured_repairs"] == 0
    assert any(e["category"] == "model_identity" for e in events)
    assert_private_absent(events)


@pytest.mark.parametrize("choice", [None, 123, []])
def test_nondictionary_choice_attributes_envelope_without_changing_error_or_repair(mock_provider, choice):
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={"model": "qwen3.8-max", "choices": [choice]})
    provider = mock_provider(respond)
    with pytest.raises(AttributeError):
        provider.generate_structured(GAP_PROMPT.text, GapBatch, {})
    service, events = invoke(provider)
    with pytest.raises(ServiceError) as error:
        service.execute(PROJECT, 1, PROJECT, "gap")
    assert error.value.code == "llm_failed" and len(calls) == 2
    assert events[-1]["logical_invocations"] == 1 and events[-1]["structured_repairs"] == 0
    assert any(e["layer"] == "decode" and e["category"] == "envelope" for e in events)
    assert_private_absent(events)


def test_generic_pre_result_value_error_keeps_legacy_count_distinct():
    class Provider:
        def generate_structured(self, *args):
            raise ValueError(PRIVATE)
    service, events = invoke(Provider())
    with pytest.raises(ServiceError):
        service.execute(PROJECT, 1, PROJECT, "gap")
    assert service.failed[-1][2] == 2
    assert events[-1]["logical_invocations"] == 2
    assert events[-1]["adapter_request_attempts"] == events[-1]["transport_records"] == 0
    assert not events[-1]["capture_complete"]
    assert sum(e["category"] == "pre_result_value_error" for e in events) == 2
    assert_private_absent(events)


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
def test_sink_failure_cannot_change_success_or_add_calls(mock_provider, failure):
    calls = []
    observed = []
    def respond(request):
        calls.append(request.content)
        return httpx.Response(200, json=envelope({"gaps": [proposal()]}))
    def sink(event):
        observed.append(event)
        raise failure(PRIVATE)
    service, _ = invoke(mock_provider(respond), sink)
    result, attempts = service.execute(PROJECT, 1, PROJECT, "gap")
    assert attempts == 1 and len(result.gaps) == len(calls) == 1 and not service.failed
    assert not observed[-1]["capture_complete"] and observed[-1]["sink_failures"] > 0
    assert _ACTIVE.get() is None
    assert_private_absent(observed)


def test_collector_failure_cannot_replace_provider_success(mock_provider, monkeypatch):
    def fail(*args, **kwargs):
        raise asyncio.CancelledError(PRIVATE)
    monkeypatch.setattr(GapCapture, "emit", fail)
    service, _ = invoke(mock_provider(lambda request: httpx.Response(200, json=envelope({"gaps": [proposal()]}))))
    _, attempts = service.execute(PROJECT, 1, PROJECT, "gap")
    assert attempts == 1 and not service.failed and _ACTIVE.get() is None


def test_provider_cancellation_is_preserved_and_correlation_is_reset():
    cancellation = asyncio.CancelledError(PRIVATE)
    class Provider:
        def generate_structured(self, *args):
            raise cancellation
    service, events = invoke(Provider())
    with pytest.raises(asyncio.CancelledError) as error:
        service.execute(PROJECT, 1, PROJECT, "gap")
    assert error.value is cancellation and not service.failed and _ACTIVE.get() is None
    assert any(e["category"] == "cancelled" for e in events)
    assert_private_absent(events)


def test_payload_prompt_schema_and_results_are_unchanged(mock_provider):
    requests = []
    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=envelope({"gaps": [proposal()]}))
    provider = mock_provider(respond)
    expected = provider.generate_structured(GAP_PROMPT.text, GapBatch, {})
    service, events = invoke(provider)
    actual, attempts = service.execute(PROJECT, 1, PROJECT, "gap")
    assert requests[0] == requests[1]
    assert actual.model_dump() == expected.output.model_dump() and attempts == 1
    assert_private_absent(events)


def test_bound_and_malformed_events_disclose_incomplete_capture():
    events = []
    capture = GapCapture(PROJECT, events.append)
    capture.run_id = PROJECT
    capture.emit({"layer": PRIVATE, "outcome": "pass", PRIVATE: PRIVATE})
    for _ in range(MAX_EVENTS + 20):
        capture.emit({"layer": "provider", "outcome": "pass", "headers": PRIVATE, "source_ids": [PRIVATE]})
    capture.emit({"layer": "capture", "outcome": "summary"}, summary=True)
    assert len(events) == MAX_EVENTS + 1
    assert events[-1]["invalid_events"] == 1 and events[-1]["dropped_events"] == 20
    assert not events[-1]["capture_complete"]
    assert_private_absent(events)


@pytest.mark.parametrize("nested_model", [True, False])
def test_unvalidated_nested_transport_values_never_reach_sink(nested_model):
    from app.providers.transport_diagnostics import PhaseRecord, TransportAttempt
    record = TransportAttempt(PROJECT).finish("transport_complete", False, "not_owned")
    malicious = PhaseRecord.model_construct(seq=1, phase=PRIVATE, state="complete",
        traffic="unknown", elapsed_ms=0, causes=[])
    record["events"] = [malicious if nested_model else malicious.model_dump(warnings=False)]
    events = []
    capture = GapCapture(PROJECT, events.append)
    capture.emit({"layer": "transport", "outcome": "pass", "transport_diagnostic": record})
    capture.emit({"layer": "capture", "outcome": "summary"}, summary=True)
    assert len(events) == 1 and events[0]["invalid_events"] == 1
    assert not events[0]["capture_complete"]
    assert_private_absent(events)


@pytest.mark.parametrize("path", [PRIVATE, "gaps.10.title", "gaps.0.title.1", "gaps.0.assumptions.10000"])
def test_impossible_schema_paths_are_replaced_before_emission(path):
    assert safe_path(path, "missing") == "<unknown_field>"


def test_concurrent_runs_have_context_local_correlation(mock_provider):
    events = []
    service, _ = invoke(mock_provider(lambda request: httpx.Response(200, json=envelope({"gaps": [proposal()]}))), events.append)
    projects = [PROJECT, "00000000-0000-4000-8000-000000000002"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda project: service.execute(project, 1, project, "gap"), projects))
    assert all(attempts == 1 for _, attempts in results)
    for project in projects:
        selected = [e for e in events if e["project_id"] == project]
        assert selected and {e["generation_run_id"] for e in selected} == {str(uuid5(NAMESPACE_URL, project))}
        assert len({e["logical_invocation_id"] for e in selected if "logical_invocation_id" in e}) == 1
    assert _ACTIVE.get() is None
    assert_private_absent(events)


def test_unrelated_provider_trace_context_is_restored(mock_provider):
    outer = []
    service, events = invoke(mock_provider(lambda request: httpx.Response(200, json=envelope({"gaps": [proposal()]}))))
    with trace_provider_calls(outer.append):
        service.execute(PROJECT, 1, PROJECT, "gap")
        service.provider.generate_structured(GAP_PROMPT.text, GapBatch, {})
    assert events and outer and not any(e["layer"] == "adapter" for e in outer)
    assert _ACTIVE.get() is None


def test_malformed_provider_event_cannot_change_validation_or_hide_incompleteness():
    class Provider:
        def generate_structured(self, *args):
            _trace({"layer": PRIVATE, "outcome": "pass", "provider_role": PRIVATE,
                    "repair_round": PRIVATE, PRIVATE: PRIVATE})
            _trace([PRIVATE])
            return LLMResult(GapBatch.model_validate({"gaps": [proposal()]}), "qwen3.8-max", 1)
    service, events = invoke(Provider())
    _, attempts = service.execute(PROJECT, 1, PROJECT, "gap")
    assert attempts == 1 and not service.failed
    assert events[-1]["invalid_events"] == 2 and not events[-1]["capture_complete"]
    assert_private_absent(events)


def test_logger_failure_is_isolated_before_any_business_decision(mock_provider, monkeypatch):
    observed = []
    def fail(message, serialized):
        observed.append(serialized)
        raise asyncio.CancelledError(PRIVATE)
    monkeypatch.setattr("app.services.gap_diagnostics._LOG.info", fail)
    service = UnitGap(mock_provider(lambda request: httpx.Response(200, json=envelope({"gaps": [proposal()]}))), None)
    _, attempts = service.execute(PROJECT, 1, PROJECT, "gap")
    assert attempts == 1 and not service.failed and _ACTIVE.get() is None
    assert observed and PRIVATE not in "".join(observed)


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
def test_invocation_bookkeeping_failure_cannot_prevent_provider_call(mock_provider, monkeypatch, failure):
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(200, json=envelope({"gaps": [proposal()]}))
    def broken_identity():
        raise failure(PRIVATE)
    monkeypatch.setattr("app.services.gap_diagnostics.uuid4", broken_identity)
    service, events = invoke(mock_provider(respond))
    _, attempts = service.execute(PROJECT, 1, PROJECT, "gap")
    assert len(calls) == 1 and attempts == 1 and not service.failed
    assert not events[-1]["capture_complete"] and _ACTIVE.get() is None
    assert_private_absent(events)


def test_default_capture_uses_existing_worker_log_channel(mock_provider, caplog):
    service = UnitGap(mock_provider(lambda request: httpx.Response(200, json=envelope({"gaps": [proposal()]}))), None)
    with caplog.at_level("INFO", logger="uvicorn.error"):
        _, attempts = service.execute(PROJECT, 1, PROJECT, "gap")
    records = [r for r in caplog.records if r.getMessage().startswith("gap_trace ")]
    assert attempts == 1 and records and all(r.name == "uvicorn.error" for r in records)
    events = [json.loads(r.getMessage().removeprefix("gap_trace ")) for r in records]
    assert events[-1]["outcome"] == "summary" and events[-1]["capture_complete"]
    assert_private_absent(events)


def test_nested_unrelated_stage_suspends_and_restores_gap_correlation():
    class UnrelatedProvider:
        def generate_structured(self, *args):
            assert _ACTIVE.get() is None
            return LLMResult(GapBatch.model_validate({"gaps": [proposal()]}), "qwen3.8-max", 1)
    unrelated_events = []
    unrelated = UnitGap(UnrelatedProvider(), unrelated_events.append)
    class Provider:
        def generate_structured(self, *args):
            before = _ACTIVE.get()
            unrelated.execute("00000000-0000-4000-8000-000000000002", 1, PROJECT, "hypothesis")
            assert _ACTIVE.get() is before and before.project_id == PROJECT
            return LLMResult(GapBatch.model_validate({"gaps": [proposal()]}), "qwen3.8-max", 1)
    service, events = invoke(Provider())
    _, attempts = service.execute(PROJECT, 1, PROJECT, "gap")
    assert attempts == 1 and not unrelated_events and _ACTIVE.get() is None
    assert all(e["project_id"] == PROJECT for e in events)
