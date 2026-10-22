"""M6 report narration and benchmark safety contracts."""

import pytest
from hashlib import sha256
import json
from pathlib import Path
import pymupdf

from app.persistence.base import Base
from app.persistence import models  # noqa: F401
from app.services.m6_schemas import ReportNarrative, validate_narrative
from app.services.m6_benchmark import BenchmarkSession, compare_sessions, report_from_files, markdown_report
from app.prompts.scientific import canonical_digest
from app.services.m6_render import render_html, render_markdown, report_sections
from app.services.m6_preflight import proxy_facts, classify_preflight
from app.services.m6_preflight import run_preflight
from pydantic import SecretStr
from types import SimpleNamespace
from app.providers.llm import LLMTimeout
from app.prompts.m6 import REPORT_PROMPT, REPORT_REPAIR


def test_report_scope_prompt_matches_conservative_language_contract():
    assert REPORT_PROMPT.version == "m6-v3"
    for instructions in (REPORT_PROMPT.text, REPORT_REPAIR):
        assert "even in negated" in instructions
        assert "source_refs" in instructions
        assert "fixed synthetic file" in instructions
    narrative = ReportNarrative(blocks=[{"section": "conclusion",
        "text": "Within the fixed synthetic file, the group mean ordering is consistent with the post hoc assertion. This is a descriptive demonstration limited to that file.",
        "source_refs": ["INTERPRETATION_1", "RESULT_1"]}])
    validate_narrative(narrative, {"INTERPRETATION_1", "RESULT_1"})
    with pytest.raises(ValueError, match="overclaims"):
        validate_narrative(ReportNarrative(blocks=[{"section": "conclusion",
            "text": "This does not establish a causal effect.", "source_refs": ["INTERPRETATION_1"]}]),
            {"INTERPRETATION_1"})


def test_report_timeout_budget_ordering():
    from app.config.settings import Settings
    from app.services.m6_report import report_timeout_budget_seconds

    settings = Settings(database_url="postgresql+psycopg://test:test@localhost/test",
                        provider_timeout_seconds=60, retry_count=2, _env_file=None)
    budget = report_timeout_budget_seconds(settings)
    assert budget.max_provider_attempts == 6
    assert budget.max_provider_wait_seconds == 360
    assert budget.max_valid_request_seconds == 390
    assert budget.max_valid_request_seconds < 420 < budget.active_stale_after_seconds
    assert budget.active_stale_after_seconds == 450


def test_report_narrative_accepts_only_known_source_tokens():
    narrative = ReportNarrative.model_validate({"blocks": [{"section": "executive_summary",
        "text": "Within the supplied literature, the selected comparison remains limited.",
        "source_refs": ["CLAIM_1"]}]})
    validate_narrative(narrative, {"CLAIM_1"})
    with pytest.raises(ValueError, match="source"):
        validate_narrative(narrative, {"CLAIM_2"})


@pytest.mark.parametrize("text", [
    "PAPER_FAKE reports a benefit.",
    "CLAIM_FAKE establishes an effect.",
    "The supplied comparison remains uncertain. RESULT_FAKE agrees with CLAIM_1.",
])
def test_report_rejects_fake_source_identity_inside_prose(text):
    narrative = ReportNarrative.model_validate({"blocks": [{"section": "conclusion",
        "text": text, "source_refs": ["CLAIM_1"]}]})
    with pytest.raises(ValueError, match="source"):
        validate_narrative(narrative, {"CLAIM_1"})


@pytest.mark.parametrize("text", [
    "There is no effect in this population.",
    "We found no effect in this population.",
    "The effect does not exist.",
    "There is no difference between these groups.",
])
def test_report_rejects_absolute_null_result_against_inconclusive_interpretation(text):
    narrative = ReportNarrative.model_validate({"blocks": [{"section": "conclusion",
        "text": text, "source_refs": ["INTERPRETATION_1"]}]})
    with pytest.raises(ValueError, match="overclaim"):
        validate_narrative(narrative, {"INTERPRETATION_1"},
            interpretation={"summary": "The analysis is inconclusive.", "uncertainty": "Small sample"})


