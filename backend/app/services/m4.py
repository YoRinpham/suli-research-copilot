"""M4 scientific stages with explicit human authority and relational provenance."""

from collections.abc import Callable
from copy import deepcopy
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.persistence.models import (
    Claim, Evidence, ExperimentPlan, GapClaim, GapEvidence, GapLandscapeItem, GenerationRun,
    Hypothesis, HypothesisClaim, HypothesisEvidence, LandscapeItem, Project, ResearchGap,
    ResearchLandscape, now_utc,
)
from app.prompts.m4 import (GAP_ASSESS, GAP_PROMPT, HYPOTHESIS_ASSESS, HYPOTHESIS_PROMPT,
                            GAP_STRUCTURED_REPAIR, GAP_ASSESSMENT_REPAIR, M4_ASSESSMENT_REPAIR, PLAN_ASSESS, PLAN_PROMPT, PLAN_ASSESSMENT_REPAIR)
from app.prompts.scientific import Prompt, canonical_digest
from app.services.errors import ServiceError
from app.services.gap_context import GapContext, GapCapacityError
from app.services.gap_lineage import GapLineageError
from app.services.gap_diagnostics import diagnose_gap, bind_gap_run, gap_event, gap_capacity
from app.services.m4_schemas import GapBatch, GapProposal, HypothesisBatch, HypothesisProposal, M4Assessment, PlanProposal
from app.services.scientific import ScientificService, _hash_text


def _gap_source_authority(context: dict[str, Any]) -> str:
    """Complete source identity independent of unspecified relationship row order."""
    canonical = {**context,
        "landscape": {**context["landscape"], "items": sorted(context["landscape"]["items"], key=lambda row: row["id"])},
        "claims": sorted(context["claims"], key=lambda row: row["id"]),
        "evidence": sorted(context["evidence"], key=lambda row: row["id"])}
    return canonical_digest(canonical)


class HypothesisApplicationValidationError(ValueError):
    def __init__(self, message: str, validation_code: str) -> None:
        super().__init__(message)
        self.validation_code = validation_code


