"""Canonical Landscape persistence against an explicitly isolated migrated database."""

import os

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.persistence.database import get_engine
from app.persistence.models import Claim, ClaimEvidence, Evidence, GenerationRun, LandscapeItem, ResearchLandscape
from app.services.scientific import LANDSCAPE_SCHEMA_VERSION
from test_m3_database import live_m3
from test_m3_semantic_database import two_claims


pytestmark = pytest.mark.skipif(os.getenv("RUN_DB_INTEGRATION") != "1", reason="Requires isolated PostgreSQL")


def proposal(claims, evidence, text="The synthetic sources specify schedules; measured effects remain unknown."):
    return {"summary": "These are synthetic planned schedules, without observed results.",
            "uncertainty": "Effects are unresolved.", "items": [{"category": "method", "text": text,
            "uncertainty": "This is a planned protocol only.", "claim_ids": claims, "evidence_ids": evidence}]}


def test_canonical_passages_persist_pending_review_with_real_source_relationships(live_m3):
    client, provider, _ = live_m3
    passages = ("虚构方案 A：连续搅拌。\n  不含测量结果。", "虚构方案 B：名义 60 rpm；五次 30 秒。\n边界条件未规定。")
    project, claims, evidence = two_claims(live_m3, *passages)
    provider.responses = [proposal(claims, evidence)]
    response = client.post(f"/api/v1/projects/{project}/landscapes/generate", json={
        "claim_ids": claims, "expected_question_revision": 1})
    assert response.status_code == 201, response.text
    landscape = response.json()["landscape"]
    assert landscape["review_status"] == "pending_review" and not landscape["eligible_for_downstream"]
    item = landscape["items"][0]
    assessments = [item["ai_conflict_assessment"], landscape["ai_summary_assessments"][-1]]
    with Session(get_engine()) as session:
        assert session.get(GenerationRun, response.json()["generation_run_id"]).schema_version == LANDSCAPE_SCHEMA_VERSION
        assert session.get(LandscapeItem, item["id"]).ai_conflict_assessment == assessments[0]
        for assessment in assessments:
            assert assessment["source_binding_origin"] == "server_bound_canonical_source_text"
            excerpts = {quoted["evidence_id"]: quoted for quoted in assessment["support_excerpts"]}
            assert set(excerpts) == set(evidence)
            for evidence_id, text in zip(evidence, passages):
                stored = session.get(Evidence, evidence_id)
                assert excerpts[evidence_id]["excerpt"] == text == stored.source_text
                assert stored.paper_chunk_id and stored.chunk.paper.project_id == project
                assert stored.chunk.page_number in (2, 3)


@pytest.mark.parametrize("verdict", ["unsupported", "insufficient_evidence"])
def test_authentic_sources_cannot_override_unsupported_item_verdict(live_m3, verdict):
    client, provider, _ = live_m3
    project, claims, evidence = two_claims(live_m3,
        "Synthetic method A is continuous; no observed data.", "Synthetic method B is intermittent; no observed data.")
    provider.responses = [proposal(claims, evidence, "A is significantly superior to B."),
                          proposal(claims, evidence, "A causally improves cooling versus B.")]
    assessment = {"verdict": "not_conflict", "asserts_direct_conflict": False,
        "dimensions": {key: "unknown" for key in ("dataset", "population", "task", "metric", "experimental_condition", "baseline")},
        "support_verdict": verdict, "flagged_statements": ["No observed data support an effect."],
        "qualifications": ["Only fictional schedules are described."], "evidence_refs": evidence,
        "justification": "Authentic references do not establish measured effects."}
    provider.verification_responses = [assessment, assessment]
    response = client.post(f"/api/v1/projects/{project}/landscapes/generate", json={
        "claim_ids": claims, "expected_question_revision": 1})
    assert response.status_code == 502, response.text
    with Session(get_engine()) as session:
        assert not list(session.scalars(select(ResearchLandscape).where(ResearchLandscape.project_id == project)))
        run = session.get(GenerationRun, response.json()["error"]["details"]["generation_run_id"])
        assert run.status == "failed" and run.attempt_count == 4


def test_legacy_assessment_json_remains_readable_without_rebinding(live_m3):
    client, provider, _ = live_m3
    project, claims, evidence = two_claims(live_m3, "Synthetic schedule A.", "Synthetic schedule B.")
    provider.responses = [proposal(claims, evidence)]
    response = client.post(f"/api/v1/projects/{project}/landscapes/generate", json={
        "claim_ids": claims, "expected_question_revision": 1})
    assert response.status_code == 201, response.text
    landscape = response.json()["landscape"]
    # Simulate a historical partial quotation in an isolated fixture only.
    legacy = {"verdict": "not_conflict", "evidence_refs": [evidence[0]],
              "support_excerpts": [{"evidence_id": evidence[0], "excerpt": "Synthetic"}],
              "justification": "Historical diagnostic"}
    with Session(get_engine()) as session:
        session.get(LandscapeItem, landscape["items"][0]["id"]).ai_conflict_assessment = legacy
        session.get(ResearchLandscape, landscape["id"]).ai_summary_assessments = [legacy]
        session.commit()
    fetched = client.get(f"/api/v1/projects/{project}/landscapes/{landscape['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["items"][0]["ai_conflict_assessment"] == legacy
    assert fetched.json()["ai_summary_assessments"] == [legacy]


@pytest.mark.parametrize("change", ["source_text", "paper_identity", "evidence_roles", "claim_type", "claim_uncertainty"])
def test_midflight_canonical_source_change_prevents_stale_binding(live_m3, change):
    client, provider, _ = live_m3
    project, claims, evidence = two_claims(live_m3, "Synthetic schedule A.", "Synthetic schedule B.")
    if change == "evidence_roles":
        with Session(get_engine()) as session:
            session.add(ClaimEvidence(claim_id=claims[0], evidence_id=evidence[1], role="challenges"))
            session.commit()
    provider.responses = [proposal(claims, evidence)]

    def mutate_isolated_source():
        if "accepted_items" not in provider.contexts[-1]:
            return
        with Session(get_engine()) as session:
            first = session.get(Evidence, evidence[0])
            if change == "source_text":
                first.source_text += " Updated protocol."
                first.chunk.text = first.source_text
            elif change == "paper_identity":
                # Both Papers remain in this Project; IDs and text alone must not conceal drift.
                first.chunk.paper_id = session.get(Evidence, evidence[1]).chunk.paper_id
            elif change == "evidence_roles":
                for link in session.scalars(select(ClaimEvidence).where(ClaimEvidence.claim_id == claims[0])):
                    link.role = "challenges" if link.role == "supports" else "supports"
            elif change == "claim_type":
                session.get(Claim, claims[0]).claim_type = "method"
            else:
                session.get(Claim, claims[0]).uncertainty += " Updated qualification."
            session.commit()

    provider.on_call = mutate_isolated_source
    response = client.post(f"/api/v1/projects/{project}/landscapes/generate", json={
        "claim_ids": claims, "expected_question_revision": 1})
    assert response.status_code == 409, response.text
    with Session(get_engine()) as session:
        assert not list(session.scalars(select(ResearchLandscape).where(ResearchLandscape.project_id == project)))
        run = session.get(GenerationRun, response.json()["error"]["details"]["generation_run_id"])
        assert run.status == "failed" and run.attempt_count == 3