def test_report_allows_cautious_null_result_with_accepted_interpretation():
    narrative = ReportNarrative.model_validate({"blocks": [{"section": "conclusion",
        "text": "The analysis did not provide sufficient evidence of an effect under this design.",
        "source_refs": ["INTERPRETATION_1"]}]})
    validate_narrative(narrative, {"INTERPRETATION_1"},
        interpretation={"summary": "The analysis is inconclusive.", "uncertainty": "Small sample"})


def _benchmark_session(mode: str, stages: list[str], duration: int = 1000,
                       failed: str | None = None) -> dict:
    scenario_path = Path(__file__).parents[1] / "evaluation" / "m6_scenario.json"
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    from datetime import datetime, timedelta, timezone
    start = datetime(2026, 10, 5, tzinfo=timezone.utc)
    events = [{"stage": stage, "started_at": (start + timedelta(seconds=index * duration / len(stages))).isoformat(),
        "completed_at": (start + timedelta(seconds=(index + 1) * duration / len(stages))).isoformat(),
        "status": "failed" if stage == failed else "succeeded"} for index, stage in enumerate(stages)]
    return BenchmarkSession.model_validate({"session_id": mode, "scenario_id": scenario["scenario_id"],
        "task": scenario["task"], "input_hash": sha256(scenario_path.read_bytes()).hexdigest(),
        "protocol_version": scenario["protocol_version"], "mode": mode, "events": events,
        "human_decisions": 0}).summary()


def test_one_stage_fabrication_is_not_measured():
    manual = _benchmark_session("manual", ["report_review"], 1000)
    copilot = _benchmark_session("research_copilot", ["report_review"], 400)
    result = compare_sessions(manual, copilot)
    assert manual["complete"] is False
    assert result == {"comparison": "NOT MEASURED", "time_saved_seconds": None,
                      "percentage_reduction": None}


def test_recorded_one_stage_files_expose_incomplete_status_without_percentage(tmp_path):
    from datetime import datetime, timedelta, timezone
    scenario_path = Path(__file__).parents[1] / "evaluation" / "m6_scenario.json"
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    paths = []
    for mode, seconds in (("manual", 1000), ("research_copilot", 400)):
        end = datetime(2026, 10, 5, tzinfo=timezone.utc) + timedelta(seconds=seconds)
        session = BenchmarkSession.model_validate({"session_id": mode, "scenario_id": scenario["scenario_id"],
            "task": scenario["task"], "input_hash": sha256(scenario_path.read_bytes()).hexdigest(),
            "mode": mode, "events": [{"stage": "report_review", "started_at": "2026-10-05T00:00:00Z",
                "completed_at": end.isoformat(),
                "status": "succeeded"}], "human_decisions": 0})
        path = tmp_path / f"{mode}.json"
        path.write_text(session.model_dump_json(), encoding="utf-8")
        paths.append(path)
    result = report_from_files(*paths)
    assert result["comparison"] == "NOT MEASURED"
    assert result["time_saved_seconds"] is None and result["percentage_reduction"] is None
    assert result["manual"]["complete"] is False
    assert len(result["manual"]["missing_stages"]) == 6
    assert "NOT MEASURED" in markdown_report(result)