class M4Service(ScientificService):
    @staticmethod
    def validate_gap_sources(proposal: GapProposal, items: dict[str, dict[str, set[str]]],
                             claim_sources: dict[str, set[str]]) -> None:
        chosen = [items[item_id] for item_id in proposal.landscape_item_ids if item_id in items]
        if len(chosen) != len(proposal.landscape_item_ids):
            gap_event(layer="domain_validation", outcome="fail", category="source_lineage", validation_code="gap_foreign_landscape_item")
            raise GapLineageError("gap_foreign_landscape_item")
        claims = set().union(*(item["claims"] for item in chosen))
        evidence = set().union(*(item["evidence"] for item in chosen))
        cited_evidence = set(proposal.evidence_for_ids + proposal.evidence_against_ids)
        if not proposal.evidence_for_ids or not set(proposal.claim_ids) <= claims or not cited_evidence <= evidence:
            gap_event(layer="domain_validation", outcome="fail", category="source_lineage", validation_code="gap_incomplete_lineage")
            raise GapLineageError("gap_incomplete_lineage")
        if not cited_evidence <= set().union(*(claim_sources[cid] for cid in proposal.claim_ids)):
            gap_event(layer="domain_validation", outcome="fail", category="source_lineage", validation_code="gap_evidence_claim_mismatch")
            raise GapLineageError("gap_evidence_claim_mismatch")

    @staticmethod
    def validate_hypothesis_sources(proposal: HypothesisProposal, evidence_ids: set[str], claim_ids: set[str]) -> None:
        if not set(proposal.evidence_motivating_ids + proposal.evidence_challenging_ids) <= evidence_ids:
            raise ValueError("Hypothesis cited foreign Evidence")
        if not set(proposal.claim_ids) <= claim_ids:
            raise ValueError("Hypothesis cited foreign Claim")

    def _validate_hypothesis_batch(self, output: HypothesisBatch, info: dict[str, Any], count: int,
                                   *, run_id: str | None = None, repair_round: int | None = None) -> None:
        def trace(check: str, outcome: str, **fields: object) -> None:
            if run_id is not None and repair_round is not None:
                self._claim_trace(run_id, f"hypothesis_batch.{check}", repair_round,
                                  generation_round="initial" if repair_round == 0 else "repair",
                                  outcome=outcome, **fields)

        actual_count = len(output.hypotheses)
        if actual_count > count:
            code = "hypothesis_candidate_count_mismatch"
            trace("cardinality_validation", "fail", validation_code=code,
                  expected_count=count, actual_count=actual_count)
            raise HypothesisApplicationValidationError("Hypothesis batch exceeds requested count", code)
        trace("cardinality_validation", "pass", expected_count=count, actual_count=actual_count)
        for index, hypothesis in enumerate(output.hypotheses):
            try:
                self.validate_hypothesis_sources(hypothesis, info["gap_evidence_ids"], info["gap_claim_ids"])
            except ValueError as exc:
                foreign_evidence = sorted(
                    set(hypothesis.evidence_motivating_ids + hypothesis.evidence_challenging_ids)
                    - info["gap_evidence_ids"])
                if foreign_evidence:
                    code = "hypothesis_source_not_whitelisted"
                    trace("evidence_whitelist_validation", "fail", validation_code=code,
                          candidate_index=index, offending_source_id=foreign_evidence[0])
                    raise HypothesisApplicationValidationError(str(exc), code) from exc
                trace("evidence_whitelist_validation", "pass", candidate_index=index)
                foreign_claims = sorted(set(hypothesis.claim_ids) - info["gap_claim_ids"])
                if foreign_claims:
                    code = "hypothesis_source_not_whitelisted"
                    trace("claim_whitelist_validation", "fail", validation_code=code,
                          candidate_index=index, offending_source_id=foreign_claims[0])
                    raise HypothesisApplicationValidationError(str(exc), code) from exc
                trace("domain_validation", "fail", validation_code="hypothesis_domain_validation_error",
                      candidate_index=index)
                raise
            trace("evidence_whitelist_validation", "pass", candidate_index=index)
            trace("claim_whitelist_validation", "pass", candidate_index=index)

    @staticmethod
    def _successor(session: Session, model: type, object_id: str) -> bool:
        return session.scalar(select(model.id).where(model.supersedes_id == object_id)) is not None

    def _landscape_context(self, session: Session, project_id: str, revision: int, landscape_id: str) -> dict[str, Any]:
        landscape = session.scalar(select(ResearchLandscape).where(
            ResearchLandscape.id == landscape_id, ResearchLandscape.project_id == project_id))
        if landscape is None:
            raise ServiceError(404, "not_found", "ResearchLandscape not found", "gap_generation")
        if landscape.question_revision != revision or landscape.review_status != "accepted" or self._successor(session, ResearchLandscape, landscape_id):
            raise ServiceError(409, "invalid_state", "Landscape requires current human acceptance", "gap_generation")
        item_rows = list(session.scalars(select(LandscapeItem).where(LandscapeItem.landscape_id == landscape_id)))
        if not item_rows:
            raise ServiceError(424, "insufficient_evidence", "Landscape has no source items", "gap_generation")
        claim_ids = sorted({link.claim_id for item in item_rows for link in item.claim_links})
        claims, source_context, claim_manifest, evidence_ids = self._claim_input(session, project_id, revision, claim_ids)
        claim_sources = {claim.id: {link.evidence_id for link in claim.evidence_links} for claim in claims}
        items: dict[str, dict[str, set[str]]] = {}
        item_context = []
        item_manifest = []
        for item in item_rows:
            linked_claims = {link.claim_id for link in item.claim_links}
            linked_evidence = {link.evidence_id for link in item.evidence_links}
            if not linked_claims or not linked_evidence <= set().union(*(claim_sources[cid] for cid in linked_claims)):
                raise ServiceError(409, "invalid_provenance", "Landscape source join is invalid", "gap_generation")
            # A Gap may cite Evidence present in its LandscapeItem or inherited from its cited Claim.
            items[item.id] = {"claims": linked_claims, "evidence": linked_evidence | set().union(*(claim_sources[cid] for cid in linked_claims))}
            item_context.append({"id": item.id, "category": item.category, "text": item.text,
                                 "uncertainty": item.uncertainty, "claim_ids": sorted(linked_claims),
                                 "evidence_ids": sorted(items[item.id]["evidence"])})
            item_manifest.append({"type": "landscape_item", "id": item.id, "content_hash": _hash_text(item.text),
                                  "claim_ids": sorted(linked_claims), "evidence_ids": sorted(items[item.id]["evidence"])})
        manifest = [{"type": "landscape", "id": landscape.id, "summary_hash": _hash_text(landscape.summary),
                     "uncertainty_hash": _hash_text(landscape.uncertainty), "review_status": landscape.review_status,
                     "reviewed_at": landscape.reviewed_at.isoformat() if landscape.reviewed_at else None},
                    *sorted(item_manifest, key=lambda row: row["id"]), *claim_manifest]
        return {"context": {"landscape": {"id": landscape.id, "summary": landscape.summary,
                                            "uncertainty": landscape.uncertainty, "items": item_context}, **source_context},
                "manifest": manifest, "items": items, "claim_sources": claim_sources,
                "claim_ids": set(claim_ids), "evidence_ids": evidence_ids,
                "landscape": landscape}

    @staticmethod
    def _gap_proposal(gap: ResearchGap) -> GapProposal:
        return GapProposal.model_validate({"title": gap.title, "description": gap.description,
            "gap_type": gap.gap_type, "why_it_matters": gap.why_it_matters, "uncertainty": gap.uncertainty,
            "assumptions": gap.assumptions, "novelty_scope": gap.novelty_scope,
            "landscape_item_ids": [link.item_id for link in gap.item_links],
            "claim_ids": [link.claim_id for link in gap.claim_links],
            "evidence_for_ids": [link.evidence_id for link in gap.evidence_links if link.role == "supports"],
            "evidence_against_ids": [link.evidence_id for link in gap.evidence_links if link.role == "challenges"]})

    def _gap_context(self, session: Session, project_id: str, revision: int, gap_id: str) -> dict[str, Any]:
        gap = session.scalar(select(ResearchGap).where(ResearchGap.id == gap_id, ResearchGap.project_id == project_id))
        if gap is None:
            raise ServiceError(404, "not_found", "ResearchGap not found", "hypothesis_generation")
        if gap.question_revision != revision or gap.selection_status != "selected" or self._successor(session, ResearchGap, gap_id):
            raise ServiceError(409, "invalid_state", "Gap requires current human selection", "hypothesis_generation")
        base = self._landscape_context(session, project_id, revision, gap.landscape_id)
        try:
            proposal = self._gap_proposal(gap)
            self.validate_gap_sources(proposal, base["items"], base["claim_sources"])
        except (ValidationError, ValueError) as exc:
            raise ServiceError(409, "invalid_provenance", "Gap source lineage is invalid", "hypothesis_generation") from exc
        gap_context = {"id": gap.id, **proposal.model_dump()}
        base["context"]["gap"] = gap_context
        base["manifest"].append({"type": "gap", "id": gap.id, "content_hash": canonical_digest(gap_context),
                                  "selection_status": gap.selection_status})
        base.update({"gap": gap, "gap_claim_ids": set(proposal.claim_ids),
                     "gap_evidence_ids": set(proposal.evidence_for_ids + proposal.evidence_against_ids)})
        return base

    @staticmethod
    def _hypothesis_provider_context(info: dict[str, Any]) -> dict[str, Any]:
        context = info["context"]
        allowed_claims = info["gap_claim_ids"]
        allowed_evidence = info["gap_evidence_ids"]
        selected_items = set(context["gap"]["landscape_item_ids"])
        claims = [
            {**claim,
             "evidence_for_ids": [eid for eid in claim["evidence_for_ids"] if eid in allowed_evidence],
             "evidence_against_ids": [eid for eid in claim["evidence_against_ids"] if eid in allowed_evidence]}
            for claim in context["claims"] if claim["id"] in allowed_claims
        ]
        items = []
        for item in context["landscape"]["items"]:
            if item["id"] not in selected_items:
                continue
            item_claims = [cid for cid in item["claim_ids"] if cid in allowed_claims]
            claim_evidence = set().union(*(info["claim_sources"][cid] for cid in item_claims))
            items.append({"id": item["id"], "category": item["category"],
                          "claim_ids": item_claims,
                          "evidence_ids": sorted(set(item["evidence_ids"]) & allowed_evidence & claim_evidence)})
        return {"landscape": {"id": context["landscape"]["id"], "items": items},
                "claims": claims,
                "evidence": [row for row in context["evidence"] if row["id"] in allowed_evidence],
                "gap": context["gap"]}

    def _hypothesis_context(self, session: Session, project_id: str, revision: int, hypothesis_id: str) -> dict[str, Any]:
        hypothesis = session.scalar(select(Hypothesis).where(Hypothesis.id == hypothesis_id, Hypothesis.project_id == project_id))
        if hypothesis is None:
            raise ServiceError(404, "not_found", "Hypothesis not found", "experiment_plan_generation")
        if hypothesis.question_revision != revision or hypothesis.selection_status != "selected" or self._successor(session, Hypothesis, hypothesis_id):
            raise ServiceError(409, "invalid_state", "Hypothesis requires current human selection", "experiment_plan_generation")
        base = self._gap_context(session, project_id, revision, hypothesis.research_gap_id)
        proposal = HypothesisProposal.model_validate({
            "statement": hypothesis.statement, "rationale": hypothesis.rationale,
            "independent_variable": hypothesis.independent_variable, "dependent_variable": hypothesis.dependent_variable,
            "control_variables": hypothesis.control_variables, "falsification_condition": hypothesis.falsification_condition,
            "assumptions": hypothesis.assumptions, "uncertainty": hypothesis.uncertainty,
            "evidence_motivating_ids": [link.evidence_id for link in hypothesis.evidence_links if link.role == "motivates"],
            "evidence_challenging_ids": [link.evidence_id for link in hypothesis.evidence_links if link.role == "challenges"],
            "claim_ids": [link.claim_id for link in hypothesis.claim_links]})
        try:
            self.validate_hypothesis_sources(proposal, base["gap_evidence_ids"], base["gap_claim_ids"])
        except ValueError as exc:
            raise ServiceError(409, "invalid_provenance", "Hypothesis source lineage is invalid", "experiment_plan_generation") from exc
        hypothesis_context = {"id": hypothesis.id, **proposal.model_dump()}
        base["context"]["hypothesis"] = hypothesis_context
        base["manifest"].append({"type": "hypothesis", "id": hypothesis.id,
                                  "content_hash": canonical_digest(hypothesis_context),
                                  "selection_status": hypothesis.selection_status})
        base["hypothesis"] = hypothesis
        return base

    @staticmethod
    def _plan_provider_context(info: dict[str, Any]) -> dict[str, Any]:
        """Represent only inherited Hypothesis sources; keep authoritative info intact."""
        context = info["context"]
        hypothesis = context["hypothesis"]
        claims = set(hypothesis["claim_ids"])
        evidence = set(hypothesis["evidence_motivating_ids"] + hypothesis["evidence_challenging_ids"])
        selected_items = set(context["gap"]["landscape_item_ids"])
        payload = {
            "context_encoding": "plan-context-v1",
            "landscape": {"id": context["landscape"]["id"], "items": [
                {"id": row["id"], "category": row["category"],
                 "claim_ids": [cid for cid in row["claim_ids"] if cid in claims],
                 "evidence_ids": sorted(set(row["evidence_ids"]) & evidence &
                    set().union(*(info["claim_sources"][cid] for cid in row["claim_ids"] if cid in claims)))}
                for row in context["landscape"]["items"] if row["id"] in selected_items and
                (claims.intersection(row["claim_ids"]) or evidence.intersection(row["evidence_ids"]))]},
            "claims": [{**row,
                "evidence_for_ids": [eid for eid in row["evidence_for_ids"] if eid in evidence],
                "evidence_against_ids": [eid for eid in row["evidence_against_ids"] if eid in evidence]}
                for row in context["claims"] if row["id"] in claims],
            "evidence": [row for row in context["evidence"] if row["id"] in evidence],
            "gap": context["gap"], "hypothesis": hypothesis,
            "source_roles": {
                "hypothesis_claims": [{"claim_id": link.claim_id, "role": link.role}
                    for link in info["hypothesis"].claim_links],
                "hypothesis_evidence": [{"evidence_id": link.evidence_id, "role": link.role}
                    for link in info["hypothesis"].evidence_links],
                "gap_evidence": [{"evidence_id": link.evidence_id, "role": link.role}
                    for link in info["gap"].evidence_links if link.evidence_id in evidence]}}
        return deepcopy(payload)

    @staticmethod
    def _plan_visible_sources(context: dict[str, Any]) -> set[str]:
        # IDs merely mentioned in complete Gap reference arrays are not supplied rows.
        return ({context[key]["id"] for key in ("landscape", "gap", "hypothesis")} |
                {row["id"] for key in ("claims", "evidence") for row in context[key]} |
                {row["id"] for row in context["landscape"]["items"]})

    def _failed_plan(self, project_id: str, run_id: str, code: str, attempts: int) -> None:
        with self.session_factory() as session:
            session.execute(update(GenerationRun).where(
                GenerationRun.id == run_id, GenerationRun.project_id == project_id,
                GenerationRun.stage == "experiment_plan", GenerationRun.status == "started").values(
                status="failed", error_type=code, attempt_count=attempts, completed_at=now_utc()))
            session.commit()

    @diagnose_gap
    def _run_stage(self, project_id: str, revision: int, input_id: str, stage: str,
                   loader: Callable[[Session, str, int, str], dict[str, Any]], prompt: Prompt,
                   schema: type[BaseModel], assess_prompt: str,
                   validate: Callable[[BaseModel, dict[str, Any]], None],
                   persist: Callable[[Session, BaseModel, list[M4Assessment], str, dict[str, Any]], list[str]]) -> tuple[str, list[dict[str, object]]]:
        with self.session_factory() as session:
            project = self._project(session, project_id, revision)
            info = loader(session, project_id, revision, input_id)
            question = project.research_question
            context = {"research_question": question, **(
                self._plan_provider_context(info) if stage == "experiment_plan" else info["context"])}
            source_authority = _gap_source_authority(context) if stage == "gap" else None
            manifest = info["manifest"]
            gap_context = None
            if stage == "gap":
                try:
                    gap_context = GapContext(context, revision, self.settings.llm_max_context_chars)
                except ValueError as exc:
                    if isinstance(exc, GapCapacityError):
                        gap_event(layer="capacity", outcome="fail", category="candidate_capacity",
                                  measured_size=exc.measured_chars, limit=exc.limit,
                                  candidate_allowance=exc.candidate_limit)
                    raise ServiceError(422, "validation_error", str(exc), stage) from exc
                context = gap_context.context
                prompt = Prompt(prompt.name, prompt.version,
                    prompt.text + f"\nExact encoded candidate allowance: {gap_context.candidate_limit} Python representation characters."
                    " All fields and references share this allowance. Preserve scientific qualifications; infeasible candidates fail validation.", prompt.bundle)
            self._check_size(context, self.settings)
        with self.session_factory() as session:
            project = self._project(session, project_id, revision, lock=True)
            current = loader(session, project_id, revision, input_id)
            if project.research_question != question or current["manifest"] != manifest:
                raise ServiceError(409, "invalid_state", "Scientific input changed before Run creation", stage)
            if gap_context is not None and _gap_source_authority(
                    {"research_question": project.research_question, **current["context"]}) != source_authority:
                gap_event(layer="domain_validation", outcome="fail", category="source_authority")
                raise ServiceError(409, "invalid_state", "Complete Gap source changed before Run creation", stage)
            run = GenerationRun(project_id=project_id, stage=stage, status="started", provider="qwen",
                model=self.settings.qwen_model, prompt_name=prompt.name, prompt_version=prompt.version,
                prompt_hash=prompt.digest, schema_version="m4-gap-v2" if stage == "gap" else f"m4-{stage}-v1", research_question_snapshot=question,
                question_revision=revision, input_manifest=manifest,
                input_digest=canonical_digest({"question": question, "revision": revision, "inputs": manifest}),
                attempt_count=0)
            session.add(run)
            session.commit()
            run_id = run.id
        if stage == "gap":
            bind_gap_run(run_id)
            gap_event(layer="capacity", outcome="pass", context_chars=len(str(context)),
                      limit=gap_context.limit, candidate_allowance=gap_context.candidate_budget)
        round_validator = None
        if stage == "hypothesis":
            def round_validator(value: BaseModel, repair_round: int) -> None:
                self._validate_hypothesis_batch(
                    value, info, info["context"]["requested_count"],
                    run_id=run_id, repair_round=repair_round)
        def validate_generated(value: BaseModel) -> None:
            validate(value, info)
            if gap_context is not None:
                for index, candidate in enumerate(value.gaps):
                    try:
                        gap_context.validate_candidate(candidate.model_dump())
                    except GapCapacityError as error:
                        gap_capacity(gap_context, candidate.model_dump(), index, error)
                        raise
                    gap_capacity(gap_context, candidate.model_dump(), index)

        fail_run = (lambda rid, code, count: self._failed_plan(project_id, rid, code, count)) if stage == "experiment_plan" else self._failed
        failure_options = {"failure_handler": fail_run} if stage == "experiment_plan" else {}

        output, attempts = self._generate(run_id, prompt, schema, context, validate_generated,
            repair_instruction=prompt.bundle["structured_repair"],
            trace_stage="hypothesis_batch" if stage == "hypothesis" else "gap_generation" if stage == "gap" else None,
            trace_validator="_validate_hypothesis_batch" if stage == "hypothesis" else None,
            round_validator=round_validator, **failure_options)
        proposals = output.gaps if stage == "gap" else output.hypotheses if stage == "hypothesis" else [output]
        allowed_sources = set(info.get("items", {})) | info["claim_ids"] | info["evidence_ids"]
        allowed_sources.add(info["landscape"].id)
        if "gap" in info:
            allowed_sources.add(info["gap"].id)
        if "hypothesis" in info:
            allowed_sources.add(info["hypothesis"].id)
        if stage == "experiment_plan":
            allowed_sources &= self._plan_visible_sources(context)
        assessments: list[M4Assessment] = []
        for candidate_index, proposed in enumerate(proposals):
            assess_context = (gap_context.assessment_context(proposed.model_dump()) if gap_context is not None
                              else {"scientific_input": context, "proposal": proposed.model_dump()})
            self._check_run_context(run_id, assess_context, attempts, **failure_options)
            def validate_advisory(value):
                try:
                    self._validate_assessment(value, allowed_sources)
                except ValueError:
                    if stage == "gap":
                        gap_event(layer="domain_validation", outcome="fail", category="advisory_source_reference", validation_code="gap_advisory_source_reference")
                    raise
            assessment, attempts = self._generate(run_id, Prompt(f"{stage}_assessment", prompt.version, assess_prompt, {}),
                M4Assessment, assess_context,
                validate_advisory,
                trace_stage=f"gap_assessment[{candidate_index}]" if stage == "gap" else None,
                prior_attempts=attempts, repair_instruction=GAP_ASSESSMENT_REPAIR if stage == "gap" else
                PLAN_ASSESSMENT_REPAIR if stage == "experiment_plan" else M4_ASSESSMENT_REPAIR, **failure_options)
            assessments.append(assessment)
        try:
            with self.session_factory() as session:
                project = self._project(session, project_id, revision, lock=True)
                if stage in {"hypothesis", "experiment_plan"}:
                    run = session.scalar(select(GenerationRun).where(
                        GenerationRun.id == run_id, GenerationRun.project_id == project_id,
                        GenerationRun.stage == stage).with_for_update())
                    if run is None or run.status != "started":
                        raise ServiceError(409, "generation_superseded", "Generation Run is no longer active", stage)
                current = loader(session, project_id, revision, input_id)
                if project.research_question != question or current["manifest"] != manifest:
                    raise ServiceError(409, "invalid_state", "Scientific input changed during generation", stage)
                if gap_context is not None and _gap_source_authority(
                        {"research_question": project.research_question, **current["context"]}) != source_authority:
                    gap_event(layer="domain_validation", outcome="fail", category="source_authority")
                    raise ServiceError(409, "invalid_state", "Complete Gap source changed during generation", stage)
                validate(output, current)
                if stage == "gap":
                    gap_event(layer="persistence", outcome="start", legacy_cumulative_attempts=attempts)
                ids = persist(session, output, assessments, run_id, current)
                if stage in {"hypothesis", "experiment_plan"}:
                    transitioned = session.execute(update(GenerationRun).where(
                        GenerationRun.id == run_id, GenerationRun.project_id == project_id,
                        GenerationRun.stage == stage, GenerationRun.status == "started").values(
                        status="succeeded", attempt_count=attempts, completed_at=now_utc()))
                    if transitioned.rowcount != 1:
                        raise ServiceError(409, "generation_superseded", "Generation Run is no longer active", stage)
                else:
                    run = session.get(GenerationRun, run_id)
                    run.status = "succeeded"
                    run.attempt_count = attempts
                    run.completed_at = now_utc()
                session.flush()
                if stage == "gap":
                    rows = [self._gap_read(session, session.get(ResearchGap, item_id)) for item_id in ids]
                elif stage == "hypothesis":
                    rows = [self._hypothesis_read(session, session.get(Hypothesis, item_id)) for item_id in ids]
                else:
                    rows = [self._plan_read(session, session.get(ExperimentPlan, item_id)) for item_id in ids]
                session.commit()
                return run_id, rows
        except ServiceError as exc:
            fail_run(run_id, exc.code, attempts)
            exc.details["generation_run_id"] = run_id
            raise
        except SQLAlchemyError as exc:
            fail_run(run_id, "database_failed", attempts)
            raise ServiceError(500, "database_failed", "M4 scientific batch could not be saved", stage,
                               {"generation_run_id": run_id}) from exc

    @staticmethod
    def _validate_assessment(assessment: M4Assessment, allowed_sources: set[str]) -> None:
        if len(assessment.source_ids) != len(set(assessment.source_ids)) or not set(assessment.source_ids) <= allowed_sources:
            raise ValueError("Assessment referenced a foreign source")

    @staticmethod
    def _store_gap_links(session: Session, gap_id: str, proposal: GapProposal) -> None:
        session.add_all([GapLandscapeItem(gap_id=gap_id, item_id=item_id) for item_id in proposal.landscape_item_ids])
        session.add_all([GapClaim(gap_id=gap_id, claim_id=claim_id) for claim_id in proposal.claim_ids])
        session.add_all([GapEvidence(gap_id=gap_id, evidence_id=eid, role=role)
                         for role, ids in (("supports", proposal.evidence_for_ids), ("challenges", proposal.evidence_against_ids))
                         for eid in ids])

    @staticmethod
    def _store_hypothesis_links(session: Session, hypothesis_id: str, proposal: HypothesisProposal,
                                claim_sources: dict[str, set[str]]) -> None:
        session.add_all([HypothesisEvidence(hypothesis_id=hypothesis_id, evidence_id=eid, role=role)
                         for role, ids in (("motivates", proposal.evidence_motivating_ids),
                                           ("challenges", proposal.evidence_challenging_ids)) for eid in ids])
        challenging = set(proposal.evidence_challenging_ids)
        session.add_all([HypothesisClaim(hypothesis_id=hypothesis_id, claim_id=cid,
            role="challenges" if claim_sources[cid] & challenging else "extends") for cid in proposal.claim_ids])

    def generate_gaps(self, project_id: str, landscape_id: str, revision: int) -> dict[str, object]:
        def validate(output: GapBatch, info: dict[str, Any]) -> None:
            for index, gap in enumerate(output.gaps):
                try:
                    self.validate_gap_sources(gap, info["items"], info["claim_sources"])
                except ValueError as exc:
                    gap_event(layer="domain_validation", outcome="fail", category="source_lineage", candidate_index=index)
                    if type(exc) is GapLineageError:
                        raise exc.at_candidate(index) from exc
                    raise

        def persist(session: Session, output: GapBatch, assessments: list[M4Assessment], run_id: str,
                    info: dict[str, Any]) -> list[str]:
            ids = []
            for proposed, assessment in zip(output.gaps, assessments, strict=True):
                gap = ResearchGap(project_id=project_id, landscape_id=landscape_id,
                    **proposed.model_dump(exclude={"landscape_item_ids", "claim_ids", "evidence_for_ids", "evidence_against_ids"}),
                    ai_assessment=assessment.model_dump(), selection_status="candidate",
                    question_revision=revision, origin="model", generation_run_id=run_id)
                session.add(gap)
                session.flush()
                self._store_gap_links(session, gap.id, proposed)
                ids.append(gap.id)
            return ids
        run_id, rows = self._run_stage(project_id, revision, landscape_id, "gap", self._landscape_context,
            GAP_PROMPT, GapBatch, GAP_ASSESS, validate, persist)
        return {"generation_run_id": run_id, "gaps": rows}

    def generate_hypotheses(self, project_id: str, gap_id: str, revision: int, count: int = 3) -> dict[str, object]:
        if not 1 <= count <= 5:
            raise ServiceError(422, "validation_error", "Hypothesis count must be 1 to 5", "hypothesis_generation")

        def loader(session: Session, pid: str, rev: int, gid: str) -> dict[str, Any]:
            info = self._gap_context(session, pid, rev, gid)
            info["context"] = self._hypothesis_provider_context(info)
            info["context"]["requested_count"] = count
            info["manifest"].append({"type": "generation_options", "count": count})
            return info

        def validate(output: HypothesisBatch, info: dict[str, Any]) -> None:
            self._validate_hypothesis_batch(output, info, count)

        def persist(session: Session, output: HypothesisBatch, assessments: list[M4Assessment], run_id: str,
                    info: dict[str, Any]) -> list[str]:
            ids = []
            for proposed, assessment in zip(output.hypotheses, assessments, strict=True):
                hypothesis = Hypothesis(project_id=project_id, research_gap_id=gap_id,
                    **proposed.model_dump(exclude={"evidence_motivating_ids", "evidence_challenging_ids", "claim_ids"}),
                    ai_assessment=assessment.model_dump(), selection_status="candidate",
                    question_revision=revision, origin="model", generation_run_id=run_id)
                session.add(hypothesis)
                session.flush()
                self._store_hypothesis_links(session, hypothesis.id, proposed, info["claim_sources"])
                ids.append(hypothesis.id)
            return ids
        run_id, rows = self._run_stage(project_id, revision, gap_id, "hypothesis", loader,
            HYPOTHESIS_PROMPT, HypothesisBatch, HYPOTHESIS_ASSESS, validate, persist)
        return {"generation_run_id": run_id, "hypotheses": rows}

    def generate_plan(self, project_id: str, hypothesis_id: str, revision: int) -> dict[str, object]:
        def validate(output: PlanProposal, info: dict[str, Any]) -> None:
            # The schema guarantees presence; the bounded AI assessment and human review judge semantic alignment.
            return None

        def persist(session: Session, output: PlanProposal, assessments: list[M4Assessment], run_id: str,
                    info: dict[str, Any]) -> list[str]:
            plan = ExperimentPlan(hypothesis_id=hypothesis_id, **output.model_dump(),
                ai_alignment_assessment=assessments[0].model_dump(), review_status="pending_review",
                question_revision=revision, origin="model", generation_run_id=run_id)
            session.add(plan)
            session.flush()
            return [plan.id]
        run_id, rows = self._run_stage(project_id, revision, hypothesis_id, "experiment_plan", self._hypothesis_context,
            PLAN_PROMPT, PlanProposal, PLAN_ASSESS, validate, persist)
        return {"generation_run_id": run_id, "plan": rows[0]}

    def _gap_read(self, session: Session, gap: ResearchGap) -> dict[str, object]:
        project = self._project(session, gap.project_id)
        eligible = (gap.question_revision == project.question_revision and gap.selection_status == "selected" and
                    not self._successor(session, ResearchGap, gap.id))
        if eligible:
            try:
                self._gap_context(session, gap.project_id, gap.question_revision, gap.id)
            except ServiceError:
                eligible = False
        return {"id": gap.id, "project_id": gap.project_id, "landscape_id": gap.landscape_id,
                "title": gap.title, "description": gap.description, "gap_type": gap.gap_type,
                "why_it_matters": gap.why_it_matters, "uncertainty": gap.uncertainty,
                "assumptions": gap.assumptions, "novelty_scope": gap.novelty_scope,
                "landscape_item_ids": sorted(link.item_id for link in gap.item_links),
                "claim_ids": sorted(link.claim_id for link in gap.claim_links),
                "evidence_for_ids": sorted(link.evidence_id for link in gap.evidence_links if link.role == "supports"),
                "evidence_against_ids": sorted(link.evidence_id for link in gap.evidence_links if link.role == "challenges"),
                "ai_assessment": gap.ai_assessment, "selection_status": gap.selection_status,
                "question_revision": gap.question_revision, "origin": gap.origin,
                "supersedes_id": gap.supersedes_id, "generation_run_id": gap.generation_run_id,
                "eligible_for_generation": eligible, "created_at": gap.created_at}

    def list_gaps(self, project_id: str) -> list[dict[str, object]]:
        with self.session_factory() as session:
            self._project(session, project_id)
            return [self._gap_read(session, row) for row in session.scalars(select(ResearchGap).where(
                ResearchGap.project_id == project_id).order_by(ResearchGap.created_at, ResearchGap.id))]

    def get_gap(self, project_id: str, gap_id: str) -> dict[str, object]:
        with self.session_factory() as session:
            self._project(session, project_id)
            row = session.scalar(select(ResearchGap).where(ResearchGap.id == gap_id, ResearchGap.project_id == project_id))
            if row is None:
                raise ServiceError(404, "not_found", "ResearchGap not found", "gap")
            return self._gap_read(session, row)

    def select_gap(self, project_id: str, gap_id: str, status: str, revision: int) -> dict[str, object]:
        with self.session_factory() as session:
            self._project(session, project_id, revision, lock=True)
            gap = session.scalar(select(ResearchGap).where(ResearchGap.id == gap_id, ResearchGap.project_id == project_id).with_for_update())
            if gap is None:
                raise ServiceError(404, "not_found", "ResearchGap not found", "gap_selection")
            if gap.question_revision != revision or self._successor(session, ResearchGap, gap_id):
                raise ServiceError(409, "superseded_resource", "Gap is not current", "gap_selection")
            if status == "selected":
                info = self._landscape_context(session, project_id, revision, gap.landscape_id)
                try:
                    self.validate_gap_sources(self._gap_proposal(gap), info["items"], info["claim_sources"])
                except (ValidationError, ValueError) as exc:
                    raise ServiceError(409, "invalid_provenance", "Gap source lineage is invalid", "gap_selection") from exc
            gap.selection_status = status
            session.commit()
        return self.get_gap(project_id, gap_id)

    def revise_gap(self, project_id: str, gap_id: str, revision: int, proposed: GapProposal) -> dict[str, object]:
        with self.session_factory() as session:
            self._project(session, project_id, revision, lock=True)
            prior = session.scalar(select(ResearchGap).where(ResearchGap.id == gap_id, ResearchGap.project_id == project_id).with_for_update())
            if prior is None:
                raise ServiceError(404, "not_found", "ResearchGap not found", "gap_revision")
            if prior.question_revision != revision or self._successor(session, ResearchGap, gap_id):
                raise ServiceError(409, "superseded_resource", "Gap is not current", "gap_revision")
            info = self._landscape_context(session, project_id, revision, prior.landscape_id)
            try:
                self.validate_gap_sources(proposed, info["items"], info["claim_sources"])
            except ValueError as exc:
                raise ServiceError(422, "invalid_reference", "Gap source relationship is invalid", "gap_revision") from exc
            revised = ResearchGap(project_id=project_id, landscape_id=prior.landscape_id,
                **proposed.model_dump(exclude={"landscape_item_ids", "claim_ids", "evidence_for_ids", "evidence_against_ids"}),
                selection_status="candidate", question_revision=revision, origin="human_revision", supersedes_id=gap_id)
            session.add(revised)
            try:
                session.flush()
                self._store_gap_links(session, revised.id, proposed)
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise ServiceError(409, "superseded_resource", "Gap already revised", "gap_revision") from exc
            return self._gap_read(session, revised)

    def _hypothesis_read(self, session: Session, hypothesis: Hypothesis) -> dict[str, object]:
        project = self._project(session, hypothesis.project_id)
        eligible = (hypothesis.question_revision == project.question_revision and hypothesis.selection_status == "selected" and
                    not self._successor(session, Hypothesis, hypothesis.id))
        if eligible:
            try:
                self._hypothesis_context(session, hypothesis.project_id, hypothesis.question_revision, hypothesis.id)
            except ServiceError:
                eligible = False
        return {"id": hypothesis.id, "project_id": hypothesis.project_id,
                "research_gap_id": hypothesis.research_gap_id, "statement": hypothesis.statement,
                "rationale": hypothesis.rationale, "independent_variable": hypothesis.independent_variable,
                "dependent_variable": hypothesis.dependent_variable, "control_variables": hypothesis.control_variables,
                "falsification_condition": hypothesis.falsification_condition, "assumptions": hypothesis.assumptions,
                "uncertainty": hypothesis.uncertainty, "ai_assessment": hypothesis.ai_assessment,
                "evidence_motivating_ids": sorted(link.evidence_id for link in hypothesis.evidence_links if link.role == "motivates"),
                "evidence_challenging_ids": sorted(link.evidence_id for link in hypothesis.evidence_links if link.role == "challenges"),
                "claim_ids": sorted(link.claim_id for link in hypothesis.claim_links),
                "selection_status": hypothesis.selection_status, "question_revision": hypothesis.question_revision,
                "origin": hypothesis.origin, "supersedes_id": hypothesis.supersedes_id,
                "generation_run_id": hypothesis.generation_run_id, "eligible_for_generation": eligible,
                "created_at": hypothesis.created_at}

    def list_hypotheses(self, project_id: str, gap_id: str | None = None) -> list[dict[str, object]]:
        with self.session_factory() as session:
            self._project(session, project_id)
            query = select(Hypothesis).where(Hypothesis.project_id == project_id)
            if gap_id:
                query = query.where(Hypothesis.research_gap_id == gap_id)
            return [self._hypothesis_read(session, row) for row in session.scalars(query.order_by(Hypothesis.created_at, Hypothesis.id))]

    def get_hypothesis(self, project_id: str, hypothesis_id: str) -> dict[str, object]:
        with self.session_factory() as session:
            self._project(session, project_id)
            row = session.scalar(select(Hypothesis).where(Hypothesis.id == hypothesis_id, Hypothesis.project_id == project_id))
            if row is None:
                raise ServiceError(404, "not_found", "Hypothesis not found", "hypothesis")
            return self._hypothesis_read(session, row)

    def select_hypothesis(self, project_id: str, hypothesis_id: str, status: str, revision: int) -> dict[str, object]:
        with self.session_factory() as session:
            self._project(session, project_id, revision, lock=True)
            hypothesis = session.scalar(select(Hypothesis).where(Hypothesis.id == hypothesis_id,
                Hypothesis.project_id == project_id).with_for_update())
            if hypothesis is None:
                raise ServiceError(404, "not_found", "Hypothesis not found", "hypothesis_selection")
            if hypothesis.question_revision != revision or self._successor(session, Hypothesis, hypothesis_id):
                raise ServiceError(409, "superseded_resource", "Hypothesis is not current", "hypothesis_selection")
            if status == "selected":
                self._gap_context(session, project_id, revision, hypothesis.research_gap_id)
            hypothesis.selection_status = status
            session.commit()
        return self.get_hypothesis(project_id, hypothesis_id)

    def revise_hypothesis(self, project_id: str, hypothesis_id: str, revision: int,
                          proposed: HypothesisProposal) -> dict[str, object]:
        with self.session_factory() as session:
            self._project(session, project_id, revision, lock=True)
            prior = session.scalar(select(Hypothesis).where(Hypothesis.id == hypothesis_id,
                Hypothesis.project_id == project_id).with_for_update())
            if prior is None:
                raise ServiceError(404, "not_found", "Hypothesis not found", "hypothesis_revision")
            if prior.question_revision != revision or self._successor(session, Hypothesis, hypothesis_id):
                raise ServiceError(409, "superseded_resource", "Hypothesis is not current", "hypothesis_revision")
            info = self._gap_context(session, project_id, revision, prior.research_gap_id)
            try:
                self.validate_hypothesis_sources(proposed, info["gap_evidence_ids"], info["gap_claim_ids"])
            except ValueError as exc:
                raise ServiceError(422, "invalid_reference", "Hypothesis source relationship is invalid", "hypothesis_revision") from exc
            revised = Hypothesis(project_id=project_id, research_gap_id=prior.research_gap_id,
                **proposed.model_dump(exclude={"evidence_motivating_ids", "evidence_challenging_ids", "claim_ids"}),
                selection_status="candidate", question_revision=revision, origin="human_revision", supersedes_id=hypothesis_id)
            session.add(revised)
            try:
                session.flush()
                self._store_hypothesis_links(session, revised.id, proposed, info["claim_sources"])
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise ServiceError(409, "superseded_resource", "Hypothesis already revised", "hypothesis_revision") from exc
            return self._hypothesis_read(session, revised)

    def _plan_read(self, session: Session, plan: ExperimentPlan) -> dict[str, object]:
        hypothesis = session.get(Hypothesis, plan.hypothesis_id)
        project = self._project(session, hypothesis.project_id)
        eligible = (plan.question_revision == project.question_revision and plan.review_status == "accepted" and
                    not self._successor(session, ExperimentPlan, plan.id))
        if eligible:
            try:
                self._hypothesis_context(session, hypothesis.project_id, plan.question_revision, plan.hypothesis_id)
            except ServiceError:
                eligible = False
        fields = ("id", "hypothesis_id", "objective", "independent_variable", "dependent_variable", "control_variables",
                  "experimental_groups", "baseline", "dataset_requirements", "metrics", "procedure", "analysis_approach",
                  "expected_observations", "falsification_criteria", "confounders", "limitations", "assumptions",
                  "uncertainty", "ai_alignment_assessment", "review_status", "reviewed_at", "question_revision",
                  "origin", "supersedes_id", "generation_run_id", "created_at")
        return {**{field: getattr(plan, field) for field in fields}, "eligible_for_downstream": eligible}

    def list_plans(self, project_id: str, hypothesis_id: str | None = None) -> list[dict[str, object]]:
        with self.session_factory() as session:
            self._project(session, project_id)
            query = select(ExperimentPlan).join(Hypothesis, ExperimentPlan.hypothesis_id == Hypothesis.id).where(
                Hypothesis.project_id == project_id)
            if hypothesis_id:
                query = query.where(ExperimentPlan.hypothesis_id == hypothesis_id)
            return [self._plan_read(session, row) for row in session.scalars(query.order_by(ExperimentPlan.created_at, ExperimentPlan.id))]

    def get_plan(self, project_id: str, plan_id: str) -> dict[str, object]:
        with self.session_factory() as session:
            self._project(session, project_id)
            plan = session.scalar(select(ExperimentPlan).join(Hypothesis, ExperimentPlan.hypothesis_id == Hypothesis.id).where(
                ExperimentPlan.id == plan_id, Hypothesis.project_id == project_id))
            if plan is None:
                raise ServiceError(404, "not_found", "ExperimentPlan not found", "experiment_plan")
            return self._plan_read(session, plan)

    def review_plan(self, project_id: str, plan_id: str, status: str, revision: int) -> dict[str, object]:
        with self.session_factory() as session:
            self._project(session, project_id, revision, lock=True)
            plan = session.scalar(select(ExperimentPlan).join(Hypothesis, ExperimentPlan.hypothesis_id == Hypothesis.id).where(
                ExperimentPlan.id == plan_id, Hypothesis.project_id == project_id).with_for_update(of=ExperimentPlan))
            if plan is None:
                raise ServiceError(404, "not_found", "ExperimentPlan not found", "plan_review")
            if plan.question_revision != revision or self._successor(session, ExperimentPlan, plan_id):
                raise ServiceError(409, "superseded_resource", "Plan is not current", "plan_review")
            if status == "accepted":
                self._hypothesis_context(session, project_id, revision, plan.hypothesis_id)
            plan.review_status = status
            plan.reviewed_at = now_utc()
            session.commit()
        return self.get_plan(project_id, plan_id)

    def revise_plan(self, project_id: str, plan_id: str, revision: int, proposed: PlanProposal) -> dict[str, object]:
        with self.session_factory() as session:
            self._project(session, project_id, revision, lock=True)
            prior = session.scalar(select(ExperimentPlan).join(Hypothesis, ExperimentPlan.hypothesis_id == Hypothesis.id).where(
                ExperimentPlan.id == plan_id, Hypothesis.project_id == project_id).with_for_update(of=ExperimentPlan))
            if prior is None:
                raise ServiceError(404, "not_found", "ExperimentPlan not found", "plan_revision")
            if prior.question_revision != revision or self._successor(session, ExperimentPlan, plan_id):
                raise ServiceError(409, "superseded_resource", "Plan is not current", "plan_revision")
            self._hypothesis_context(session, project_id, revision, prior.hypothesis_id)
            revised = ExperimentPlan(hypothesis_id=prior.hypothesis_id, **proposed.model_dump(),
                review_status="pending_review", question_revision=revision, origin="human_revision", supersedes_id=plan_id)
            session.add(revised)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise ServiceError(409, "superseded_resource", "Plan already revised", "plan_revision") from exc
            return self._plan_read(session, revised)
