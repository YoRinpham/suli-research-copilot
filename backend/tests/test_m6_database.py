"""Full M6 report authority and physical-source behavior on migrated PostgreSQL."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from test_m5_database import m5_case  # reuse complete source-linked M4/M5 fixture
from test_m4_database import m4_case

from app.persistence.database import get_engine
from app.persistence.models import (
    AnalysisRun, AnalysisSpec, ExperimentPlan, FigureArtifact, GenerationRun, Project, ResearchReport,
    ResearchLandscape, LandscapeItem, ResultInterpretation, StatisticalResult,
)
from app.persistence.models import now_utc
from app.services.m6_report import M6ReportService
from app.config.settings import get_settings
from app.providers.interfaces import LLMResult
from app.providers.llm import DashScopeLLMProvider, LLMTimeout
from app.services.m6_schemas import ReportNarrative
from app.services.m5_storage import M5Storage


pytestmark = pytest.mark.skipif(os.getenv("RUN_DB_INTEGRATION") != "1", reason="Requires migrated PostgreSQL")


class FakeReport:
    def __init__(self):
        self.mode = "valid"
        self.on_call = None
        self.calls = 0

    def generate_structured(self, prompt, output_schema, context):
        assert output_schema is ReportNarrative
        self.calls += 1
        if self.on_call:
            callback, self.on_call = self.on_call, None
            callback()
        source = "CLAIM_1"
        text = "Within the supplied literature, the comparison remains uncertain."
        if self.mode == "fake_source":
            source = "EVIDENCE_999"
        elif self.mode == "numeric_mutation":
            text = "The stored p = 0.0032 proves the hypothesis."
        elif self.mode == "global_novelty":
            text = "This is the first study ever."
        elif self.mode == "hypothesis_proof":
            text = "The hypothesis is proven."
        elif self.mode == "causal":
            text = "The observed association causes the outcome."
        elif self.mode == "fake_paper_prose":
            text = "PAPER_FAKE reports a benefit."
        elif self.mode == "fake_claim_prose":
            text = "CLAIM_FAKE establishes an effect."
        elif self.mode == "absolute_null":
            source, text = "INTERPRETATION_1", "There is no effect in this population."
        elif self.mode == "cautious_null":
            source, text = "INTERPRETATION_1", "The analysis did not provide sufficient evidence of an effect under this design."
        return LLMResult(output=ReportNarrative.model_validate({"blocks": [
            {"section": "executive_summary", "text": text, "source_refs": [source]}]}),
            model_name="qwen3.8-max", attempt_count=1)


@pytest.fixture
def m6_case(m5_case):
    client, _fake_m5, project_id, plan_id, _hypothesis_id, _gap_id, evidence_id, paper_id, root = m5_case
    upload = client.post(f"{root}/experiment-plans/{plan_id}/datasets?expected_question_revision=1",
        files={"file": ("synthetic.csv", b"group,score\nA,1\nA,2\nB,3\nB,4\n", "text/csv")})
    assert upload.status_code == 201, upload.text
    version_id = upload.json()["versions"][0]["id"]
    proposed = client.post(f"{root}/dataset-versions/{version_id}/analysis-specs", json={
        "expected_question_revision": 1, "method": "welch_t",
        "column_mapping": {"outcome": "score", "group": "group"},
        "parameters": {"groups": ["A", "B"], "missing_policy": "reject"},
        "rationale": "Synthetic planned comparison"})
    assert proposed.status_code == 201, proposed.text
    spec_id = proposed.json()["id"]
    assert client.patch(f"{root}/analysis-specs/{spec_id}/review", json={
        "review_status": "accepted", "expected_question_revision": 1}).status_code == 200
    executed = client.post(f"{root}/analysis-specs/{spec_id}/execute", json={"expected_question_revision": 1})
    assert executed.status_code == 201, executed.text
    run_id = executed.json()["id"]
    result_id = executed.json()["result"]["id"]
    figure = client.post(f"{root}/dataset-versions/{version_id}/figures", json={
        "expected_question_revision": 1, "chart_type": "histogram", "x_column": "score",
        "title": "Synthetic scores", "analysis_run_id": run_id,
        "statistical_result_id": result_id})
    assert figure.status_code == 201, figure.text
    figure_id = figure.json()["id"]
    generated = client.post(f"{root}/analysis-runs/{run_id}/interpretations/generate",
        json={"expected_question_revision": 1})
    assert generated.status_code == 201, generated.text
    interpretation_id = generated.json()["interpretation"]["id"]
    accepted = client.patch(f"{root}/result-interpretations/{interpretation_id}/review", json={
        "review_status": "accepted", "expected_question_revision": 1})
    assert accepted.status_code == 200, accepted.text
    fake = FakeReport()
    client.app.state.llm_provider = fake
    try:
        yield client, fake, project_id, root, interpretation_id, result_id, figure_id, evidence_id, paper_id
    finally:
        with Session(get_engine()) as session:
            session.execute(delete(ResearchReport).where(ResearchReport.project_id == project_id))
            session.commit()


def test_permanent_figure_deletion_invalidates_accepted_outputs_without_recomputing(m6_case):
    client, _, project_id, root, interpretation_id, result_id, figure_id, *_ = m6_case
    generated = client.post(f"{root}/reports/generate", json={
        "interpretation_id": interpretation_id, "expected_question_revision": 1})
    assert generated.status_code == 201
    report_id = generated.json()["report"]["id"]
    assert client.patch(f"{root}/reports/{report_id}/review", json={
        "review_status": "accepted", "expected_question_revision": 1}).status_code == 200
    before = client.get(f"{root}/statistical-results/{result_id}").json()
    with Session(get_engine()) as session:
        artifact = session.get(FigureArtifact, figure_id)
        stored_file = M5Storage(get_settings().m5_storage_root)._path(artifact.storage_key)
    assert stored_file.exists()
    assert client.post(f"/api/v1/projects/foreign/figures/{figure_id}/delete",
        json={"expected_question_revision": 1}).status_code == 404
    assert client.post(f"{root}/figures/{figure_id}/delete",
        json={"expected_question_revision": 2}).status_code == 409
    assert stored_file.exists()
    deleted = client.post(f"{root}/figures/{figure_id}/delete", json={"expected_question_revision": 1})
    assert deleted.status_code == 200 and deleted.json()["deleted_figure_id"] == figure_id
    assert not stored_file.exists()
    assert client.get(f"{root}/figures/{figure_id}").status_code == 404
    assert client.get(f"{root}/figures/{figure_id}/file").status_code == 404
    assert not client.get(f"{root}/result-interpretations/{interpretation_id}").json()["eligible_for_future_report"]
    assert not client.get(f"{root}/reports/{report_id}").json()["current_source_bundle"]
    assert client.get(f"{root}/reports/{report_id}/export").status_code == 409
    assert client.get(f"{root}/statistical-results/{result_id}").json() == before
    assert len(client.get(f"{root}/analysis-runs").json()) == 1


def test_report_full_lineage_review_revision_export(m6_case):
    client, fake, project_id, root, interpretation_id, result_id, figure_id, evidence_id, paper_id = m6_case
    response = client.post(f"{root}/reports/generate", json={
        "interpretation_id": interpretation_id, "expected_question_revision": 1})
    assert response.status_code == 201, response.text
    report = response.json()["report"]
    assert report["review_status"] == "pending_review" and report["current_source_bundle"]
    assert report["source_bundle"]["results"][0]["id"] == result_id
    assert report["source_bundle"]["figures"][0]["id"] == figure_id
    assert report["source_bundle"]["plan"]["id"]
    assert "experimental_groups" in report["source_bundle"]["plan"]
    assert "control_variables" in report["source_bundle"]["plan"]
    assert "expected_observations" in report["source_bundle"]["plan"]
    assert report["source_bundle"]["hypothesis"]["id"]
    assert report["source_bundle"]["gap"]["id"]
    assert report["source_bundle"]["landscape"]["id"]
    assert report["source_bundle"]["claims"][0]["id"]
    assert report["source_bundle"]["datasets"][0]["version_id"]
    assert report["source_bundle"]["analyses"][0]["spec_id"]
    assert report["source_bundle"]["figures"][0]["visualization_spec_id"]
    assert any(row["evidence_id"] == evidence_id and row["page_number"] == 2
               for row in report["source_bundle"]["citations"])
    assert any(row["paper_chunk_id"] and row["paper_id"] == paper_id
               for row in report["source_bundle"]["citations"])
    assert client.get(f"{root}/reports/{report['id']}/export").status_code == 409
    draft = client.get(f"{root}/reports/{report['id']}/export?format=markdown&draft=true")
    assert "DRAFT / NOT HUMAN ACCEPTED" in draft.text
    assert str(report["source_bundle"]["results"][0]["values"]["p_value"]) in draft.text
    assert f"papers/{paper_id}/file#page=2" in draft.text
    assert client.get(f"/api/v1/projects/foreign/reports/{report['id']}").status_code == 404
    reviewed = client.patch(f"{root}/reports/{report['id']}/review", json={
        "review_status": "accepted", "expected_question_revision": 1})
    assert reviewed.status_code == 200 and reviewed.json()["final_export_eligible"]
    assert "HUMAN ACCEPTED" in client.get(f"{root}/reports/{report['id']}/export?format=html").text
    revised = client.post(f"{root}/reports/{report['id']}/revisions", json={
        "expected_question_revision": 1, "title": "Revised synthetic report",
        "blocks": [{"section": "executive_summary", "text": "The supplied evidence remains limited.",
                    "source_refs": ["CLAIM_1"]}]})
    assert revised.status_code == 201, revised.text
    new = revised.json()
    assert new["origin"] == "human_revision" and new["supersedes_id"] == report["id"]
    assert new["review_status"] == "pending_review" and not new["final_export_eligible"]
    assert client.get(f"{root}/reports/{report['id']}").json()["current_source_bundle"] is False


@pytest.mark.parametrize("mode", ["fake_source", "numeric_mutation", "global_novelty", "hypothesis_proof", "causal",
                                  "fake_paper_prose", "fake_claim_prose", "absolute_null"])
def test_report_adversarial_output_fails_closed(m6_case, mode):
    client, fake, project_id, root, interpretation_id, *_ = m6_case
    if mode == "absolute_null":
        with Session(get_engine()) as session:
            session.get(ResultInterpretation, interpretation_id).summary = "The analysis is inconclusive."
            session.commit()
    fake.mode = mode
    response = client.post(f"{root}/reports/generate", json={
        "interpretation_id": interpretation_id, "expected_question_revision": 1})
    assert response.status_code == 502, response.text
    with Session(get_engine()) as session:
        assert not session.scalar(select(ResearchReport.id).where(ResearchReport.project_id == project_id))
        assert session.scalar(select(GenerationRun).where(GenerationRun.project_id == project_id,
            GenerationRun.stage == "research_report").order_by(GenerationRun.created_at.desc())).status == "failed"


def test_cautious_report_matches_accepted_inconclusive_interpretation(m6_case):
    client, fake, _project_id, root, interpretation_id, *_ = m6_case
    with Session(get_engine()) as session:
        session.get(ResultInterpretation, interpretation_id).summary = "The analysis is inconclusive."
        session.commit()
    fake.mode = "cautious_null"
    response = client.post(f"{root}/reports/generate", json={
        "interpretation_id": interpretation_id, "expected_question_revision": 1})
    assert response.status_code == 201, response.text
    assert response.json()["report"]["narrative_blocks"][0]["source_refs"] == ["INTERPRETATION_1"]


def test_report_revision_cannot_attach_valid_refs_to_fake_visible_source(m6_case):
    client, _fake, _project_id, root, interpretation_id, *_ = m6_case
    created = client.post(f"{root}/reports/generate", json={
        "interpretation_id": interpretation_id, "expected_question_revision": 1})
    assert created.status_code == 201, created.text
    report_id = created.json()["report"]["id"]
    for text in ("PAPER_FAKE reports a benefit.", "CLAIM_FAKE establishes an effect."):
        response = client.post(f"{root}/reports/{report_id}/revisions", json={
            "expected_question_revision": 1, "title": "Invalid revision",
            "blocks": [{"section": "conclusion", "text": text, "source_refs": ["CLAIM_1"]}]})
        assert response.status_code == 422, response.text


def test_cross_project_report_mutations_and_exports_are_not_found(m6_case):
    client, _fake, _project_id, root, interpretation_id, *_ = m6_case
    report = client.post(f"{root}/reports/generate", json={
        "interpretation_id": interpretation_id, "expected_question_revision": 1}).json()["report"]
    with Session(get_engine()) as session:
        foreign = Project(title="Foreign project", research_question="Unrelated question?")
        session.add(foreign)
        session.commit()
        foreign_id = foreign.id
    other = f"/api/v1/projects/{foreign_id}/reports/{report['id']}"
    try:
        assert client.patch(other + "/review", json={"review_status": "accepted",
            "expected_question_revision": 1}).status_code == 404
        assert client.post(other + "/revisions", json={"expected_question_revision": 1,
            "title": "Wrong project", "blocks": report["narrative_blocks"]}).status_code == 404
        for fmt in ("markdown", "html"):
            assert client.get(other + f"/export?format={fmt}&draft=true").status_code == 404
    finally:
        with Session(get_engine()) as session:
            session.delete(session.get(Project, foreign_id))
            session.commit()


def test_retry_during_active_report_generation_is_rejected(m6_case):
    client, fake, project_id, root, interpretation_id, *_ = m6_case
    retry_status = []
    fake.on_call = lambda: retry_status.append(client.post(f"{root}/reports/generate", json={
        "interpretation_id": interpretation_id, "expected_question_revision": 1}).status_code)
    first = client.post(f"{root}/reports/generate", json={
        "interpretation_id": interpretation_id, "expected_question_revision": 1})
    assert first.status_code == 201, first.text
    assert retry_status == [409]
    with Session(get_engine()) as session:
        assert len(list(session.scalars(select(ResearchReport).where(ResearchReport.project_id == project_id)))) == 1


def test_report_retry_after_browser_timeout_still_sees_active_generation(m6_case):
    client, fake, project_id, root, interpretation_id, *_ = m6_case
    retry_status = []

    def retry_after_client_timeout():
        fake.on_call = None
        with Session(get_engine()) as session:
            active = session.scalar(select(GenerationRun).where(
                GenerationRun.project_id == project_id, GenerationRun.stage == "research_report",
                GenerationRun.status == "started"))
            active.created_at = now_utc() - timedelta(seconds=421)
            session.commit()
        retry_status.append(client.post(f"{root}/reports/generate", json={
            "interpretation_id": interpretation_id, "expected_question_revision": 1}).status_code)

    fake.on_call = retry_after_client_timeout
    first = client.post(f"{root}/reports/generate", json={
        "interpretation_id": interpretation_id, "expected_question_revision": 1})
    assert first.status_code == 201, first.text
    assert retry_status == [409]
    with Session(get_engine()) as session:
        assert len(list(session.scalars(select(ResearchReport).where(
            ResearchReport.project_id == project_id)))) == 1


def test_report_stale_generation_can_be_recovered(m6_case):
    client, fake, project_id, root, interpretation_id, *_ = m6_case
    retry_status = []

    def retry_after_stale_threshold():
        fake.on_call = None
        with Session(get_engine()) as session:
            active = session.scalar(select(GenerationRun).where(
                GenerationRun.project_id == project_id, GenerationRun.stage == "research_report",
                GenerationRun.status == "started"))
            active.created_at = now_utc() - timedelta(seconds=451)
            session.commit()
        retry_status.append(client.post(f"{root}/reports/generate", json={
            "interpretation_id": interpretation_id, "expected_question_revision": 1}).status_code)
        # The old worker is abandoned; only the recovered request may finish.
        raise RuntimeError("simulated abandoned report worker")

    fake.on_call = retry_after_stale_threshold
    old_request = client.post(f"{root}/reports/generate", json={
        "interpretation_id": interpretation_id, "expected_question_revision": 1})
    assert old_request.status_code == 502
    assert retry_status == [201]
    with Session(get_engine()) as session:
        assert len(list(session.scalars(select(ResearchReport).where(
            ResearchReport.project_id == project_id)))) == 1


def test_late_old_worker_cannot_persist_after_stale_recovery(m6_case):
    client, fake, project_id, root, interpretation_id, *_ = m6_case
    blocked, release = Event(), Event()

    def pause_old_worker():
        blocked.set()
        assert release.wait(10)

    fake.on_call = pause_old_worker
    url = f"{root}/reports/generate"
    body = {"interpretation_id": interpretation_id, "expected_question_revision": 1}
    with ThreadPoolExecutor(max_workers=2) as pool:
        old_future = pool.submit(client.post, url, json=body)
        assert blocked.wait(10)
        with Session(get_engine()) as session:
            old_run = session.scalar(select(GenerationRun).where(
                GenerationRun.project_id == project_id, GenerationRun.stage == "research_report",
                GenerationRun.status == "started"))
            old_id = old_run.id
            old_run.created_at = now_utc() - timedelta(seconds=451)
            session.commit()
        new_response = pool.submit(client.post, url, json=body).result(timeout=10)
        assert new_response.status_code == 201, new_response.text
        release.set()
        old_response = old_future.result(timeout=10)
    assert old_response.status_code == 409
    with Session(get_engine()) as session:
        runs = list(session.scalars(select(GenerationRun).where(
            GenerationRun.project_id == project_id, GenerationRun.stage == "research_report")))
        reports = list(session.scalars(select(ResearchReport).where(ResearchReport.project_id == project_id)))
        assert session.get(GenerationRun, old_id).status == "failed"
        assert len([run for run in runs if run.status == "succeeded"]) == 1
        assert len(reports) == 1 and reports[0].generation_run_id != old_id


def test_old_final_commit_wins_before_stale_recovery(m6_case, monkeypatch):
    client, fake, project_id, root, interpretation_id, *_ = m6_case
    final_locked, retry_entered, release = Event(), Event(), Event()
    original = M6ReportService._report_input
    original_project = M6ReportService._project
    paused = False

    def hold_old_final_transaction(self, session, *args):
        nonlocal paused
        result = original(self, session, *args)
        if not paused:
            paused = True
            final_locked.set()
            assert release.wait(10)
        return result

    def observe_retry_lock(session, project_id, revision=None, *, lock=False):
        if lock and final_locked.is_set():
            retry_entered.set()
        return original_project(session, project_id, revision, lock=lock)

    url = f"{root}/reports/generate"
    body = {"interpretation_id": interpretation_id, "expected_question_revision": 1}
    # Hold the provider first so the originating Run can be aged before its final commit.
    provider_blocked, provider_release = Event(), Event()
    fake.on_call = lambda: (provider_blocked.set(), provider_release.wait(10))
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="old-report") as pool:
        old_future = pool.submit(client.post, url, json=body)
        assert provider_blocked.wait(10)
        with Session(get_engine()) as session:
            old_run = session.scalar(select(GenerationRun).where(
                GenerationRun.project_id == project_id, GenerationRun.stage == "research_report",
                GenerationRun.status == "started"))
            old_id = old_run.id
            old_run.created_at = now_utc() - timedelta(seconds=451)
            session.commit()
        monkeypatch.setattr(M6ReportService, "_report_input", hold_old_final_transaction)
        provider_release.set()
        assert final_locked.wait(10)
        monkeypatch.setattr(M6ReportService, "_project", staticmethod(observe_retry_lock))
        retry_future = pool.submit(client.post, url, json=body)
        assert retry_entered.wait(10)
        release.set()
        assert old_future.result(timeout=10).status_code == 201
        assert retry_future.result(timeout=10).status_code == 409
    with Session(get_engine()) as session:
        assert session.get(GenerationRun, old_id).status == "succeeded"
        assert session.scalar(select(ResearchReport.id).where(ResearchReport.project_id == project_id))
        assert len(list(session.scalars(select(GenerationRun).where(
            GenerationRun.project_id == project_id, GenerationRun.stage == "research_report")))) == 1


def test_expired_report_deadline_rejects_late_result_without_replacement(m6_case, monkeypatch):
    client, fake, project_id, root, interpretation_id, *_ = m6_case
    from app.services import m5_ai
    fake.on_call = lambda: monkeypatch.setattr(m5_ai, "monotonic", lambda: float("inf"))
    response = client.post(f"{root}/reports/generate", json={
        "interpretation_id": interpretation_id, "expected_question_revision": 1})
    assert response.status_code == 504, response.text
    with Session(get_engine()) as session:
        run = session.scalar(select(GenerationRun).where(GenerationRun.project_id == project_id,
            GenerationRun.stage == "research_report"))
        assert run.status == "failed" and run.error_type == "report_deadline_expired"
        assert session.scalar(select(ResearchReport.id).where(ResearchReport.project_id == project_id)) is None


def test_provider_total_timeout_fails_run_without_partial_report(m6_case):
    client, _fake, project_id, root, interpretation_id, *_ = m6_case

    class TimedOutProvider:
        def generate_structured(self, *_args):
            raise LLMTimeout("Qwen request timed out", 1, category="provider_total_timeout")

    client.app.state.llm_provider = TimedOutProvider()
    response = client.post(f"{root}/reports/generate", json={
        "interpretation_id": interpretation_id, "expected_question_revision": 1})
    assert response.status_code == 504, response.text
    with Session(get_engine()) as session:
        run = session.scalar(select(GenerationRun).where(GenerationRun.project_id == project_id,
            GenerationRun.stage == "research_report"))
        assert run.status == "failed" and run.error_type == "provider_total_timeout"
        assert session.scalar(select(ResearchReport.id).where(ResearchReport.project_id == project_id)) is None


def test_report_hash_ignores_landscape_query_order_but_tracks_material_change(m6_case, monkeypatch):
    client, _fake, project_id, root, interpretation_id, *_ = m6_case
    url = f"{root}/reports/preview?interpretation_id={interpretation_id}&expected_question_revision=1"
    initial = client.get(url)
    assert initial.status_code == 200, initial.text
    original = M6ReportService._hypothesis_context
    def reordered(self, *args):
        from copy import deepcopy
        source = deepcopy(original(self, *args))
        source["context"]["landscape"]["items"].reverse()
        return source
    monkeypatch.setattr(M6ReportService, "_hypothesis_context", reordered)
    reordered_response = client.get(url)
    assert reordered_response.status_code == 200, reordered_response.text
    assert reordered_response.json()["source_bundle_hash"] == initial.json()["source_bundle_hash"]
    monkeypatch.setattr(M6ReportService, "_hypothesis_context", original)
    with Session(get_engine()) as session:
        item = session.scalar(select(LandscapeItem).join(ResearchLandscape).where(
            ResearchLandscape.project_id == project_id))
        assert item is not None
        item.text += " Material source change."
        session.commit()
    changed = client.get(url)
    assert changed.status_code == 200, changed.text
    assert changed.json()["source_bundle_hash"] != initial.json()["source_bundle_hash"]


def test_accepted_plan_structured_fields_appear_in_deterministic_report(m6_case):
    client, _fake, _project_id, root, interpretation_id, *_ = m6_case
    with Session(get_engine()) as session:
        interpretation = session.get(ResultInterpretation, interpretation_id)
        analysis = session.get(AnalysisRun, interpretation.analysis_run_id)
        spec = session.get(AnalysisSpec, analysis.analysis_spec_id)
        plan = session.get(ExperimentPlan, spec.experiment_plan_id)
        plan.control_variables = ["batch"]
        plan.experimental_groups = ["Treatment A", "Control B"]
        plan.expected_observations = ["Observed score may vary"]
        plan.uncertainty = "Pilot sample"
        session.commit()
    preview = client.get(f"{root}/reports/preview?interpretation_id={interpretation_id}&expected_question_revision=1")
    assert preview.status_code == 200, preview.text
    for expected in ("batch", "Treatment A", "Control B", "Observed score may vary", "Pilot sample"):
        assert expected in preview.json()["markdown"]


def test_new_result_during_provider_call_invalidates_report(m6_case):
    client, fake, project_id, root, interpretation_id, result_id, *_ = m6_case
    def add_result():
        with Session(get_engine()) as session:
            previous = session.get(StatisticalResult, result_id)
            session.add(StatisticalResult(project_id=project_id, analysis_run_id=previous.analysis_run_id,
                method=previous.method, values={"synthetic_extra": True}))
            session.commit()
    fake.on_call = add_result
    response = client.post(f"{root}/reports/generate", json={
        "interpretation_id": interpretation_id, "expected_question_revision": 1})
    assert response.status_code == 409, response.text
    with Session(get_engine()) as session:
        assert not session.scalar(select(ResearchReport.id).where(ResearchReport.project_id == project_id))
        assert session.scalar(select(GenerationRun).where(GenerationRun.project_id == project_id,
            GenerationRun.stage == "research_report")).status == "failed"


@pytest.mark.skipif(os.getenv("RUN_M6_REAL_SMOKE") != "1", reason="Opt-in real provider smoke")
def test_m6_real_provider_smoke_on_synthetic_sources(m6_case):
    client, _fake, project_id, root, interpretation_id, *_ = m6_case
    settings = get_settings()
    assert settings.qwen_api_key is not None
    client.app.state.llm_provider = DashScopeLLMProvider(
        settings.qwen_api_key.get_secret_value(), settings.qwen_model,
        timeout_seconds=60, retry_count=0, base_url=settings.qwen_base_url,
    )
    response = client.post(f"{root}/reports/generate", json={
        "interpretation_id": interpretation_id, "expected_question_revision": 1})
    assert response.status_code == 201, response.text
    report = response.json()["report"]
    assert report["source_bundle"]["source_map"]
    assert report["narrative_blocks"]
    assert all(set(block["source_refs"]) <= set(report["source_bundle"]["source_map"])
               for block in report["narrative_blocks"])
    with Session(get_engine()) as session:
        assert session.scalar(select(GenerationRun).where(
            GenerationRun.project_id == project_id,
            GenerationRun.stage == "research_report")).status == "succeeded"
