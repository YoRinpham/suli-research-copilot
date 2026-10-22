"""M3 human authority boundaries against real PostgreSQL."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.persistence.database import get_engine
from app.persistence.models import Claim, GenerationRun, ResearchLandscape
from test_m3_database import live_m3
from test_m3_semantic_database import two_claims


pytestmark = pytest.mark.skipif(os.getenv("RUN_DB_INTEGRATION") != "1", reason="Requires migrated PostgreSQL")


def _review_claim(client, project_id: str, claim_id: str, status: str = "supported"):
    return client.patch(
        f"/api/v1/projects/{project_id}/claims/{claim_id}/grounding-review",
        json={"human_grounding_status": status, "expected_question_revision": 1},
    )


def test_ai_false_positive_remains_pending_until_explicit_human_review(live_m3):
    client, provider, source = live_m3
    project_id, evidence_id, _, _ = source("X is associated with Y.")
    provider.responses = [{"claims": [{"text": "X causes Y.", "claim_type": "finding",
        "grounding_status": "supported", "confidence": "high", "uncertainty": "none_identified",
        "evidence_for_ids": [evidence_id], "evidence_against_ids": []}]}]
    provider.verification_responses = [{"verdict": "directly_supported", "evidence_refs": [evidence_id],
        "risk_flags": [], "justification": "Incorrect AI assessment"}]
    response = client.post(f"/api/v1/projects/{project_id}/claims/generate", json={
        "evidence_ids": [evidence_id], "expected_question_revision": 1})
    assert response.status_code == 201, response.text
    claim = response.json()["claims"][0]
    assert claim["ai_grounding_assessment"] == "directly_supported"
    assert claim["ai_support_excerpts"] == [{"evidence_id": evidence_id, "excerpt": "X is associated with Y."}]
    assert claim["human_grounding_status"] == "pending_review"
    with Session(get_engine()) as session:
        persisted = session.get(Claim, claim["id"])
        assert persisted.ai_grounding_assessment == "directly_supported"
        assert persisted.ai_support_excerpts == claim["ai_support_excerpts"]
        assert persisted.ai_evidence_refs == [evidence_id]
        assert session.get(GenerationRun, persisted.generation_run_id).model == "qwen3.8-max"
    assert claim["selection_status"] == "candidate" and not claim["eligible_for_generation"]
    selected = client.patch(f"/api/v1/projects/{project_id}/claims/{claim['id']}/selection", json={
        "selection_status": "selected", "expected_question_revision": 1})
    assert selected.status_code == 200 and not selected.json()["eligible_for_generation"]
    blocked = client.post(f"/api/v1/projects/{project_id}/landscapes/generate", json={
        "claim_ids": [claim["id"]], "expected_question_revision": 1})
    assert blocked.status_code == 409
    reviewed = _review_claim(client, project_id, claim["id"])
    assert reviewed.status_code == 200 and reviewed.json()["eligible_for_generation"]


@pytest.mark.parametrize("judgment,eligible", [
    ("supported", True), ("inferred", True), ("unsupported", False),
    ("insufficient_evidence", False),
])
def test_human_grounding_decides_landscape_eligibility(live_m3, judgment, eligible):
    client, provider, source = live_m3
    project_id, evidence_id, _, _ = source()
    provider.responses = [{"claims": [{"text": "Method A improved accuracy on Dataset X.",
        "claim_type": "finding", "grounding_status": "supported", "confidence": "medium",
        "uncertainty": "Small sample", "evidence_for_ids": [evidence_id], "evidence_against_ids": []}]}]
    claim = client.post(f"/api/v1/projects/{project_id}/claims/generate", json={
        "evidence_ids": [evidence_id], "expected_question_revision": 1}).json()["claims"][0]
    selected = client.patch(f"/api/v1/projects/{project_id}/claims/{claim['id']}/selection", json={
        "selection_status": "selected", "expected_question_revision": 1})
    assert selected.status_code == 200 and not selected.json()["eligible_for_generation"]
    reviewed = _review_claim(client, project_id, claim["id"], judgment)
    assert reviewed.status_code == 200 and reviewed.json()["eligible_for_generation"] is eligible


def test_human_revision_needs_its_own_grounding_review(live_m3):
    client, provider, source = live_m3
    project_id, evidence_id, _, _ = source("X was associated with Y.")
    provider.responses = [{"claims": [{"text": "X was associated with Y.", "claim_type": "finding",
        "grounding_status": "supported", "confidence": "medium", "uncertainty": "Single report",
        "evidence_for_ids": [evidence_id], "evidence_against_ids": []}]}]
    original = client.post(f"/api/v1/projects/{project_id}/claims/generate", json={
        "evidence_ids": [evidence_id], "expected_question_revision": 1}).json()["claims"][0]
    forged_review = client.post(f"/api/v1/projects/{project_id}/claims/{original['id']}/revisions", json={
        "text": "X causes Y.", "claim_type": "finding", "grounding_status": "supported",
        "confidence": "high", "uncertainty": "Human interpretation",
        "evidence_for_ids": [evidence_id], "evidence_against_ids": [], "expected_question_revision": 1})
    assert forged_review.status_code == 422
    revised = client.post(f"/api/v1/projects/{project_id}/claims/{original['id']}/revisions", json={
        "text": "X causes Y.", "claim_type": "finding", "confidence": "high",
        "uncertainty": "Human interpretation", "evidence_for_ids": [evidence_id],
        "evidence_against_ids": [], "expected_question_revision": 1})
    assert revised.status_code == 201, revised.text
    claim = revised.json()
    assert claim["origin"] == "human_revision" and claim["generation_run_id"] is None
    assert claim["ai_grounding_assessment"] is None and claim["human_grounding_status"] == "pending_review"
    assert claim["selection_status"] == "candidate"
    selected = client.patch(f"/api/v1/projects/{project_id}/claims/{claim['id']}/selection", json={
        "selection_status": "selected", "expected_question_revision": 1})
    assert selected.status_code == 200 and not selected.json()["eligible_for_generation"]
    assert _review_claim(client, project_id, claim["id"]).json()["eligible_for_generation"]


def test_grounding_review_is_project_scoped_and_selection_is_separate(live_m3):
    client, provider, source = live_m3
    project_id, evidence_id, _, _ = source()
    other_project_id, _, _, _ = source()
    provider.responses = [{"claims": [{"text": "Method A improved accuracy on Dataset X.",
        "claim_type": "finding", "grounding_status": "supported", "confidence": "medium",
        "uncertainty": "Single report", "evidence_for_ids": [evidence_id], "evidence_against_ids": []}]}]
    claim = client.post(f"/api/v1/projects/{project_id}/claims/generate", json={
        "evidence_ids": [evidence_id], "expected_question_revision": 1}).json()["claims"][0]
    assert _review_claim(client, other_project_id, claim["id"]).status_code == 404
    reviewed = _review_claim(client, project_id, claim["id"])
    assert reviewed.status_code == 200
    assert reviewed.json()["selection_status"] == "candidate"
    assert not reviewed.json()["eligible_for_generation"]
    assert reviewed.json()["human_reviewed_at"] is not None


def test_human_grounding_change_during_landscape_generation_prevents_commit(live_m3):
    client, provider, _ = live_m3
    project_id, claims, evidence = two_claims(live_m3, "A scored 80% on Dataset X.", "A scored 60% on Dataset X.")
    provider.contexts.clear()
    provider.responses = [{"summary": "Two results differ.", "uncertainty": "Small corpus",
        "items": [{"category": "finding", "text": "Two results were reported.",
                   "uncertainty": "Small corpus", "claim_ids": claims, "evidence_ids": evidence}]}]
    entered, release = Event(), Event()

    def hold():
        if len(provider.contexts) == 1:
            entered.set()
            assert release.wait(10)

    provider.on_call = hold
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.post, f"/api/v1/projects/{project_id}/landscapes/generate", json={
            "claim_ids": claims, "expected_question_revision": 1})
        assert entered.wait(10)
        try:
            changed = _review_claim(client, project_id, claims[0], "unsupported")
            assert changed.status_code == 200 and not changed.json()["eligible_for_generation"]
        finally:
            release.set()
        response = future.result(timeout=10)
    assert response.status_code == 409
    with Session(get_engine()) as session:
        assert not list(session.scalars(select(ResearchLandscape).where(ResearchLandscape.project_id == project_id)))


def test_summary_ai_false_positive_remains_pending_human_acceptance(live_m3):
    client, provider, _ = live_m3
    project_id, claims, evidence = two_claims(live_m3, "A beat baseline B by 10%.", "A beat baseline C by 5%.")
    for claim_id in claims:
        assert _review_claim(client, project_id, claim_id).status_code == 200
    provider.responses = [{"summary": "There is definitively no conflict in the field.",
        "uncertainty": "none_identified", "items": [{"category": "finding",
        "text": "The reports used different baselines.", "uncertainty": "Comparison is limited.",
        "claim_ids": claims, "evidence_ids": evidence}]}]
    provider.verification_responses = [{"verdict": "supported", "flagged_statements": [],
        "evidence_refs": evidence, "qualifications": [], "justification": "Incorrect AI assessment"}]
    response = client.post(f"/api/v1/projects/{project_id}/landscapes/generate", json={
        "claim_ids": claims, "expected_question_revision": 1})
    assert response.status_code == 201, response.text
    landscape = response.json()["landscape"]
    assert landscape["review_status"] == "pending_review" and not landscape["eligible_for_downstream"]
    assert landscape["ai_summary_assessments"][-1]["verdict"] == "supported"
    with Session(get_engine()) as session:
        persisted = session.get(ResearchLandscape, landscape["id"])
        assert persisted.review_status == "pending_review"
        assert persisted.ai_summary_assessments[-1]["verdict"] == "supported"
    other_project_id, _, _, _ = live_m3[2]()
    assert client.patch(f"/api/v1/projects/{other_project_id}/landscapes/{landscape['id']}/review", json={
        "review_status": "accepted", "expected_question_revision": 1}).status_code == 404
    accepted = client.patch(f"/api/v1/projects/{project_id}/landscapes/{landscape['id']}/review", json={
        "review_status": "accepted", "expected_question_revision": 1})
    assert accepted.status_code == 200 and accepted.json()["eligible_for_downstream"]
    rejected = client.patch(f"/api/v1/projects/{project_id}/landscapes/{landscape['id']}/review", json={
        "review_status": "rejected", "expected_question_revision": 1})
    assert rejected.status_code == 200 and not rejected.json()["eligible_for_downstream"]


@pytest.mark.parametrize("repair_category", ["conflict", "finding"])
def test_false_conflict_cannot_survive_unknown_dimensions_or_category_change(live_m3, repair_category):
    client, provider, _ = live_m3
    project_id, claims, evidence = two_claims(live_m3,
        "A scored 80% accuracy on Dataset X.", "A scored 60% accuracy on Dataset Y.")
    for claim_id in claims:
        assert _review_claim(client, project_id, claim_id).status_code == 200
    conflict = {"category": "conflict", "text": "These studies directly contradict one another.",
        "uncertainty": "none_identified", "claim_ids": claims, "evidence_ids": evidence}
    provider.responses = [
        {"summary": "The reports differ.", "uncertainty": "Different datasets.", "items": [conflict]},
        {"summary": "The reports differ.", "uncertainty": "Different datasets.",
         "items": [{**conflict, "category": repair_category,
                    "text": "These studies contradict one another." if repair_category == "finding" else conflict["text"]}]},
    ]
    assessment = (
        {"verdict": "direct_conflict" if repair_category == "conflict" else "incomparable",
         "asserts_direct_conflict": True,
         "dimensions": {key: "unknown" if repair_category == "conflict" else
                        ("different" if key == "dataset" else "unknown") for key in
                        ("dataset", "population", "task", "metric", "experimental_condition", "baseline")},
         "evidence_refs": evidence, "support_verdict": "supported", "flagged_statements": [],
         "qualifications": [], "justification": "Invalid conflict"}
    )
    provider.verification_responses = [assessment, {**assessment, "asserts_direct_conflict": False}]
    response = client.post(f"/api/v1/projects/{project_id}/landscapes/generate", json={
        "claim_ids": claims, "expected_question_revision": 1})
    assert response.status_code == 502, response.text
    with Session(get_engine()) as session:
        assert not list(session.scalars(select(ResearchLandscape).where(ResearchLandscape.project_id == project_id)))


def test_single_claim_finding_is_not_a_cross_claim_conflict_when_ai_role_is_wrong(live_m3):
    client, provider, _ = live_m3
    project_id, claims, evidence = two_claims(live_m3,
        "A beat baseline B by 10% on Dataset X.", "A beat baseline C by 5% on Dataset X.")
    provider.responses = [{"summary": "Reports used different baselines.", "uncertainty": "Small corpus",
        "items": [{"category": "finding", "text": "A beat baseline B by 10% on Dataset X.",
                   "uncertainty": "Single report", "claim_ids": [claims[0]], "evidence_ids": [evidence[0]]}]}]
    provider.verification_responses = [{"verdict": "direct_conflict", "asserts_direct_conflict": True,
        "dimensions": {key: "comparable" for key in
                       ("dataset", "population", "task", "metric", "experimental_condition", "baseline")},
        "evidence_refs": [evidence[0]], "support_verdict": "supported", "flagged_statements": [],
        "qualifications": [], "justification": "Incorrect AI role judgment"}]
    response = client.post(f"/api/v1/projects/{project_id}/landscapes/generate", json={
        "claim_ids": claims, "expected_question_revision": 1})
    assert response.status_code == 201, response.text
    landscape = response.json()["landscape"]
    assessment = landscape["items"][0]["ai_conflict_assessment"]
    assert assessment["asserts_direct_conflict"] is True
    assert assessment["server_direct_conflict_role"] is False
    assert landscape["review_status"] == "pending_review" and not landscape["eligible_for_downstream"]