def test_benchmark_missing_failed_duplicate_unknown_and_order_contract():
    scenario = json.loads((Path(__file__).parents[1] / "evaluation" / "m6_scenario.json").read_text(encoding="utf-8"))
    stages = scenario["stages"]
    full_manual = _benchmark_session("manual", stages)
    full_copilot = _benchmark_session("research_copilot", stages, 400)
    assert full_manual["complete"] is True
    assert compare_sessions(full_manual, full_copilot)["percentage_reduction"] == 60
    for mode in ("manual", "research_copilot"):
        partial = _benchmark_session(mode, stages[:-1])
        pair = (partial, full_copilot) if mode == "manual" else (full_manual, partial)
        assert compare_sessions(*pair)["comparison"] == "NOT MEASURED"
        failed = _benchmark_session(mode, stages, failed=stages[-1])
        pair = (failed, full_copilot) if mode == "manual" else (full_manual, failed)
        assert compare_sessions(*pair)["comparison"] == "NOT MEASURED"
        for invalid in (stages + ["unknown_stage"], stages + [stages[-1]], list(reversed(stages))):
            with pytest.raises(ValueError):
                _benchmark_session(mode, invalid)
    for field, value in (("scenario_id", "other"), ("protocol_version", "other"),
                         ("input_hash", "b" * 64)):
        assert compare_sessions(full_manual, {**full_copilot, field: value})["comparison"] == "NOT MEASURED"


def test_report_bundle_canonical_order_does_not_change_hash():
    from app.services.m6_report import canonicalize_source_bundle
    from copy import deepcopy
    first = {"landscape": {"items": [{"id": "b"}, {"id": "a"}]},
             "claims": [{"id": "c2"}, {"id": "c1"}], "evidence": [{"id": "e2"}, {"id": "e1"}]}
    second = {"landscape": {"items": list(reversed(deepcopy(first["landscape"]["items"])))},
              "claims": list(reversed(first["claims"])), "evidence": list(reversed(first["evidence"]))}
    assert canonicalize_source_bundle(first) == canonicalize_source_bundle(second)
    assert canonical_digest(canonicalize_source_bundle(first)) == canonical_digest(canonicalize_source_bundle(second))
    second["landscape"]["items"][0]["text"] = "Material change"
    assert canonical_digest(canonicalize_source_bundle(first)) != canonical_digest(canonicalize_source_bundle(second))


@pytest.mark.parametrize("text", [
    "This is the first study ever to prove the hypothesis.",
    "The hypothesis is proven.",
    "The correlation causes the outcome.",
    "The result was p = 0.0032.",
    "This confirms the hypothesis.",
    "This is an unprecedented finding.",
    "The observed difference is statistically significant.",
])
def test_report_narrative_rejects_untrusted_scientific_overclaim(text):
    narrative = ReportNarrative.model_validate({"blocks": [{"section": "conclusion",
        "text": text, "source_refs": ["RESULT_1"]}]})
    with pytest.raises(ValueError):
        validate_narrative(narrative, {"RESULT_1"})


def test_benchmark_comparison_requires_actual_matching_sessions():
    stages = json.loads((Path(__file__).parents[1] / "evaluation" / "m6_scenario.json").read_text(encoding="utf-8"))["stages"]
    manual = _benchmark_session("manual", stages)
    copilot = _benchmark_session("research_copilot", stages, 400)
    assert compare_sessions(manual, copilot)["percentage_reduction"] == 60
    assert compare_sessions(None, copilot)["comparison"] == "NOT MEASURED"
    assert compare_sessions({**manual, "scenario_id": "other"}, copilot)["comparison"] == "NOT MEASURED"
    assert compare_sessions({**manual, "duration_seconds": 0}, copilot)["comparison"] == "NOT MEASURED"


def test_benchmark_human_decisions_are_stage_backed():
    scenario_path = Path(__file__).parents[1] / "evaluation" / "m6_scenario.json"
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    event = {"stage": "report_review", "started_at": "2026-10-05T00:00:00Z",
             "completed_at": "2026-10-05T00:01:00Z", "status": "succeeded",
             "human_decisions": 1}
    fields = {"session_id": "s", "scenario_id": scenario["scenario_id"], "task": scenario["task"],
              "input_hash": sha256(scenario_path.read_bytes()).hexdigest(), "mode": "manual", "events": [event]}
    assert BenchmarkSession.model_validate({**fields, "human_decisions": 1}).summary()["duration_seconds"] == 60
    with pytest.raises(ValueError, match="Human decision"):
        BenchmarkSession.model_validate({**fields, "human_decisions": 2})


