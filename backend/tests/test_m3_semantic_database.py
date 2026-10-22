"""Persistence tests for the M3 semantic gate using adversarial synthetic output."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.persistence.database import get_engine
from app.persistence.models import Evidence, GenerationRun, Paper, PaperChunk, ResearchLandscape
from test_m3_database import live_m3


pytestmark = pytest.mark.skipif(os.getenv("RUN_DB_INTEGRATION") != "1", reason="Requires migrated PostgreSQL")


def two_claims(live_m3, first: str, second: str):
    client, provider, source = live_m3
    project_id, first_id, _, _ = source(first)
    with Session(get_engine()) as session:
        paper = Paper(project_id=project_id, source_filename="second.pdf", storage_key=f"second-{uuid4()}.pdf", parse_status="indexed")
        session.add(paper)
        session.flush()
        chunk = PaperChunk(paper_id=paper.id, page_number=3, chunk_index=0, text=second)
        session.add(chunk)
        session.flush()
        row = Evidence(project_id=project_id, paper_chunk_id=chunk.id, source_text=second,
                       evidence_type="reported_result", selection_status="selected", question_revision=1)
        session.add(row)
        session.commit()
        second_id = row.id
    provider.responses = [{"claims": [
        {"text": text, "claim_type": "finding", "grounding_status": "supported", "confidence": "medium",
         "uncertainty": "Single report", "evidence_for_ids": [eid], "evidence_against_ids": []}
        for text, eid in ((first, first_id), (second, second_id))]}]
    generated = client.post(f"/api/v1/projects/{project_id}/claims/generate", json={
        "evidence_ids": [first_id, second_id], "expected_question_revision": 1})
    assert generated.status_code == 201, generated.text
    claim_ids = [item["id"] for item in generated.json()["claims"]]
    for claim_id in claim_ids:
        reviewed = client.patch(f"/api/v1/projects/{project_id}/claims/{claim_id}/grounding-review", json={
            "human_grounding_status": "supported", "expected_question_revision": 1})
        assert reviewed.status_code == 200, reviewed.text
        selected = client.patch(f"/api/v1/projects/{project_id}/claims/{claim_id}/selection", json={
            "selection_status": "selected", "expected_question_revision": 1})
        assert selected.status_code == 200, selected.text
    return project_id, claim_ids, [first_id, second_id]


@pytest.mark.parametrize("first,second,verdict,dimension", [
    ("A scored 80% accuracy on Dataset X.", "A scored 60% accuracy on Dataset Y.", "incomparable", "dataset"),
    ("A scored 80% accuracy on Dataset X.", "A scored 60% recall on Dataset X.", "incomparable", "metric"),
    ("A scored 80% in adults.", "A scored 60% in children.", "conditional_difference", "population"),
    ("A scored 80% under condition P.", "A scored 60% under condition Q.", "conditional_difference", "experimental_condition"),
    ("A beat baseline B by 10%.", "A beat baseline C by 5%.", "incomparable", "baseline"),
    ("A scored 80% on Dataset X.", "A scored 60% on Dataset Y.", "direct_conflict", "dataset"),
])
def test_false_conflict_is_repaired_before_persistence(live_m3, first, second, verdict, dimension):
    client, provider, _ = live_m3
    project_id, claims, evidence = two_claims(live_m3, first, second)
    conflict = {"category": "conflict", "text": "The studies directly contradict each other.",
                "uncertainty": "none_identified", "claim_ids": claims, "evidence_ids": evidence}
    corrected = {**conflict, "category": "finding", "text": "The studies used different evaluation settings.",
                 "uncertainty": "Direct comparison is unclear."}
    provider.responses = [
        {"summary": "The findings differ.", "uncertainty": "Source conditions differ.", "items": [conflict]},
        {"summary": "The findings differ across settings.", "uncertainty": "Comparison is limited.", "items": [corrected]},
    ]
    provider.verification_responses = [{"verdict": verdict, "dimensions": {
        key: "different" if key == dimension else "unknown" for key in
        ("dataset", "population", "task", "metric", "experimental_condition", "baseline")},
        "evidence_refs": evidence, "support_verdict": "supported", "flagged_statements": [],
        "qualifications": [], "justification": "Conditions differ."}]
    generated = client.post(f"/api/v1/projects/{project_id}/landscapes/generate", json={
        "claim_ids": claims, "expected_question_revision": 1})
    assert generated.status_code == 201, generated.text
    assert all(item["category"] != "conflict" for item in generated.json()["landscape"]["items"])
    with Session(get_engine()) as session:
        assert session.get(GenerationRun, generated.json()["generation_run_id"]).attempt_count == 5


def test_true_conflict_persists_after_verification(live_m3):
    client, provider, _ = live_m3
    project_id, claims, evidence = two_claims(live_m3,
        "On Dataset X under P, A scored 80% accuracy.",
        "On Dataset X under P, A scored 60% accuracy.")
    provider.responses = [{"summary": "The reports differ under P on Dataset X.",
        "uncertainty": "Replication details are unavailable.", "items": [{"category": "conflict",
        "text": "The comparable reports differ in accuracy.", "uncertainty": "Replication details unknown",
        "claim_ids": claims, "evidence_ids": evidence}]}]
    generated = client.post(f"/api/v1/projects/{project_id}/landscapes/generate", json={
        "claim_ids": claims, "expected_question_revision": 1})
    assert generated.status_code == 201, generated.text
    assert generated.json()["landscape"]["items"][0]["category"] == "conflict"
    with Session(get_engine()) as session:
        assert session.get(GenerationRun, generated.json()["generation_run_id"]).attempt_count == 3


def test_unsupported_summary_repair_failure_leaves_no_landscape(live_m3):
    client, provider, _ = live_m3
    project_id, claims, evidence = two_claims(live_m3,
        "A beat baseline B by 10%.", "A beat baseline C by 5%.")
    item = {"category": "finding", "text": "The studies used different baselines.",
            "uncertainty": "Direct comparison is unclear", "claim_ids": claims, "evidence_ids": evidence}
    provider.responses = [
        {"summary": "There is definitively no conflict.", "uncertainty": "none_identified", "items": [item]},
        {"summary": "There is definitively no conflict.", "uncertainty": "none_identified"},
    ]
    rejected = {"verdict": "unsupported_major_statement", "flagged_statements": ["There is definitively no conflict."],
                "evidence_refs": evidence, "qualifications": [], "justification": "Different baselines cannot prove this."}
    provider.verification_responses = [rejected, rejected]
    response = client.post(f"/api/v1/projects/{project_id}/landscapes/generate", json={
        "claim_ids": claims, "expected_question_revision": 1})
    assert response.status_code == 502, response.text
    with Session(get_engine()) as session:
        assert not list(session.scalars(select(ResearchLandscape).where(ResearchLandscape.project_id == project_id)))
        run = session.get(GenerationRun, response.json()["error"]["details"]["generation_run_id"])
        assert run.status == "failed" and run.attempt_count == 5


def test_unverified_conflict_after_repair_leaves_no_landscape(live_m3):
    client, provider, _ = live_m3
    project_id, claims, evidence = two_claims(live_m3,
        "A scored 80% on Dataset X.", "A scored 60% on Dataset Y.")
    conflict = {"category": "conflict", "text": "The studies directly contradict each other.",
                "uncertainty": "none_identified", "claim_ids": claims, "evidence_ids": evidence}
    provider.responses = [{"summary": "Direct conflict.", "uncertainty": "none_identified", "items": [conflict]},
                          {"summary": "Direct conflict.", "uncertainty": "none_identified", "items": [conflict]}]
    rejected = {"verdict": "incomparable", "dimensions": {
        key: "different" if key == "dataset" else "unknown" for key in
        ("dataset", "population", "task", "metric", "experimental_condition", "baseline")},
        "evidence_refs": evidence, "support_verdict": "supported", "flagged_statements": [],
        "qualifications": [], "justification": "Datasets differ."}
    provider.verification_responses = [rejected, rejected]
    response = client.post(f"/api/v1/projects/{project_id}/landscapes/generate", json={
        "claim_ids": claims, "expected_question_revision": 1})
    assert response.status_code == 502, response.text
    with Session(get_engine()) as session:
        assert not list(session.scalars(select(ResearchLandscape).where(ResearchLandscape.project_id == project_id)))
        run = session.get(GenerationRun, response.json()["error"]["details"]["generation_run_id"])
        assert run.status == "failed" and run.attempt_count == 3


def test_unsupported_summary_is_repaired_and_reverified(live_m3):
    client, provider, _ = live_m3
    project_id, claims, evidence = two_claims(live_m3,
        "A beat baseline B by 10%.", "A beat baseline C by 5%.")
    item = {"category": "finding", "text": "The reports used different baselines.",
            "uncertainty": "Direct comparison is unclear", "claim_ids": claims, "evidence_ids": evidence}
    provider.responses = [{"summary": "There is definitively no conflict.", "uncertainty": "none_identified", "items": [item]},
                          {"summary": "The reported changes used different baselines.",
                           "uncertainty": "Whether the findings conflict is unclear."}]
    provider.verification_responses = [
        {"verdict": "unsupported_major_statement", "flagged_statements": ["There is definitively no conflict."],
         "evidence_refs": evidence, "qualifications": [], "justification": "Different baselines do not establish this."},
        {"verdict": "supported", "flagged_statements": [], "evidence_refs": evidence,
         "qualifications": [], "justification": "The repaired summary preserves the condition."},
    ]
    response = client.post(f"/api/v1/projects/{project_id}/landscapes/generate", json={
        "claim_ids": claims, "expected_question_revision": 1})
    assert response.status_code == 201, response.text
    assert response.json()["landscape"]["summary"] == "The reported changes used different baselines."
    with Session(get_engine()) as session:
        assert session.get(GenerationRun, response.json()["generation_run_id"]).attempt_count == 5


def test_claim_selection_change_during_conflict_verification_prevents_commit(live_m3):
    client, provider, _ = live_m3
    project_id, claims, evidence = two_claims(live_m3,
        "On Dataset X under P, A scored 80% accuracy.",
        "On Dataset X under P, A scored 60% accuracy.")
    provider.contexts.clear()
    provider.responses = [{"summary": "The reports differ.", "uncertainty": "Replication unknown", "items": [
        {"category": "conflict", "text": "The findings conflict under P on Dataset X.",
         "uncertainty": "Replication unknown", "claim_ids": claims, "evidence_ids": evidence}]}]
    entered, release = Event(), Event()

    def hold_verifier():
        if len(provider.contexts) == 2:
            entered.set()
            assert release.wait(10)

    provider.on_call = hold_verifier
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.post, f"/api/v1/projects/{project_id}/landscapes/generate", json={
            "claim_ids": claims, "expected_question_revision": 1})
        assert entered.wait(10)
        try:
            changed = client.patch(f"/api/v1/projects/{project_id}/claims/{claims[0]}/selection", json={
                "selection_status": "rejected", "expected_question_revision": 1})
            assert changed.status_code == 200, changed.text
        finally:
            release.set()
        response = future.result(timeout=10)
    assert response.status_code == 409, response.text
    with Session(get_engine()) as session:
        assert not list(session.scalars(select(ResearchLandscape).where(ResearchLandscape.project_id == project_id)))
        run = session.scalar(select(GenerationRun).where(GenerationRun.project_id == project_id,
                                                         GenerationRun.stage == "landscape"))
        assert run.status == "failed" and run.attempt_count == 3


@pytest.mark.parametrize("stage", ["conflict", "summary"])
def test_landscape_verifier_invalid_source_reference_fails_closed(live_m3, stage):
    client, provider, _ = live_m3
    project_id, claims, evidence = two_claims(live_m3,
        "On Dataset X under P, A scored 80% accuracy.",
        "On Dataset X under P, A scored 60% accuracy.")
    provider.responses = [{"summary": "The reports differ.", "uncertainty": "Replication unknown", "items": [
        {"category": "conflict" if stage == "conflict" else "finding",
         "text": "The reports differ under P on Dataset X.", "uncertainty": "Replication unknown",
         "claim_ids": claims, "evidence_ids": evidence}]}]
    if stage == "conflict":
        invalid = {"verdict": "direct_conflict", "dimensions": {
            key: "comparable" for key in ("dataset", "population", "task", "metric", "experimental_condition", "baseline")},
            "evidence_refs": ["invented"], "support_verdict": "supported", "flagged_statements": [],
            "qualifications": [], "justification": "Invalid reference"}
    else:
        invalid = {"verdict": "supported", "flagged_statements": [], "evidence_refs": ["invented"],
                   "qualifications": [], "justification": "Invalid reference"}
    provider.verification_responses = [invalid, invalid]
    response = client.post(f"/api/v1/projects/{project_id}/landscapes/generate", json={
        "claim_ids": claims, "expected_question_revision": 1})
    assert response.status_code == 502, response.text
    with Session(get_engine()) as session:
        assert not list(session.scalars(select(ResearchLandscape).where(ResearchLandscape.project_id == project_id)))
        run = session.get(GenerationRun, response.json()["error"]["details"]["generation_run_id"])
        assert run.status == "failed" and run.attempt_count == (3 if stage == "conflict" else 4)
