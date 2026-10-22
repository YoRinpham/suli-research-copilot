"""Synthetic engineering diagnostics, only in a fenced disposable database."""
import os
import asyncio
from copy import deepcopy
import json
import httpx
import pytest
from test_gap_capacity_database import exact_gap_case, assert_failed_atomic, gap_runs
from app.providers.interfaces import LLMResult
from app.services.errors import ServiceError
from app.services.gap_context import GapContext
from app.services.m4_schemas import GapBatch, M4Assessment
from app.services.gap_diagnostics import _ACTIVE
from app.providers.llm import DashScopeLLMProvider

pytestmark = pytest.mark.skipif(os.getenv("RUN_DB_INTEGRATION") != "1", reason="Requires fenced disposable database")


def test_gap_initial_and_advisory_diagnostics_exist(exact_gap_case):
    service, provider, project, landscape, *_ = exact_gap_case
    events = []
    service.trace_sink = events.append
    result = service.generate_gaps(project, landscape, 1)
    assert len(result["gaps"]) == 1 and len(provider.calls) == 2
    stages = {event.get("stage") for event in events}
    assert {"gap_generation", "gap_assessment"} <= stages
    assert all(event["generation_run_id"] == result["generation_run_id"] for event in events)
    assert all(event["project_id"] == project for event in events)


@pytest.mark.parametrize("branch", ["foreign_item", "incomplete_lineage", "claim_mismatch"])
def test_gap_source_branches_are_attributable_without_logging_identifiers(exact_gap_case, branch):
    service, provider, project, landscape, context, *_ = exact_gap_case
    original = provider.generate_structured
    foreign = "00000000-0000-4000-8000-000000000999"
    def generate(prompt, schema, encoded):
        result = original(prompt, schema, encoded)
        value = deepcopy(result.output)
        if schema is GapBatch:
            gap = value["gaps"][0]
            if branch == "foreign_item":
                gap["landscape_item_ids"] = [foreign]
            elif branch == "incomplete_lineage":
                gap["claim_ids"] = [foreign]
            else:
                evidence = gap["evidence_for_ids"][0]
                by_claim = {row["id"]: row for row in context["claims"]}
                gap["claim_ids"] = [next(cid for cid in gap["claim_ids"] if evidence not in
                    by_claim[cid]["evidence_for_ids"] + by_claim[cid]["evidence_against_ids"])]
        return LLMResult(value, result.model_name, result.attempt_count)
    provider.generate_structured = generate
    events = []
    service.trace_sink = events.append
    with pytest.raises(ServiceError) as error:
        service.generate_gaps(project, landscape, 1)
    assert error.value.code == "structured_output_invalid"
    codes = {e.get("validation_code") for e in events}
    expected = {"foreign_item": "gap_foreign_landscape_item", "incomplete_lineage": "gap_incomplete_lineage",
                "claim_mismatch": "gap_evidence_claim_mismatch"}[branch]
    assert expected in codes and provider.assessment_calls == 0
    assert foreign not in __import__("json").dumps(events)
    assert_failed_atomic(exact_gap_case, 2)


@pytest.mark.parametrize("over", [0, 1])
def test_gap_capacity_exact_boundary_and_one_character_over_are_numeric(exact_gap_case, over):
    service, provider, project, landscape, context, *_ = exact_gap_case
    representation = GapContext(context, 1, 30000)
    original = provider.generate_structured
    def generate(prompt, schema, encoded):
        result = original(prompt, schema, encoded)
        if schema is GapBatch:
            value = deepcopy(result.output)
            gap = value["gaps"][0]
            gap["assumptions"] = ["x"]
            remaining = 30000 - len(str(representation.assessment_context(gap)))
            gap["assumptions"] = ["x" * (1 + remaining + over)]
            assert len(str(representation.assessment_context(gap))) == 30000 + over
            return LLMResult(value, result.model_name, result.attempt_count)
        return result
    provider.generate_structured = generate
    events = []
    service.trace_sink = events.append
    if over:
        with pytest.raises(ServiceError) as error:
            service.generate_gaps(project, landscape, 1)
        assert error.value.code == "structured_output_invalid" and provider.assessment_calls == 0
        assert_failed_atomic(exact_gap_case, 2)
    else:
        result = service.generate_gaps(project, landscape, 1)
        assert result["gaps"][0]["selection_status"] == "candidate" and provider.assessment_calls == 1
    admissions = [e for e in events if e["layer"] == "capacity" and e.get("candidate_index") == 0]
    assert admissions and all(e["measured_size"] == 30000 + over and e["limit"] == 30000 for e in admissions)
    assert all(e["candidate_allowance"] == 1068 for e in admissions)