def test_benchmark_rejects_naive_or_mixed_timezone_timing():
    scenario_path = Path(__file__).parents[1] / "evaluation" / "m6_scenario.json"
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    fields = {"session_id": "s", "scenario_id": scenario["scenario_id"], "task": scenario["task"],
        "input_hash": sha256(scenario_path.read_bytes()).hexdigest(), "mode": "manual",
        "human_decisions": 0, "events": [{"stage": "report_review",
            "started_at": "2026-10-05T00:00:00", "completed_at": "2026-10-05T00:01:00Z",
            "status": "succeeded"}]}
    with pytest.raises(ValueError, match="timezone"):
        BenchmarkSession.model_validate(fields)


def test_report_has_immutable_source_snapshot_columns():
    columns = Base.metadata.tables["research_reports"].c
    for name in ("source_bundle", "source_bundle_hash", "narrative_blocks", "supersedes_id",
                 "review_status", "generation_run_id"):
        assert name in columns


def test_report_render_uses_stored_numbers_and_marks_draft():
    report = {"title": "Research report", "review_status": "pending_review",
              "source_bundle": {"research_question": "Question?", "results": [
                  {"id": "r1", "values": {"p_value": 0.032}}], "figures": [], "citations": []},
              "narrative_blocks": []}
    output = render_markdown(report)
    assert "DRAFT / NOT HUMAN ACCEPTED" in output
    assert "0.032" in output
    assert "Question?" in output
    assert "| Result ID | Metric | Value |" in output
    assert "<table>" in render_html(report)


def test_report_render_resolves_visible_claim_identity_from_source_map():
    report = {"title": "Report", "review_status": "pending_review", "source_bundle": {
        "research_question": "Question?", "results": [], "figures": [], "citations": [],
        "source_map": {"CLAIM_1": {"type": "claim", "id": "stored-claim-id"}}},
        "narrative_blocks": [{"section": "conclusion", "text": "The evidence remains limited.",
                              "source_refs": ["CLAIM_1"]}]}
    conclusion = next(paragraphs for key, _heading, paragraphs in report_sections(report) if key == "conclusion")
    assert "CLAIM_1 (claim stored-claim-id)" in conclusion[0]


def test_preflight_reports_proxy_presence_without_secret_values():
    facts = proxy_facts("dashscope.aliyuncs.com", {"HTTPS_PROXY": "http://user:secret@proxy:8080",
        "ALL_PROXY": "", "NO_PROXY": "localhost,.aliyuncs.com"})
    assert facts["https_proxy_configured"] is True
    assert facts["all_proxy_configured"] is False
    assert facts["no_proxy_matches_provider"] is True
    assert "secret" not in str(facts)


def test_preflight_classifies_blockers_and_provider_degradation():
    assert classify_preflight({"database": False, "provider": False}) == "BLOCKED"
    assert classify_preflight({"database": True, "migration": True,
        "pgvector": True, "storage": True, "provider": False}) == "DEGRADED"
    assert classify_preflight({"database": True, "migration": True,
        "pgvector": True, "storage": True, "provider": True,
        "frontend_http": False}) == "BLOCKED"


def test_preflight_hides_key_and_reports_missing_assets(monkeypatch, tmp_path):
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def execute(self, *_): return None
        def scalar(self, query):
            return "0010_m6_report_delivery" if "version_num" in str(query) else True
    monkeypatch.setattr("app.services.m6_preflight.create_engine", lambda *a, **k: SimpleNamespace(connect=lambda: Connection()))
    settings = SimpleNamespace(database_url="postgresql+psycopg://unused", storage_root=tmp_path,
        m5_storage_root=tmp_path, qwen_base_url="https://dashscope.aliyuncs.com/path",
        qwen_model="qwen3.8-max", embedding_model="model", qwen_api_key=SecretStr("top-secret"))
    output = run_preflight(settings, demo_assets=[tmp_path / "missing.pdf"])
    assert output["status"] == "BLOCKED"
    assert "demo_asset_missing" in output["failure_categories"]
    assert "top-secret" not in str(output)


