"""Opt-in focused synthetic M3 regression through real Qwen and PostgreSQL."""

import json
import os
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.persistence.database import get_engine
from app.persistence.models import GenerationRun
from app.providers.llm import DashScopeLLMProvider
from app.services.scientific_schemas import ClaimBatch, LandscapeOutput
from test_m3_database import live_m3
from test_m3_semantic_database import two_claims


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1" or os.getenv("RUN_REAL_QWEN") != "1",
    reason="Requires migrated PostgreSQL, real Qwen credential, and explicit opt-in",
)


class RecordingQwen:
    def __init__(self):
        settings = get_settings()
        assert settings.qwen_api_key is not None
        self.inner = DashScopeLLMProvider(settings.qwen_api_key.get_secret_value(), "qwen3.8-max",
                                          max(settings.provider_timeout_seconds, 180), settings.retry_count,
                                          base_url=settings.qwen_base_url)
        self.outputs = []
        self.errors = []

    def generate_structured(self, prompt, output_schema, context):
        try:
            result = self.inner.generate_structured(prompt, output_schema, context)
        except Exception as exc:
            response = getattr(exc.__cause__, "response", None)
            self.errors.append({"schema": output_schema.__name__, "error_type": type(exc).__name__,
                                "cause_type": type(exc.__cause__).__name__ if exc.__cause__ else None,
                                "http_status": response.status_code if response is not None else None,
                                "provider_code": str(response.json().get("code", "unknown"))[:80]
                                if response is not None and response.headers.get("content-type", "").startswith("application/json") else None})
            raise
        self.outputs.append({"schema": output_schema.__name__, "output": result.output.model_dump(),
                             "attempt_count": result.attempt_count})
        return result


def save_result(record: dict[str, object]) -> None:
    path = Path(__file__).parents[2] / "data" / "m3-live-semantic-results.json"
    records = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    records = [item for item in records if item["scenario_id"] != record["scenario_id"]]
    records.append(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.mark.parametrize("scenario_id,source_text", [
    ("association_causal_trap", "In an observational cohort, X was associated with Y; no intervention was performed."),
    ("no_significance_trap", "A scored 80% accuracy and B scored 78%; no significance test was reported."),
    ("unsupported_comparison", "Method A achieved 80% accuracy on Dataset X; no competing method was evaluated."),
    ("conditional_finding", "Method A improved accuracy only under condition X; performance without X was not evaluated."),
])
def test_real_claim_semantic_boundary(live_m3, scenario_id, source_text):
    client, _, source = live_m3
    project_id, evidence_id, _, _ = source(source_text)
    recorder = RecordingQwen()
    client.app.state.llm_provider = recorder
    response = client.post(f"/api/v1/projects/{project_id}/claims/generate", json={
        "evidence_ids": [evidence_id], "expected_question_revision": 1})
    record = {"scenario_id": scenario_id, "stage": "claim", "model": "qwen3.8-max",
              "generator_proposal": [entry["output"] for entry in recorder.outputs if entry["schema"] == ClaimBatch.__name__],
              "ai_semantic_assessment": [entry["output"] for entry in recorder.outputs if entry["schema"] == "ClaimVerification"],
              "provider_errors": recorder.errors,
              "status_code": response.status_code,
              "final_persisted": response.json().get("claims", []),
              "human_review_before_test_action": [item.get("human_grounding_status") for item in response.json().get("claims", [])],
              "downstream_eligibility_before_test_action": [item.get("eligible_for_generation") for item in response.json().get("claims", [])],
              "error": response.json().get("error")}
    run_id = response.json().get("generation_run_id") or (record["error"] or {}).get("details", {}).get("generation_run_id")
    if run_id:
        with Session(get_engine()) as session:
            run = session.get(GenerationRun, run_id)
            record.update({"generation_run_id": run_id, "provider_attempt_count": run.attempt_count,
                           "prompt_version": run.prompt_version, "prompt_bundle_hash": run.prompt_hash,
                           "total_latency_seconds": (run.completed_at - run.created_at).total_seconds()})
    save_result(record)
    assert response.status_code == 201, response.text


@pytest.mark.parametrize("scenario_id,first,second", [
    ("true_conflict", "On Dataset X under P, A scored 80% accuracy.", "On Dataset X under P, A scored 60% accuracy."),
    ("different_dataset", "A scored 80% accuracy on Dataset X.", "A scored 60% accuracy on Dataset Y."),
    ("metric_mismatch", "A scored 80% accuracy on Dataset X.", "A scored 60% recall on Dataset X."),
    ("different_baseline_summary", "A beat baseline B by 10% on Dataset X.", "A beat baseline C by 5% on Dataset X."),
])
def test_real_landscape_semantic_boundary(live_m3, scenario_id, first, second):
    client, _, _ = live_m3
    project_id, claims, _ = two_claims(live_m3, first, second)
    recorder = RecordingQwen()
    client.app.state.llm_provider = recorder
    response = client.post(f"/api/v1/projects/{project_id}/landscapes/generate", json={
        "claim_ids": claims, "expected_question_revision": 1})
    record = {"scenario_id": scenario_id, "stage": "landscape", "model": "qwen3.8-max",
              "generator_proposal": [entry["output"] for entry in recorder.outputs if entry["schema"] == LandscapeOutput.__name__],
              "ai_conflict_assessment": [entry["output"] for entry in recorder.outputs if entry["schema"] == "ConflictVerification"],
              "ai_summary_assessment": [entry["output"] for entry in recorder.outputs if entry["schema"] == "SummaryVerification"],
              "input_review_provenance": "Synthetic deterministic test actions in two_claims; not real researcher evaluation",
              "provider_errors": recorder.errors,
              "status_code": response.status_code,
              "final_persisted": response.json().get("landscape"),
              "human_review_before_test_action": (response.json().get("landscape") or {}).get("review_status"),
              "downstream_eligibility_before_test_action": (response.json().get("landscape") or {}).get("eligible_for_downstream"),
              "error": response.json().get("error")}
    run_id = response.json().get("generation_run_id") or (record["error"] or {}).get("details", {}).get("generation_run_id")
    if run_id:
        with Session(get_engine()) as session:
            run = session.get(GenerationRun, run_id)
            record.update({"generation_run_id": run_id, "provider_attempt_count": run.attempt_count,
                           "prompt_version": run.prompt_version, "prompt_bundle_hash": run.prompt_hash,
                           "total_latency_seconds": (run.completed_at - run.created_at).total_seconds()})
    save_result(record)
    assert response.status_code == 201, response.text