def test_gap_oversized_tenth_candidate_blocks_every_advisory(exact_gap_case):
    service, provider, project, landscape, *_ = exact_gap_case
    provider.count = 10
    provider.overflow = "always"
    provider.overflow_index = 9
    events = []
    service.trace_sink = events.append
    with pytest.raises(ServiceError):
        service.generate_gaps(project, landscape, 1)
    admissions = [e for e in events if e["layer"] == "capacity" and "candidate_index" in e]
    assert {e["candidate_index"] for e in admissions if e["outcome"] == "pass"} == set(range(9))
    assert {e["candidate_index"] for e in admissions if e["outcome"] == "fail"} == {9}
    assert {e["repair_round"] for e in admissions} == {0, 1}
    assert provider.generation_calls == 2 and provider.assessment_calls == 0
    assert not any(e["stage"] == "gap_assessment" for e in events)
    assert_failed_atomic(exact_gap_case, 2)


def test_gap_all_candidates_admitted_before_first_advisory(exact_gap_case):
    service, provider, project, landscape, *_ = exact_gap_case
    provider.count = 3
    events = []
    service.trace_sink = events.append
    result = service.generate_gaps(project, landscape, 1)
    first_advisory = next(i for i, e in enumerate(events) if e["stage"] == "gap_assessment")
    assert {e["candidate_index"] for e in events[:first_advisory] if e["layer"] == "capacity" and
            "candidate_index" in e and e["outcome"] == "pass"} == {0, 1, 2}
    assert {e["candidate_index"] for e in events if e["stage"] == "gap_assessment"} == {0, 1, 2}
    assert len(result["gaps"]) == 3 and all(gap["selection_status"] == "candidate" for gap in result["gaps"])


@pytest.mark.parametrize("index", [0, 1, 2])
def test_gap_first_and_later_advisory_repair_exhaustion_are_atomic(exact_gap_case, index):
    service, provider, project, landscape, *_ = exact_gap_case
    provider.count = 3
    provider.fail_assessment_index = index
    events = []
    service.trace_sink = events.append
    with pytest.raises(ServiceError) as error:
        service.generate_gaps(project, landscape, 1)
    assert error.value.code == "structured_output_invalid"
    failures = [e for e in events if e["stage"] == "gap_assessment" and e["layer"] == "pydantic" and e["outcome"] == "fail"]
    assert failures and {e["candidate_index"] for e in failures} == {index}
    assert {e["repair_round"] for e in failures} == {0, 1}
    assert {e["error_path"] for e in failures} == {"verdict"}
    assert events[-1]["structured_repairs"] == 1
    assert_failed_atomic(exact_gap_case, index + 3)


def test_gap_advisory_source_reference_failure_is_distinct(exact_gap_case):
    service, provider, project, landscape, *_ = exact_gap_case
    original = provider.generate_structured
    def generate(prompt, schema, encoded):
        result = original(prompt, schema, encoded)
        if schema is M4Assessment:
            value = deepcopy(result.output)
            value["source_ids"] = ["SYNTHETIC_ATTACKER_GENERATED_ID"]
            return LLMResult(value, result.model_name, result.attempt_count)
        return result
    provider.generate_structured = generate
    events = []
    service.trace_sink = events.append
    with pytest.raises(ServiceError):
        service.generate_gaps(project, landscape, 1)
    assert any(e["category"] == "advisory_source_reference" and e["candidate_index"] == 0 for e in events)
    assert "SYNTHETIC_ATTACKER_GENERATED_ID" not in __import__("json").dumps(events)
    assert_failed_atomic(exact_gap_case, 3)


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
def test_gap_sink_failure_does_not_change_canonical_persistence(exact_gap_case, failure):
    service, provider, project, landscape, *_ = exact_gap_case
    observed = []
    def sink(event):
        observed.append(event)
        raise failure("Synthetic engineering collector failure")
    service.trace_sink = sink
    result = service.generate_gaps(project, landscape, 1)
    assert len(provider.calls) == 2 and result["gaps"][0]["selection_status"] == "candidate"
    proposed = provider.generated[0]["gaps"][0]
    assert all(result["gaps"][0][key] == value for key, value in proposed.items())
    assert gap_runs(project)[0].status == "succeeded"
    assert not observed[-1]["capture_complete"] and _ACTIVE.get() is None