def test_preflight_marks_unwritable_storage_blocked(monkeypatch, tmp_path):
    class BrokenEngine:
        def connect(self): raise OSError("db down")
    monkeypatch.setattr("app.services.m6_preflight.create_engine", lambda *a, **k: BrokenEngine())
    settings = SimpleNamespace(database_url="postgresql+psycopg://unused", storage_root=tmp_path / "missing",
        m5_storage_root=tmp_path / "missing", qwen_base_url="https://dashscope.aliyuncs.com/path",
        qwen_model="qwen3.8-max", embedding_model="model", qwen_api_key=None)
    output = run_preflight(settings)
    assert output["status"] == "BLOCKED"
    assert "storage_unwritable" in output["failure_categories"]


def test_preflight_detects_migration_mismatch(monkeypatch, tmp_path):
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def execute(self, *_): return None
        def scalar(self, query): return "0009_m5_data_analysis" if "version_num" in str(query) else True
    monkeypatch.setattr("app.services.m6_preflight.create_engine", lambda *a, **k: SimpleNamespace(connect=lambda: Connection()))
    settings = SimpleNamespace(database_url="postgresql+psycopg://unused", storage_root=tmp_path,
        m5_storage_root=tmp_path, qwen_base_url="https://dashscope.aliyuncs.com/path",
        qwen_model="qwen3.8-max", embedding_model="model", qwen_api_key=None)
    output = run_preflight(settings, demo_assets=[])
    assert output["checks"]["database"] is True
    assert output["checks"]["migration"] is False
    assert output["status"] == "BLOCKED"


def test_preflight_timeout_is_sanitized_and_single_attempt(monkeypatch, tmp_path):
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def execute(self, *_): return None
        def scalar(self, query): return "0010_m6_report_delivery" if "version_num" in str(query) else True
    class Socket:
        def __enter__(self): return self
        def __exit__(self, *_): return False
    calls = []
    class Provider:
        def __init__(self, *args, **kwargs):
            assert kwargs["base_url"] == "https://dashscope.aliyuncs.com/path"
            assert args[3] == 0
        def generate_structured(self, *args):
            calls.append(1)
            raise LLMTimeout("private transport detail", 1, category="provider_read_timeout")
    monkeypatch.setattr("app.services.m6_preflight.create_engine", lambda *a, **k: SimpleNamespace(connect=lambda: Connection()))
    monkeypatch.setattr("app.services.m6_preflight.socket.getaddrinfo", lambda *a, **k: [(1,)])
    monkeypatch.setattr("app.services.m6_preflight.socket.create_connection", lambda *a, **k: Socket())
    monkeypatch.setattr("app.services.m6_preflight.DashScopeLLMProvider", Provider)
    settings = SimpleNamespace(database_url="postgresql+psycopg://unused", storage_root=tmp_path,
        m5_storage_root=tmp_path, qwen_base_url="https://dashscope.aliyuncs.com/path",
        qwen_model="qwen3.8-max", embedding_model="model", qwen_api_key=SecretStr("top-secret"),
        provider_timeout_seconds=10, retry_count=2, qwen_reasoning_effort=None)
    output = run_preflight(settings, provider_smoke=True, demo_assets=[])
    assert calls == [1]
    assert output["status"] == "DEGRADED"
    assert output["provider_smoke"]["failure_category"] == "provider_read_timeout"
    assert "top-secret" not in str(output)
    assert "private transport detail" not in str(output)


def test_synthetic_demo_manifest_is_reproducible_and_labeled():
    scenario_path = Path(__file__).parents[1] / "evaluation" / "m6_scenario.json"
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    for asset in [*scenario["corpus_manifest"], scenario["dataset_manifest"]]:
        path = scenario_path.parent / asset["path"]
        assert sha256(path.read_bytes()).hexdigest() == asset["sha256"]
    for paper in scenario["corpus_manifest"]:
        with pymupdf.open(scenario_path.parent / paper["path"]) as document:
            assert "SYNTHETIC DEMONSTRATION" in document[0].get_text()
