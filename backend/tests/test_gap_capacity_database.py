"""Exact accepted frozen authority, only in a fenced disposable PostgreSQL DB.

No Qwen/HTTP transport: providers below return deterministic structured values.
The parent runner owns database creation, migration, identity fencing and cleanup.
"""

from copy import deepcopy
from datetime import datetime
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.persistence.database import get_engine
from app.persistence.models import (
    Claim, ClaimEvidence, Evidence, GapClaim, GapEvidence, GapLandscapeItem,
    GenerationRun, LandscapeItem, LandscapeItemClaim, LandscapeItemEvidence,
    Paper, PaperChunk, Project, ResearchGap, ResearchLandscape, now_utc,
)
from app.providers.interfaces import LLMResult
from app.services.errors import ServiceError
from app.services.m4 import M4Service
from app.services.m4_schemas import GapBatch, M4Assessment
from app.services.m4_schemas import GapProposal


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="Requires fenced disposable Gap database")
FIXTURES = Path(__file__).parent / "fixtures" / "gap_capacity"


def frozen(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def instant(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def canonical_context(value):
    result = deepcopy(value)
    for key in ("claims", "evidence"):
        result[key].sort(key=lambda row: row["id"])
    result["landscape"]["items"].sort(key=lambda row: row["id"])
    return result


class RecordingGapProvider:
    """Raw structured outputs let the service validate its actual provider schema."""

    def __init__(self):
        self.calls = []
        self.generation_calls = 0
        self.assessment_calls = 0
        self.count = 1
        self.generation_repair = False
        self.assessment_repair = False
        self.overflow = "none"
        self.overflow_index = None
        self.reference_heavy = False
        self.bad_reference = False
        self.global_scope = False
        self.verdict = "aligned"
        self.fail_assessment_index = None
        self.on_call = None
        self.generated = []
        self.assessed = []

    def generate_structured(self, prompt, output_schema, context):
        from app.services.gap_context import decode_gap_context, decode_gap_proposal

        self.calls.append({"prompt": prompt, "schema": output_schema.__name__,
                           "context": deepcopy(context)})
        if self.on_call:
            self.on_call(self)
        if issubclass(output_schema, M4Assessment):
            self.assessment_calls += 1
            decoded = decode_gap_context(context["scientific_input"])
            proposed = decode_gap_proposal(context["proposal"], context["scientific_input"])
            self.assessed.append(proposed)
            invalid = ((self.assessment_repair and self.assessment_calls % 2 == 1) or
                       (self.fail_assessment_index is not None and
                        proposed["title"] == f"Disposable Gap {self.fail_assessment_index}"))
            output = {
                "verdict": "invalid" if invalid else self.verdict,
                "risk_flags": ["feasibility_unknown", "condition_loss"]
                    if self.verdict != "aligned" else [],
                "source_ids": [decoded["landscape"]["id"]],
                "justification": "Deterministic advice only; no human selection or empirical verdict.",
            }
        else:
            assert issubclass(output_schema, GapBatch)
            self.generation_calls += 1
            decoded = decode_gap_context(context)
            items = decoded["landscape"]["items"]
            chosen = items if self.reference_heavy else [items[0]]
            claim_ids = sorted({cid for item in chosen for cid in item["claim_ids"]})
            by_claim = {row["id"]: row for row in decoded["claims"]}
            evidence = sorted({eid for cid in claim_ids for field in
                               ("evidence_for_ids", "evidence_against_ids")
                               for eid in by_claim[cid][field]})
            gaps = []
            for index in range(self.count):
                gap = {
                    "title": f"Disposable Gap {index}",
                    "description": "Within the supplied synthetic literature, physical adherence remains unverified.",
                    "gap_type": "methodological_limitation",
                    "why_it_matters": "This is an offline fixture for source traceability and human inspection.",
                    "uncertainty": "No results or empirical conformity are established.",
                    "assumptions": [], "novelty_scope": "supplied_literature",
                    "landscape_item_ids": [item["id"] for item in chosen],
                    "claim_ids": claim_ids,
                    "evidence_for_ids": evidence[:-1] if self.reference_heavy else [evidence[0]],
                    "evidence_against_ids": [evidence[-1]] if self.reference_heavy else [],
                }
                if self.global_scope:
                    gap["novelty_scope"] = "global_field"
                if self.bad_reference or (self.generation_repair and self.generation_calls == 1):
                    gap["evidence_for_ids"] = [str(uuid4())]
                overflow_round = (self.overflow == "always" or
                                  (self.overflow == "repair" and self.generation_calls == 1))
                if overflow_round and (self.overflow_index is None or self.overflow_index == index):
                    gap["assumptions"] = ["x" * 30001]
                gaps.append(gap)
            output = {"gaps": gaps}
            self.generated.append(deepcopy(output))
        return LLMResult(output=output, model_name="qwen3.8-max", attempt_count=1)


@pytest.fixture
def exact_gap_case():
    # This check supplements, and does not replace, the parent's libpq fence.
    with get_engine().connect() as connection:
        database_name = connection.scalar(text("SELECT current_database()"))
        assert database_name.startswith("rc_gap_test_")
    assert not get_settings().qwen_api_key
    assert os.getenv("RUN_REAL_QWEN", "0") != "1"
    context = frozen("accepted_context.json")
    accepted = frozen("accepted_landscape.json")
    manifest = frozen("accepted_manifest.json")
    chunks = frozen("approved_chunks.json")
    assert len(context["claims"]) == 18 and len(context["evidence"]) == 19
    assert len(context["landscape"]["items"]) == 5
    assert accepted["origin"] == "human_revision" and accepted["generation_run_id"] is None
    assert accepted["ai_summary_assessments"] is None and accepted["review_status"] == "accepted"
    provider = RecordingGapProvider()
    settings = get_settings().model_copy(update={"qwen_model": "qwen3.8-max", "llm_max_context_chars": 30000})
    service = M4Service(settings, provider)
    project_id = str(uuid4())  # never write the production Project identity
    with Session(get_engine()) as session:
        session.add(Project(id=project_id, title="Disposable accepted Gap capacity fixture",
                            research_question=context["research_question"]))
        session.flush()
        paper_ids = sorted({row["paper_id"] for row in context["evidence"]})
        for paper_id in paper_ids:
            rows = [row for row in context["evidence"] if row["paper_id"] == paper_id]
            assert len({row["paper_title"] for row in rows}) == 1
            session.add(Paper(id=paper_id, project_id=project_id, title=rows[0]["paper_title"],
                              source_filename="approved-disposable-synthetic.pdf",
                              storage_key=f"disposable-{uuid4()}.pdf", parse_status="indexed"))
        session.flush()
        approved_chunks = {row["id"]: row for row in chunks}
        for row in context["evidence"]:
            chunk = approved_chunks[row["paper_chunk_id"]]
            assert chunk["paper_id"] == row["paper_id"] and chunk["page_number"] == row["page_number"]
            assert row["source_text"] in chunk["text"]
        for chunk in chunks:
            session.add(PaperChunk(**chunk))
        session.flush()
        for row in context["evidence"]:
            session.add(Evidence(id=row["id"], project_id=project_id, paper_chunk_id=row["paper_chunk_id"],
                                 source_text=row["source_text"], evidence_type="method_description",
                                 selection_status="selected", question_revision=1))
        scaffold_run = GenerationRun(project_id=project_id, stage="claim", status="succeeded",
            provider="offline_disposable_fixture", model="mock", prompt_name="fixture", prompt_version="fixture",
            prompt_hash="0" * 64, schema_version="fixture", research_question_snapshot=context["research_question"],
            question_revision=1, input_manifest=[], input_digest="0" * 64, attempt_count=0)
        session.add(scaffold_run)
        session.flush()
        by_manifest = {(row["type"], row["id"]): row for row in manifest}
        for row in context["claims"]:
            claim_manifest = by_manifest[("claim", row["id"])]
            session.add(Claim(id=row["id"], project_id=project_id, text=row["text"], claim_type=row["claim_type"],
                human_grounding_status=row["human_grounding_status"],
                human_reviewed_at=instant(claim_manifest["human_reviewed_at"]), confidence="unknown",
                uncertainty=row["uncertainty"], selection_status="selected", question_revision=1,
                origin="model", generation_run_id=scaffold_run.id))
        session.flush()
        for row in context["claims"]:
            for key, role in (("evidence_for_ids", "supports"), ("evidence_against_ids", "challenges")):
                session.add_all([ClaimEvidence(claim_id=row["id"], evidence_id=eid, role=role) for eid in row[key]])
        # Minimal disposable lineage scaffold, not a claim about original scientific wording.
        old_run = GenerationRun(project_id=project_id, stage="landscape", status="succeeded",
            provider="offline_disposable_fixture", model="mock", prompt_name="fixture", prompt_version="fixture",
            prompt_hash="1" * 64, schema_version="fixture", research_question_snapshot=context["research_question"],
            question_revision=1, input_manifest=[], input_digest="1" * 64, attempt_count=0)
        session.add(old_run)
        session.flush()
        session.add(ResearchLandscape(id=accepted["supersedes_id"], project_id=project_id,
            summary="Disposable historical lineage scaffold.", uncertainty="No scientific assertion.",
            origin="model", generation_run_id=old_run.id, question_revision=1))
        session.flush()
        session.add(ResearchLandscape(id=accepted["id"], project_id=project_id, summary=accepted["summary"],
            uncertainty=accepted["uncertainty"], review_status="accepted", reviewed_at=instant(accepted["reviewed_at"]),
            origin="human_revision", supersedes_id=accepted["supersedes_id"], question_revision=1))
        session.flush()
        for item in accepted["items"]:
            session.add(LandscapeItem(id=item["id"], landscape_id=accepted["id"], category=item["category"],
                                     text=item["text"], uncertainty=item["uncertainty"]))
        session.flush()
        for item in accepted["items"]:
            session.add_all([LandscapeItemClaim(item_id=item["id"], claim_id=cid) for cid in item["claim_ids"]])
            session.add_all([LandscapeItemEvidence(item_id=item["id"], evidence_id=eid) for eid in item["evidence_ids"]])
        session.commit()
        actual = service._landscape_context(session, project_id, 1, accepted["id"])
        assert canonical_context({"research_question": context["research_question"], **actual["context"]}) == canonical_context(context)
        assert actual["manifest"] == manifest
    try:
        yield service, provider, project_id, accepted["id"], context, manifest
    finally:
        with Session(get_engine()) as session:
            gaps = list(session.scalars(select(ResearchGap.id).where(ResearchGap.project_id == project_id)))
            for model, column in ((GapEvidence, GapEvidence.gap_id), (GapClaim, GapClaim.gap_id),
                                  (GapLandscapeItem, GapLandscapeItem.gap_id)):
                session.execute(delete(model).where(column.in_(gaps)))
            session.execute(delete(ResearchGap).where(ResearchGap.project_id == project_id))
            landscapes = list(session.scalars(select(ResearchLandscape.id).where(ResearchLandscape.project_id == project_id)))
            items = list(session.scalars(select(LandscapeItem.id).where(LandscapeItem.landscape_id.in_(landscapes))))
            session.execute(delete(LandscapeItemClaim).where(LandscapeItemClaim.item_id.in_(items)))
            session.execute(delete(LandscapeItemEvidence).where(LandscapeItemEvidence.item_id.in_(items)))
            session.execute(delete(LandscapeItem).where(LandscapeItem.id.in_(items)))
            # Delete successors from newest to oldest, preserving self-FK integrity.
            while landscapes:
                parents = set(session.scalars(select(ResearchLandscape.supersedes_id).where(ResearchLandscape.id.in_(landscapes))))
                leaves = set(landscapes) - parents
                assert leaves
                session.execute(delete(ResearchLandscape).where(ResearchLandscape.id.in_(leaves)))
                landscapes = list(set(landscapes) - leaves)
            claims = list(session.scalars(select(Claim.id).where(Claim.project_id == project_id)))
            session.execute(delete(ClaimEvidence).where(ClaimEvidence.claim_id.in_(claims)))
            session.execute(delete(Claim).where(Claim.project_id == project_id, Claim.origin == "human_revision"))
            session.execute(delete(Claim).where(Claim.project_id == project_id))
            session.execute(delete(GenerationRun).where(GenerationRun.project_id == project_id))
            session.execute(delete(Evidence).where(Evidence.project_id == project_id))
            session.execute(delete(PaperChunk).where(PaperChunk.paper_id.in_(paper_ids)))
            session.execute(delete(Paper).where(Paper.project_id == project_id))
            session.execute(delete(Project).where(Project.id == project_id))
            session.commit()


def gap_runs(project_id):
    with Session(get_engine()) as session:
        return list(session.scalars(select(GenerationRun).where(
            GenerationRun.project_id == project_id, GenerationRun.stage == "gap")))


def assert_failed_atomic(case, expected_attempts):
    service, provider, project, *_ = case
    runs = gap_runs(project)
    assert len(runs) == 1 and runs[0].status == "failed" and runs[0].attempt_count == expected_attempts
    assert service.list_gaps(project) == []
    with Session(get_engine()) as session:
        for model in (GapClaim, GapEvidence, GapLandscapeItem):
            assert not list(session.scalars(select(model)))


def test_exact_accepted_human_revision_fits_through_independent_advisory(exact_gap_case):
    service, provider, project, landscape, context, manifest = exact_gap_case
    assert len(str(context)) == 36325
    result = service.generate_gaps(project, landscape, 1)
    assert len(result["gaps"]) == 1 and len(provider.calls) == 2
    from app.services.gap_context import decode_gap_context
    actual_source = provider.calls[0]["context"]
    assert canonical_context(decode_gap_context(actual_source)) == canonical_context(context)
    assert all(len(str(call["context"])) <= 30000 for call in provider.calls)
    run = gap_runs(project)[0]
    assert run.status == "succeeded" and run.input_manifest == manifest
    assert run.schema_version == "m4-gap-v2" and run.prompt_version == "m4-v4"
    gap = result["gaps"][0]
    assert gap["selection_status"] == "candidate" and not gap["eligible_for_generation"]
    assert gap["novelty_scope"] == "supplied_literature"


def test_harmless_landscape_item_order_changes_preserve_gap_source_authority(exact_gap_case, monkeypatch):
    service, provider, project, landscape, context, manifest = exact_gap_case
    original_loader = service._landscape_context
    loads = []

    def permuted_loader(session, project_id, revision, landscape_id):
        info = original_loader(session, project_id, revision, landscape_id)
        original_items = info["items"]
        original_manifest = info["manifest"]
        if loads:
            info["context"] = deepcopy(info["context"])
            info["context"]["landscape"]["items"].reverse()
        loads.append([row["id"] for row in info["context"]["landscape"]["items"]])
        assert info["items"] is original_items and info["manifest"] is original_manifest
        assert info["manifest"] == manifest
        assert canonical_context({"research_question": context["research_question"], **info["context"]}) == canonical_context(context)
        return info

    monkeypatch.setattr(service, "_landscape_context", permuted_loader)
    result = service.generate_gaps(project, landscape, 1)
    assert len(loads) == 3
    assert loads[1] == loads[2] == list(reversed(loads[0]))
    assert len(result["gaps"]) == 1 and len(provider.calls) == 2
    run = gap_runs(project)[0]
    assert run.status == "succeeded" and run.input_manifest == manifest
    assert run.attempt_count == 2


@pytest.mark.parametrize("count", [1, 10])
def test_all_gap_candidates_have_independent_generation_and_assessment_repairs(exact_gap_case, count):
    service, provider, project, landscape, *_ = exact_gap_case
    provider.count = count
    provider.generation_repair = provider.assessment_repair = True
    result = service.generate_gaps(project, landscape, 1)
    assert len(result["gaps"]) == count
    assert provider.generation_calls == 2 and provider.assessment_calls == 2 * count
    assert len(provider.calls) == 2 + 2 * count
    assert provider.calls[0]["context"] == provider.calls[1]["context"]
    for index in range(count):
        left, right = provider.calls[2 + index * 2:4 + index * 2]
        assert left["context"] == right["context"]
        assert provider.assessed[2 * index] == provider.assessed[2 * index + 1]
        assert "Previous assessment was invalid" in right["prompt"]
    assert gap_runs(project)[0].attempt_count == 2 + 2 * count
    assert all(len(str(call["context"])) <= 30000 for call in provider.calls)


@pytest.mark.parametrize("verdict", ["concerns", "misaligned"])
def test_reference_heavy_roles_and_advisory_concerns_remain_visible(exact_gap_case, verdict):
    service, provider, project, landscape, context, *_ = exact_gap_case
    provider.reference_heavy = True
    provider.verdict = verdict
    gap = service.generate_gaps(project, landscape, 1)["gaps"][0]
    assert set(gap["landscape_item_ids"]) == {row["id"] for row in context["landscape"]["items"]}
    assert set(gap["claim_ids"]) == {row["id"] for row in context["claims"]}
    assert set(gap["evidence_for_ids"] + gap["evidence_against_ids"]) == {row["id"] for row in context["evidence"]}
    assert set(gap["evidence_for_ids"]).isdisjoint(gap["evidence_against_ids"])
    assert gap["selection_status"] == "candidate" and gap["ai_assessment"]["verdict"] == verdict
    assert gap["ai_assessment"]["risk_flags"] == ["feasibility_unknown", "condition_loss"]
    with Session(get_engine()) as session:
        links = list(session.scalars(select(GapEvidence).where(GapEvidence.gap_id == gap["id"])))
        assert {link.evidence_id for link in links if link.role == "supports"} == set(gap["evidence_for_ids"])
        assert {link.evidence_id for link in links if link.role == "challenges"} == set(gap["evidence_against_ids"])
    assert provider.assessed[0] == provider.generated[0]["gaps"][0]


def test_schema_valid_candidate_overflow_repairs_before_any_advisory(exact_gap_case):
    service, provider, project, landscape, *_ = exact_gap_case
    provider.overflow = "repair"
    result = service.generate_gaps(project, landscape, 1)
    GapBatch.model_validate(provider.generated[0])  # schema-valid alone does not prove fit
    assert provider.generation_calls == 2 and provider.assessment_calls == 1
    assert result["gaps"][0]["assumptions"] == []
    assert gap_runs(project)[0].attempt_count == 3


def test_schema_valid_persistent_overflow_exhausts_one_repair_atomically(exact_gap_case):
    service, provider, project, landscape, *_ = exact_gap_case
    provider.overflow = "always"
    with pytest.raises(ServiceError) as failure:
        service.generate_gaps(project, landscape, 1)
    assert failure.value.code == "structured_output_invalid"
    assert provider.generation_calls == 2 and provider.assessment_calls == 0
    assert_failed_atomic(exact_gap_case, 2)


def test_oversized_tenth_candidate_prevents_all_advisory_calls(exact_gap_case):
    service, provider, project, landscape, *_ = exact_gap_case
    provider.count = 10
    provider.overflow = "always"
    provider.overflow_index = 9
    with pytest.raises(ServiceError) as failure:
        service.generate_gaps(project, landscape, 1)
    assert failure.value.code == "structured_output_invalid"
    assert provider.generation_calls == 2 and provider.assessment_calls == 0
    for output in provider.generated:
        GapBatch.model_validate(output)
        assert all(candidate["assumptions"] == [] for candidate in output["gaps"][:9])
        assert output["gaps"][9]["assumptions"] == ["x" * 30001]
    assert_failed_atomic(exact_gap_case, 2)


@pytest.mark.parametrize("mode", ["bad_reference", "global_scope"])
def test_foreign_references_and_global_novelty_fail_after_bounded_repair(exact_gap_case, mode):
    service, provider, project, landscape, *_ = exact_gap_case
    setattr(provider, mode, True)
    with pytest.raises(ServiceError) as failure:
        service.generate_gaps(project, landscape, 1)
    assert failure.value.code == "structured_output_invalid"
    assert provider.assessment_calls == 0
    assert_failed_atomic(exact_gap_case, 2)


def test_late_candidate_assessment_failure_leaves_no_partial_gap_batch(exact_gap_case):
    service, provider, project, landscape, *_ = exact_gap_case
    provider.count = 10
    provider.fail_assessment_index = 9
    with pytest.raises(ServiceError) as failure:
        service.generate_gaps(project, landscape, 1)
    assert failure.value.code == "structured_output_invalid"
    assert provider.generation_calls == 1 and provider.assessment_calls == 11
    assert_failed_atomic(exact_gap_case, 12)


@pytest.mark.parametrize("state", ["pending_review", "rejected", "superseded", "claim_rejected", "claim_superseded"])
def test_current_accepted_source_authority_is_required_before_run(exact_gap_case, state):
    service, provider, project, landscape, context, *_ = exact_gap_case
    with Session(get_engine()) as session:
        if state in {"pending_review", "rejected"}:
            session.get(ResearchLandscape, landscape).review_status = state
        elif state == "superseded":
            session.add(ResearchLandscape(project_id=project, summary="Disposable successor.", uncertainty="No data.",
                origin="human_revision", supersedes_id=landscape, question_revision=1))
        elif state == "claim_rejected":
            session.get(Claim, context["claims"][0]["id"]).selection_status = "rejected"
        else:
            original = context["claims"][0]
            session.add(Claim(project_id=project, text="Disposable superseding source fixture.", claim_type="limitation",
                confidence="unknown", uncertainty="No data.", origin="human_revision", supersedes_id=original["id"],
                question_revision=1, human_grounding_status="supported", selection_status="selected"))
        session.commit()
    with pytest.raises(ServiceError) as failure:
        service.generate_gaps(project, landscape, 1)
    assert failure.value.status_code == 409
    assert provider.calls == [] and gap_runs(project) == []


def test_infeasible_full_source_rejected_before_run_or_provider(exact_gap_case):
    service, provider, project, landscape, context, *_ = exact_gap_case
    with Session(get_engine()) as session:
        source = session.get(Evidence, context["evidence"][0]["id"])
        source.source_text = "x" * 30001
        source.chunk.text = source.source_text
        session.commit()
    with pytest.raises(ServiceError) as failure:
        service.generate_gaps(project, landscape, 1)
    assert failure.value.status_code == 422
    assert provider.calls == [] and gap_runs(project) == []


@pytest.mark.parametrize("change", ["landscape_rejected", "evidence_rejected", "question_revision"])
def test_source_change_after_advisory_blocks_stale_final_persistence(exact_gap_case, change):
    service, provider, project, landscape, context, *_ = exact_gap_case

    def change_source(current):
        if len(current.calls) != 2:
            return
        with Session(get_engine()) as session:
            if change == "landscape_rejected":
                session.get(ResearchLandscape, landscape).review_status = "rejected"
            elif change == "evidence_rejected":
                session.get(Evidence, context["evidence"][0]["id"]).selection_status = "rejected"
            else:
                session.get(Project, project).question_revision = 2
            session.commit()
    provider.on_call = change_source
    with pytest.raises(ServiceError) as failure:
        service.generate_gaps(project, landscape, 1)
    assert failure.value.status_code == 409
    assert_failed_atomic(exact_gap_case, 2)


def test_unmanifested_item_uncertainty_change_blocks_final_persistence(exact_gap_case):
    service, provider, project, landscape, context, manifest = exact_gap_case
    original_uncertainty = context["landscape"]["items"][0]["uncertainty"]

    def change_uncertainty(current):
        if len(current.calls) != 2:
            return
        with Session(get_engine()) as session:
            item = session.get(LandscapeItem, context["landscape"]["items"][0]["id"])
            item.uncertainty += " Disposable condition changed during advisory."
            session.commit()
            authority = service._landscape_context(session, project, 1, landscape)
            # Establish that the existing canonical manifest cannot detect this change.
            assert authority["manifest"] == manifest
    provider.on_call = change_uncertainty
    with pytest.raises(ServiceError) as failure:
        service.generate_gaps(project, landscape, 1)
    assert failure.value.code == "invalid_state"
    assert failure.value.message == "Complete Gap source changed during generation"
    assert provider.generation_calls == 1 and provider.assessment_calls == 1
    assert_failed_atomic(exact_gap_case, 2)
    with Session(get_engine()) as session:
        assert session.get(LandscapeItem, context["landscape"]["items"][0]["id"]).uncertainty != original_uncertainty


def test_final_response_failure_rolls_back_all_ten_candidates(exact_gap_case, monkeypatch):
    service, provider, project, landscape, *_ = exact_gap_case
    provider.count = 10

    def fail(*_args):
        raise SQLAlchemyError("Disposable controlled response failure")
    monkeypatch.setattr(service, "_gap_read", fail)
    with pytest.raises(ServiceError) as failure:
        service.generate_gaps(project, landscape, 1)
    assert failure.value.code == "database_failed"
    assert_failed_atomic(exact_gap_case, 11)


def test_historical_gap_and_old_run_remain_readable_without_generation_budget(exact_gap_case):
    service, provider, project, landscape, *_ = exact_gap_case
    result = service.generate_gaps(project, landscape, 1)
    gap_id = result["gaps"][0]["id"]
    with Session(get_engine()) as session:
        gap = session.get(ResearchGap, gap_id)
        gap.description = "Historical content " + "h" * 40001
        gap.ai_assessment = {"verdict": "concerns", "risk_flags": ["condition_loss"],
                             "source_ids": [], "justification": "Historical advisory retained."}
        run = session.get(GenerationRun, result["generation_run_id"])
        run.schema_version, run.prompt_version = "m4-gap-v1", "m4-v2"
        session.commit()
    before = len(provider.calls)
    read = service.get_gap(project, gap_id)
    assert read["description"] == "Historical content " + "h" * 40001
    assert read["ai_assessment"]["risk_flags"] == ["condition_loss"]
    assert service.list_gaps(project)[0]["id"] == gap_id
    assert len(provider.calls) == before
    run = gap_runs(project)[0]
    assert (run.schema_version, run.prompt_version) == ("m4-gap-v1", "m4-v2")


def test_human_gap_revision_preserves_old_advice_and_requires_new_selection(exact_gap_case):
    service, provider, project, landscape, *_ = exact_gap_case
    provider.verdict = "concerns"
    original = service.generate_gaps(project, landscape, 1)["gaps"][0]
    selected = service.select_gap(project, original["id"], "selected", 1)
    assert selected["eligible_for_generation"]
    proposed = GapProposal.model_validate(provider.generated[0]["gaps"][0])
    proposed.title = "Disposable human revised Gap"
    revised = service.revise_gap(project, original["id"], 1, proposed)
    assert revised["origin"] == "human_revision" and revised["supersedes_id"] == original["id"]
    assert revised["selection_status"] == "candidate" and revised["generation_run_id"] is None
    assert revised["ai_assessment"] is None and not revised["eligible_for_generation"]
    old = service.get_gap(project, original["id"])
    assert old["ai_assessment"] == original["ai_assessment"] and not old["eligible_for_generation"]
    assert len(provider.calls) == 2 and len(gap_runs(project)) == 1
    with pytest.raises(ServiceError) as failure:
        service.select_gap(project, original["id"], "selected", 1)
    assert failure.value.code == "superseded_resource"