def test_gap_pre_result_value_error_preserves_failed_run_only_legacy_semantics(exact_gap_case):
    service, provider, project, landscape, *_ = exact_gap_case
    def fail(*args):
        raise ValueError("SYNTHETIC_PRIVATE_PRE_RESULT_ERROR")
    provider.generate_structured = fail
    events = []
    service.trace_sink = events.append
    with pytest.raises(ServiceError):
        service.generate_gaps(project, landscape, 1)
    assert events[-1]["logical_invocations"] == 2 and events[-1]["adapter_request_attempts"] == 0
    assert events[-1]["legacy_cumulative_attempts"] == 2 and not events[-1]["capture_complete"]
    assert_failed_atomic(exact_gap_case, 2)


def test_gap_collector_cancellation_cannot_change_successful_persistence(exact_gap_case, monkeypatch):
    from app.services.gap_diagnostics import GapCapture
    service, provider, project, landscape, *_ = exact_gap_case
    def fail(*args, **kwargs):
        raise asyncio.CancelledError("Synthetic collector cancellation")
    monkeypatch.setattr(GapCapture, "emit", fail)
    result = service.generate_gaps(project, landscape, 1)
    assert result["gaps"][0]["selection_status"] == "candidate" and len(provider.calls) == 2
    assert gap_runs(project)[0].status == "succeeded" and _ACTIVE.get() is None


def test_gap_provider_cancellation_preserves_existing_started_run_semantics(exact_gap_case):
    service, provider, project, landscape, *_ = exact_gap_case
    cancellation = asyncio.CancelledError("Synthetic provider cancellation")
    def fail(*args):
        raise cancellation
    provider.generate_structured = fail
    events = []
    service.trace_sink = events.append
    with pytest.raises(asyncio.CancelledError) as error:
        service.generate_gaps(project, landscape, 1)
    assert error.value is cancellation and service.list_gaps(project) == []
    run = gap_runs(project)[0]
    assert run.status == "started" and run.attempt_count == 0
    assert any(e["category"] == "cancelled" for e in events) and _ACTIVE.get() is None


@pytest.mark.parametrize("index", [0, 1])
def test_gap_mock_adapter_correlates_first_and_later_advisory_attempts(exact_gap_case, index):
    service, fixture, project, landscape, *_ = exact_gap_case
    fixture.count = 2
    fixture.fail_assessment_index = index
    def respond(request):
        payload = json.loads(request.content)
        schema = GapBatch if payload["response_format"]["json_schema"]["name"] == "GapBatch" else M4Assessment
        context = json.loads(payload["messages"][1]["content"])
        output = fixture.generate_structured(payload["messages"][0]["content"], schema, context).output
        return httpx.Response(200, json={"model": "qwen3.8-max", "choices": [{"finish_reason": "stop",
            "message": {"content": json.dumps(output)}}]})
    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    service.provider = DashScopeLLMProvider("synthetic-offline-key", "qwen3.8-max", 120, 2, client=client,
                                          reasoning_effort="low")
    events = []
    service.trace_sink = events.append
    try:
        with pytest.raises(ServiceError) as error:
            service.generate_gaps(project, landscape, 1)
        assert error.value.code == "structured_output_invalid"
        expected = index + 3
        summary = events[-1]
        assert summary["logical_invocations"] == summary["adapter_invocations"] == expected
        assert summary["adapter_request_attempts"] == summary["transport_records"] == expected
        assert summary["structured_repairs"] == 1 and summary["retry_decisions"] == 0
        assert summary["capture_complete"] and summary["remote_authenticated_receipt"] == "unknown"
        failing = [e for e in events if e["stage"] == "gap_assessment" and e["layer"] == "pydantic" and e["outcome"] == "fail"]
        assert {e["candidate_index"] for e in failing} == {index}
        assert len({e["logical_invocation_id"] for e in failing}) == 2
        for event in failing:
            matching = [e for e in events if e.get("logical_invocation_id") == event["logical_invocation_id"]]
            assert len({e["adapter_call_id"] for e in matching if "adapter_call_id" in e}) == 1
        assert "synthetic-offline-key" not in json.dumps(events)
        assert_failed_atomic(exact_gap_case, expected)
    finally:
        asyncio.run(client.aclose())
