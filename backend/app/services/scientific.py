"""M3 Claim/Landscape lifecycle with short transactions and source validation."""

import json
import logging
import re
from collections.abc import Callable, Sequence
from contextlib import nullcontext
from hashlib import sha256
from typing import get_args

from pydantic import BaseModel, ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, joinedload, selectinload

from app.config.settings import Settings
from app.persistence.database import get_engine
from app.persistence.models import (
    Claim, ClaimEvidence, Evidence, GenerationRun, LandscapeItem, LandscapeItemClaim,
    LandscapeItemEvidence, PaperChunk, Project, ResearchLandscape, now_utc,
)
from app.prompts.scientific import (
    CLAIM_PROMPT, LANDSCAPE_PROMPT, CLAIM_VERIFY, CONFLICT_VERIFY, SUMMARY_VERIFY,
    CLAIM_VERIFIER_REPAIR, CONFLICT_REPAIR, SUMMARY_REPAIR, STRUCTURED_REPAIR, LANDSCAPE_VERIFIER_REPAIR, Prompt, canonical_digest,
)
from app.providers.interfaces import LLMProvider
from app.providers.llm import LLMFormatError, LLMProviderError, LLMTimeout, trace_provider_calls
from app.services.errors import ServiceError
from app.services.landscape_budget import LandscapeBudget, LandscapeBudgetExceeded, assessment_payload, summary_source_context
from app.services.scientific_schemas import (
    ClaimBatch, ClaimRiskFlag, ClaimVerification, ConflictVerification, HumanClaimRevision, LandscapeOutput,
    LandscapeItemProposal, LandscapeItemVerification, LandscapeSummaryVerification, SummaryVerification, SummaryRepair,
)


CLAIM_SCHEMA_VERSION = "m3-claim-v5"
LANDSCAPE_SCHEMA_VERSION = "m3-landscape-v9"
STRUCTURED_OUTPUT_MAX_CALLS = 2  # Initial output plus at most one validation repair.
_CLAIM_TRACE_LOG = logging.getLogger("uvicorn.error")
_LANDSCAPE_TRACE_FIELDS = frozenset({
    "layer", "outcome", "category", "error_type", "error_path", "validation_code",
    "validator", "source", "attempt", "provider_attempt", "provider_attempts",
    "cumulative_attempts", "duration_ms", "http_status", "response_length",
    "response_sha256", "reason", "candidate_index", "conflict_index",
    "verification_round", "provider_role", "persisted_landscape_count", "verdict",
    "size_budget", "measured_size", "context_chars", "generated_output_chars",
    "repair_attempted", "persistence_occurred",
    "reserved_context_chars",
    "cumulative_cap", "immutable_prior_chars", "current_candidate_chars", "server_overhead_chars",
    "list_overhead_chars", "later_reservation_chars", "current_response_allowance",
    "minimum_response_chars", "minimum_required_chars", "remaining_items", "required_reduction_chars",
    "assessment_encoding_version",
    "logical_call_id", "transport_diagnostic",
})
_DOMAIN_ERROR_CODES = {
    "Report source reference is not whitelisted": "report_source_not_allowed",
    "Report narrative contains source identity; citations come from source_refs": "report_identity_in_prose",
    "Report narrative overclaims absence of an effect": "report_absolute_null",
    "Report narrative null-result claim conflicts with accepted interpretation": "report_interpretation_mismatch",
    "Report narrative contains numerical text": "report_numeric_prose",
    "Report narrative overclaims scientific authority": "report_overclaim",
    "AI prose must not reproduce numerical results": "interpretation_numeric_prose",
    "AI prose overstates the statistical evidence": "interpretation_overclaim",
    "Interpretation must reference the complete result and figure set": "interpretation_source_set_mismatch",
    "Claim source ID is outside the validated input": "source_not_allowed",
    "Verifier referenced an invalid Evidence ID": "verifier_source_invalid",
    "Verifier excerpt referenced an invalid Evidence ID": "verifier_excerpt_source_invalid",
    "Direct support cannot retain material scientific risk flags": "direct_support_risk_flags",
    "Direct support reference must come from supporting Evidence": "direct_support_wrong_source",
    "Landscape source ID is outside the validated input": "source_not_allowed",
    "Landscape Evidence is not linked to cited Claims": "evidence_not_linked_to_claim",
    "Conflict requires independently cited Evidence for each Claim": "conflict_evidence_insufficient",
    "Verifier excerpt is not an exact source substring": "verifier_excerpt_mismatch",
    "Landscape support reference must come from supporting Evidence": "landscape_support_wrong_source",
    "Direct conflict references must independently cover each Claim": "conflict_reference_coverage_invalid",
}
_ALL_CLAIM_RISKS = frozenset(get_args(ClaimRiskFlag))
_EVIDENCE_LIMITATIONS = frozenset({
    "methodological_limitation", "statistical_evidence_limitation", "generalizability_limitation",
})
_CLAIM_RISK_COMPATIBILITY = {
    "directly_supported": _EVIDENCE_LIMITATIONS,
    "inferentially_supported": _ALL_CLAIM_RISKS,
    "unsupported": _ALL_CLAIM_RISKS,
    "contradicted": _ALL_CLAIM_RISKS,
    "insufficient_evidence": _ALL_CLAIM_RISKS,
}


def _hash_text(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


from app.services.gap_diagnostics import diagnose_gap_call, gap_event, gap_invocation, gap_provider_event
from app.services.gap_lineage import GapRepairFeedback


class ScientificService:
    def __init__(self, settings: Settings, provider: LLMProvider, session_factory: Callable[[], Session] | None = None,
                 *, trace_sink: Callable[[dict[str, object]], None] | None = None):
        self.settings = settings
        self.provider = provider
        self.session_factory = session_factory or (lambda: Session(get_engine(), expire_on_commit=False))
        self.trace_sink = trace_sink

    def _claim_trace(self, run_id: str, stage: str, repair_round: int | None, **fields: object) -> None:
        if stage.startswith("gap_"):
            gap_provider_event(fields, repair_round)
            return
        if stage.startswith(("landscape_generation", "conflict_verification", "summary_verification")):
            self._landscape_trace(run_id, stage, repair_round, **fields)
            return
        event = {"generation_run_id": run_id, "stage": stage, "repair_round": repair_round, **fields}
        if stage.startswith("claim_verification["):
            event["candidate_index"] = int(stage.removeprefix("claim_verification[").removesuffix("]"))
        if self.trace_sink is not None:
            self.trace_sink(event)
        else:
            _CLAIM_TRACE_LOG.info("claim_trace %s", json.dumps(event, sort_keys=True))

    def _landscape_trace(self, run_id: str, stage: str, repair_round: int | None, **fields: object) -> None:
        event = {"generation_run_id": run_id, "stage": stage, "repair_round": repair_round,
                 **{key: value for key, value in fields.items() if key in _LANDSCAPE_TRACE_FIELDS}}
        if event.get("error_type") == "extra_forbidden" or event.get("validation_code") == "extra_forbidden":
            event["error_path"] = "<extra_field>"
        if stage.startswith("conflict_verification["):
            event["conflict_index"] = int(stage.split("[", 1)[1].split("]", 1)[0])
        if self.trace_sink is not None:
            self.trace_sink(event)
        else:
            _CLAIM_TRACE_LOG.info("landscape_trace %s", json.dumps(event, sort_keys=True))

    @staticmethod
    def _project(session: Session, project_id: str, revision: int | None = None, *, lock: bool = False) -> Project:
        query = select(Project).where(Project.id == project_id)
        if lock:
            query = query.with_for_update()
        project = session.scalar(query)
        if project is None:
            raise ServiceError(404, "not_found", "Project not found", "scientific")
        if revision is not None and project.question_revision != revision:
            raise ServiceError(409, "stale_revision", "Research question revision is stale", "scientific")
        return project

    @staticmethod
    def _check_size(context: dict[str, object], settings: Settings) -> None:
        if len(str(context)) > settings.llm_max_context_chars:
            raise ServiceError(422, "validation_error", "Selected scientific context exceeds the configured limit", "scientific")

    def _check_run_context(self, run_id: str, context: dict[str, object], attempts: int, *,
                           failure_handler: Callable[[str, str, int], None] | None = None) -> None:
        try:
            self._check_size(context, self.settings)
        except ServiceError as exc:
            (failure_handler or self._failed)(run_id, exc.code, attempts)
            exc.details["generation_run_id"] = run_id
            raise

    def _check_landscape_context(self, run_id: str, budget: LandscapeBudget, context: dict[str, object],
                                 attempts: int, stage: str, *, repair_attempted: bool = False) -> None:
        try:
            budget.check_context(stage, context)
            if "rejected_conflict" in context:
                budget.check_critique({"rejected_conflict": context["rejected_conflict"]})
            elif "flagged_statements" in context:
                budget.check_critique({key: context[key] for key in ("flagged_statements", "qualifications")})
        except LandscapeBudgetExceeded as exc:
            error = exc.service_error(repair_attempted=repair_attempted, context_chars=len(str(context)))
            self._failed(run_id, error.code, attempts)
            self._landscape_trace(run_id, stage, None, layer="context_budget", outcome="fail", **error.details)
            error.details["generation_run_id"] = run_id
            raise error from exc
        self._check_run_context(run_id, context, attempts)

    def _evidence_input(self, session: Session, project_id: str, revision: int, ids: Sequence[str]) -> tuple[list[Evidence], list[dict[str, object]], list[dict[str, object]]]:
        if not ids:
            raise ServiceError(424, "insufficient_evidence", "Select Evidence before Claim generation", "claim_generation")
        if len(ids) != len(set(ids)) or len(ids) > self.settings.llm_max_input_items:
            raise ServiceError(422, "validation_error", "Invalid Evidence input list", "claim_generation")
        rows = list(session.scalars(select(Evidence).where(Evidence.id.in_(ids), Evidence.project_id == project_id)
                                    .options(joinedload(Evidence.chunk).joinedload(PaperChunk.paper))))
        if len(rows) != len(ids):
            raise ServiceError(422, "invalid_reference", "Evidence input is not in this Project", "claim_generation")
        by_id = {row.id: row for row in rows}
        ordered = [by_id[item_id] for item_id in sorted(ids)]
        context = []
        manifest = []
        for item in ordered:
            if item.question_revision != revision or item.selection_status != "selected":
                raise ServiceError(409, "invalid_state", "Evidence is no longer selected in this revision", "claim_generation")
            chunk = item.chunk
            paper = chunk.paper
            if paper.project_id != project_id or item.source_text not in chunk.text:
                raise ServiceError(422, "invalid_provenance", "Evidence source relationship is invalid", "claim_generation")
            context.append({"id": item.id, "source_text": item.source_text, "evidence_type": item.evidence_type,
                            "paper_id": paper.id, "paper_title": paper.title, "page_number": chunk.page_number,
                            "section": chunk.section})
            manifest.append({"type": "evidence", "id": item.id, "source_hash": _hash_text(item.source_text),
                             "selection_status": item.selection_status, "paper_chunk_id": chunk.id,
                             "paper_id": paper.id, "page_number": chunk.page_number})
        return ordered, context, manifest

    def _claim_input(self, session: Session, project_id: str, revision: int, ids: Sequence[str]) -> tuple[list[Claim], dict[str, object], list[dict[str, object]], set[str]]:
        if not ids:
            raise ServiceError(424, "insufficient_evidence", "Select Claims before Landscape generation", "landscape_generation")
        if len(ids) != len(set(ids)) or len(ids) > self.settings.llm_max_input_items:
            raise ServiceError(422, "validation_error", "Invalid Claim input list", "landscape_generation")
        rows = list(session.scalars(select(Claim).where(Claim.id.in_(ids), Claim.project_id == project_id)
                                    .options(selectinload(Claim.evidence_links))))
        if len(rows) != len(ids):
            raise ServiceError(422, "invalid_reference", "Claim input is not in this Project", "landscape_generation")
        by_id = {row.id: row for row in rows}
        ordered = [by_id[item_id] for item_id in sorted(ids)]
        all_evidence_ids = {link.evidence_id for claim in ordered for link in claim.evidence_links}
        evidence_rows = list(session.scalars(select(Evidence).where(Evidence.id.in_(all_evidence_ids))
                                             .options(joinedload(Evidence.chunk).joinedload(PaperChunk.paper))))
        evidences = {item.id: item for item in evidence_rows}
        context_claims = []
        manifest = []
        for claim in ordered:
            successor = session.scalar(select(Claim.id).where(Claim.supersedes_id == claim.id))
            if successor is not None:
                raise ServiceError(409, "superseded_resource", "Claim has a newer version", "landscape_generation")
            if claim.question_revision != revision or claim.selection_status != "selected":
                raise ServiceError(409, "invalid_state", "Claim is not selected in the current revision", "landscape_generation")
            if claim.human_grounding_status not in ("supported", "inferred") or not claim.evidence_links or (
                claim.human_grounding_status == "supported" and
                not any(link.role == "supports" for link in claim.evidence_links)
            ):
                raise ServiceError(409, "invalid_state", "Claim grounding is not eligible", "landscape_generation")
            source_ids = sorted(link.evidence_id for link in claim.evidence_links)
            if any(source_id not in evidences or evidences[source_id].project_id != project_id or
                   evidences[source_id].question_revision != revision or evidences[source_id].selection_status != "selected" or
                   evidences[source_id].chunk.paper.project_id != project_id or
                   evidences[source_id].source_text not in evidences[source_id].chunk.text
                   for source_id in source_ids):
                raise ServiceError(409, "invalid_state", "Claim provenance is no longer current", "landscape_generation")
            context_claims.append({"id": claim.id, "text": claim.text, "claim_type": claim.claim_type,
                                   "human_grounding_status": claim.human_grounding_status, "uncertainty": claim.uncertainty,
                                   "evidence_for_ids": sorted(link.evidence_id for link in claim.evidence_links if link.role == "supports"),
                                   "evidence_against_ids": sorted(link.evidence_id for link in claim.evidence_links if link.role == "challenges")})
            manifest.append({"type": "claim", "id": claim.id, "content_hash": _hash_text(claim.text),
                             "claim_type": claim.claim_type, "uncertainty_hash": _hash_text(claim.uncertainty),
                             "selection_status": claim.selection_status,
                             "human_grounding_status": claim.human_grounding_status,
                             "human_reviewed_at": claim.human_reviewed_at.isoformat() if claim.human_reviewed_at else None,
                             "evidence_ids": source_ids,
                             "evidence_for_ids": context_claims[-1]["evidence_for_ids"],
                             "evidence_against_ids": context_claims[-1]["evidence_against_ids"]})
        context_evidence = []
        for evidence_id in sorted(all_evidence_ids):
            item = evidences[evidence_id]
            context_evidence.append({"id": item.id, "source_text": item.source_text, "paper_chunk_id": item.paper_chunk_id,
                                     "paper_id": item.chunk.paper.id,
                                     "paper_title": item.chunk.paper.title, "page_number": item.chunk.page_number})
            manifest.append({"type": "evidence", "id": item.id, "source_hash": _hash_text(item.source_text),
                             "paper_chunk_id": item.paper_chunk_id, "paper_id": item.chunk.paper.id,
                             "page_number": item.chunk.page_number})
        return ordered, {"claims": context_claims, "evidence": context_evidence}, manifest, all_evidence_ids

    def _start_run(self, project_id: str, revision: int, question: str, manifest: list[dict[str, object]], prompt: Prompt, schema_version: str, stage: str, input_ids: Sequence[str]) -> str:
        with self.session_factory() as session:
            project = self._project(session, project_id, revision, lock=True)
            if stage == "claim":
                _, _, current_manifest = self._evidence_input(session, project_id, revision, input_ids)
            else:
                _, _, current_manifest, _ = self._claim_input(session, project_id, revision, input_ids)
            if project.research_question != question or current_manifest != manifest:
                raise ServiceError(409, "invalid_state", "Scientific input changed before Run creation", "scientific_generation")
            run = GenerationRun(project_id=project_id, stage=stage, status="started", provider="qwen",
                                model=self.settings.qwen_model, prompt_name=prompt.name, prompt_version=prompt.version,
                                prompt_hash=prompt.digest, schema_version=schema_version,
                                research_question_snapshot=question, question_revision=revision,
                                input_manifest=manifest, input_digest=canonical_digest({"question": question, "revision": revision, "inputs": manifest}),
                                attempt_count=0)
            session.add(run)
            session.commit()
            return run.id

    def _failed(self, run_id: str, code: str, attempts: int) -> None:
        with self.session_factory() as session:
            session.execute(update(GenerationRun).where(
                GenerationRun.id == run_id, GenerationRun.status == "started").values(
                status="failed", error_type=code, attempt_count=attempts,
                completed_at=now_utc()))
            session.commit()

    @diagnose_gap_call
    def _generate(self, run_id: str, prompt: Prompt, schema: type[BaseModel], context: dict[str, object],
                  validator: Callable[[BaseModel], None], *, prior_attempts: int = 0,
                  repair_instruction: str = STRUCTURED_REPAIR,
                  max_calls: int = STRUCTURED_OUTPUT_MAX_CALLS,
                  trace_stage: str | None = None, trace_validator: str | None = None,
                  trace_verification_round: int | None = None,
                  round_validator: Callable[[BaseModel, int], None] | None = None,
                  landscape_budget: LandscapeBudget | None = None,
                  prior_assessments: list[dict[str, object]] | None = None,
                  remaining_items: list[dict[str, object]] | None = None,
                  landscape_output: LandscapeOutput | None = None,
                  failure_handler: Callable[[str, str, int], None] | None = None) -> tuple[BaseModel, int]:
        fail_run = failure_handler or self._failed
        attempts = prior_attempts
        last_budget_error: LandscapeBudgetExceeded | None = None
        budget_feedback: LandscapeBudgetExceeded | None = None
        gap_feedback = GapRepairFeedback() if trace_stage == "gap_generation" else None
        try:
            provider_schema = (landscape_budget.response_schema(schema,
                prior_assessments=prior_assessments, remaining_items=remaining_items,
                landscape=landscape_output.model_dump() if landscape_output is not None else None)
                if landscape_budget is not None else schema)
        except LandscapeBudgetExceeded as exc:
            error = exc.service_error(repair_attempted=(trace_verification_round == 1 or
                (trace_stage or '').endswith('.repair')), context_chars=len(str(context)))
            self._landscape_trace(run_id, trace_stage or exc.stage, None,
                                  layer="output_budget", outcome="fail", **error.details)
            fail_run(run_id, error.code, attempts)
            error.details['generation_run_id'] = run_id
            raise error from exc

        def trace_budget_error(error: LandscapeBudgetExceeded, repair: int) -> None:
            self._landscape_trace(run_id, trace_stage or error.stage, repair,
                layer="output_budget", outcome="fail",
                **error.service_error(repair_attempted=bool(repair), context_chars=len(str(context))).details)
            if repair < max_calls - 1:
                self._landscape_trace(run_id, trace_stage or error.stage, repair + 1,
                    layer="repair", outcome="scheduled", reason="generated_output_budget_exceeded")
        for repair in range(max_calls):
            last_budget_error = None
            provider_role = ({"landscape_generation": "initial_structured_generation",
                              "summary_verification.repair": "summary_repair"}.get(trace_stage or "") or
                             ("conflict_repair" if (trace_stage or "").endswith(".repair") else
                              "conflict_verification" if (trace_stage or "").startswith("conflict_verification") else
                              "summary_verification" if trace_stage == "summary_verification" else None))
            if repair and provider_role:
                provider_role = "structured_repair"
            counted = False
            layer = "provider"
            provider_attempt: int | None = None
            instruction = prompt.text if repair == 0 else prompt.text + "\n" + repair_instruction
            if repair and gap_feedback is not None:
                instruction += gap_feedback.instructions()
            if landscape_budget is not None:
                instruction += landscape_budget.instructions(schema.__name__,
                    prior_assessments=prior_assessments, remaining_items=remaining_items,
                    landscape=landscape_output.model_dump() if landscape_output is not None else None)
            if budget_feedback is not None:
                instruction += budget_feedback.repair_instructions()
            def provider_trace(event: dict[str, object]) -> None:
                assert trace_stage is not None
                if trace_stage.startswith("gap_"):
                    gap_provider_event(event, repair)
                    return
                self._claim_trace(run_id, trace_stage, repair, provider_role=provider_role,
                                  verification_round=trace_verification_round, **event)
            try:
                if trace_stage and trace_stage.startswith("gap_"):
                    gap_invocation(repair, attempts)
                with trace_provider_calls(provider_trace, invocation_events=bool(trace_stage and trace_stage.startswith("gap_"))) if trace_stage else nullcontext():
                    result = self.provider.generate_structured(instruction, provider_schema, context)
                attempts += result.attempt_count
                counted = True
                provider_attempt = result.attempt_count
                if trace_stage:
                    self._claim_trace(run_id, trace_stage, repair, layer="provider_round", outcome="pass",
                                      provider_attempts=result.attempt_count, cumulative_attempts=attempts,
                                      provider_role=provider_role, verification_round=trace_verification_round)
                if result.model_name != self.settings.qwen_model:
                    raise LLMProviderError("Model identity changed", 0,
                                           category="provider_model_mismatch")
                raw_output = result.output.model_dump(mode="python") if isinstance(result.output, BaseModel) else result.output
                if landscape_budget is not None:
                    landscape_budget.check_response(schema.__name__, raw_output,
                        prior_assessments=prior_assessments or [], remaining_items=remaining_items,
                        landscape=landscape_output.model_dump() if landscape_output is not None else None)
                    if schema.__name__ in {"LandscapeOutput", "SummaryRepair"}:
                        candidate = ({**landscape_output.model_dump(), **raw_output}
                                     if landscape_output is not None and schema.__name__ == "SummaryRepair"
                                     and isinstance(raw_output, dict)
                                     else raw_output)
                        self._landscape_trace(run_id, trace_stage or "landscape_generated_output", repair,
                            layer="output_budget", outcome="pass", size_budget=landscape_budget.output_chars,
                            measured_size=len(str(candidate)), generated_output_chars=len(str(candidate)),
                            context_chars=len(str(context)), persistence_occurred=False,
                            repair_attempted=bool(repair) or (trace_stage or "").endswith(".repair"))
                layer = "pydantic"
                output = provider_schema.model_validate(raw_output)
                if trace_stage:
                    self._claim_trace(run_id, trace_stage, repair, layer="pydantic", source="service",
                                      outcome="pass", provider_attempt=provider_attempt)
                layer = "domain_validation"
                if round_validator is None:
                    validator(output)
                else:
                    round_validator(output, repair)
                if trace_stage:
                    if trace_stage == "landscape_generation":
                        self._landscape_trace(run_id, trace_stage, repair, layer="claim_whitelist", outcome="pass",
                                              provider_role=provider_role)
                    self._claim_trace(run_id, trace_stage, repair, layer="domain_validation", outcome="pass",
                                      validator=trace_validator or validator.__name__,
                                      provider_attempt=provider_attempt, provider_role=provider_role,
                                      verification_round=trace_verification_round)
                return output, attempts
            except LLMTimeout as exc:
                attempts += exc.attempt_count
                if trace_stage:
                    self._claim_trace(run_id, trace_stage, repair, layer="provider_round", outcome="fail",
                                      provider_attempts=exc.attempt_count, cumulative_attempts=attempts,
                                      category=exc.category, provider_role=provider_role,
                                      verification_round=trace_verification_round)
                fail_run(run_id, exc.category, attempts)
                raise ServiceError(504, "provider_timeout", "Qwen request timed out", "scientific_generation",
                                   {"generation_run_id": run_id, "provider_error_category": exc.category}) from exc
            except LLMProviderError as exc:
                attempts += exc.attempt_count
                if trace_stage:
                    self._claim_trace(run_id, trace_stage, repair, layer="provider_round", outcome="fail",
                                      provider_attempts=exc.attempt_count, cumulative_attempts=attempts,
                                      category=exc.category, error_type=type(exc).__name__,
                                      provider_role=provider_role, verification_round=trace_verification_round)
                # The adapter chains Pydantic failures without logging response text.
                # Recover only our typed numeric budget failure, preserving the actual
                # transport count and explicit budget category at this boundary.
                if isinstance(exc, LLMFormatError):
                    budget_error = LandscapeBudgetExceeded.from_validation_error(exc.__cause__)
                    if budget_error is not None:
                        last_budget_error = budget_feedback = budget_error
                        trace_budget_error(budget_error, repair)
                        if repair == max_calls - 1:
                            break
                        continue
                if not isinstance(exc, LLMFormatError):
                    fail_run(run_id, exc.category, attempts)
                    details = {"generation_run_id": run_id, "provider_error_category": exc.category}
                    if exc.http_status is not None:
                        details["provider_http_status"] = exc.http_status
                    raise ServiceError(502, "llm_failed", "Qwen request failed", "scientific_generation", details) from exc
                if repair == max_calls - 1:
                    break
                if trace_stage:
                    self._claim_trace(run_id, trace_stage, repair + 1, layer="repair", outcome="scheduled",
                                      reason="format_invalid")
            except LandscapeBudgetExceeded as exc:
                last_budget_error = budget_feedback = exc
                if not counted:
                    attempts += 1
                trace_budget_error(exc, repair)
                if repair == max_calls - 1:
                    break
            except (ValidationError, ValueError) as exc:
                if gap_feedback is not None:
                    gap_feedback = GapRepairFeedback.from_error(exc)
                if not counted:
                    if trace_stage and trace_stage.startswith("gap_"):
                        gap_event(layer="provider_round", outcome="fail", category="pre_result_value_error", legacy_cumulative_attempts=attempts)
                    attempts += 1
                budget_error = LandscapeBudgetExceeded.from_validation_error(exc)
                if budget_error is not None:
                    last_budget_error = budget_feedback = budget_error
                    trace_budget_error(budget_error, repair)
                    if repair == max_calls - 1:
                        break
                    continue
                if trace_stage:
                    details: dict[str, object] = {"layer": layer, "outcome": "fail",
                                                  "error_type": type(exc).__name__,
                                                  "provider_attempt": provider_attempt}
                    if isinstance(exc, ValidationError):
                        first = exc.errors(include_input=False)[0]
                        landscape_stage = trace_stage.startswith(("landscape_generation", "conflict_verification", "summary_verification"))
                        details["error_path"] = ("<extra_field>" if landscape_stage and first["type"] == "extra_forbidden"
                                                 else ".".join(str(part) for part in first["loc"]))
                        details["validation_code"] = first["type"]
                    if layer == "domain_validation":
                        details["validator"] = trace_validator or validator.__name__
                        details["validation_code"] = getattr(
                            exc, "validation_code", _DOMAIN_ERROR_CODES.get(str(exc), "domain_validation_error"))
                        if trace_stage == "landscape_generation" and details["validation_code"] == "source_not_allowed":
                            details["layer"] = "claim_whitelist"
                    details["provider_role"] = provider_role
                    details["verification_round"] = trace_verification_round
                    self._claim_trace(run_id, trace_stage, repair, **details)
                if repair == max_calls - 1:
                    break
                if trace_stage:
                    self._claim_trace(run_id, trace_stage, repair + 1, layer="repair", outcome="scheduled",
                                      reason=getattr(exc, "validation_code", layer + "_invalid"))
            except Exception as exc:
                if not counted:
                    attempts += 1
                if trace_stage:
                    self._claim_trace(run_id, trace_stage, repair, layer=layer, outcome="fail",
                                      error_type=type(exc).__name__)
                fail_run(run_id, "llm_failed", attempts)
                raise ServiceError(502, "llm_failed", "Qwen request failed", "scientific_generation", {"generation_run_id": run_id}) from exc
        if last_budget_error is not None:
            error = last_budget_error.service_error(
                repair_attempted=max_calls > 1 or (trace_stage or "").endswith(".repair") or
                                 trace_verification_round == 1, context_chars=len(str(context)))
            fail_run(run_id, error.code, attempts)
            error.details["generation_run_id"] = run_id
            raise error from last_budget_error
        if trace_stage:
            self._claim_trace(run_id, trace_stage, max_calls - 1, layer="stage_outcome", outcome="fail",
                              category="structured_output_invalid", cumulative_attempts=attempts)
        fail_run(run_id, "structured_output_invalid", attempts)
        raise ServiceError(502, "structured_output_invalid", "Qwen structured output is invalid", "scientific_generation", {"generation_run_id": run_id})

    @staticmethod
    def _validate_claim_output(batch: ClaimBatch, allowed: set[str]) -> None:
        for claim in batch.claims:
            if not set(claim.evidence_for_ids + claim.evidence_against_ids) <= allowed:
                raise ValueError("Claim source ID is outside the validated input")

    @staticmethod
    def _validate_landscape_output(output: LandscapeOutput, claim_ids: set[str], evidence_ids: set[str], provenance: dict[str, set[str]]) -> None:
        for item in output.items:
            if not set(item.claim_ids) <= claim_ids or not set(item.evidence_ids) <= evidence_ids:
                raise ValueError("Landscape source ID is outside the validated input")
            if not set(item.evidence_ids) <= set().union(*(provenance[claim_id] for claim_id in item.claim_ids)):
                raise ValueError("Landscape Evidence is not linked to cited Claims")
            if item.category == "conflict" and (len(item.evidence_ids) < 2 or any(
                not (set(item.evidence_ids) & provenance[claim_id]) for claim_id in item.claim_ids
            )):
                raise ValueError("Conflict requires independently cited Evidence for each Claim")

    @staticmethod
    def _validate_verifier_sources(verdict: ClaimVerification | ConflictVerification | SummaryVerification,
                                   sources: dict[str, str]) -> None:
        if len(set(verdict.evidence_refs)) != len(verdict.evidence_refs) or not set(verdict.evidence_refs) <= sources.keys():
            raise ValueError("Verifier referenced an invalid Evidence ID")
        if isinstance(verdict, ClaimVerification):
            return
        for quoted in verdict.support_excerpts:
            if quoted.evidence_id not in sources or quoted.evidence_id not in verdict.evidence_refs:
                raise ValueError("Verifier excerpt referenced an invalid Evidence ID")
            if quoted.excerpt not in sources[quoted.evidence_id]:
                raise ValueError("Verifier excerpt is not an exact source substring")

    @staticmethod
    def _validate_claim_risk_compatibility(verification: ClaimVerification) -> None:
        if not set(verification.risk_flags) <= _CLAIM_RISK_COMPATIBILITY[verification.verdict]:
            raise ValueError("Direct support cannot retain material scientific risk flags")

    def _verify_claim_batch(self, run_id: str, batch: ClaimBatch, sources: list[dict[str, object]],
                            attempts: int) -> tuple[list[ClaimVerification], int]:
        by_id = {str(source["id"]): source for source in sources}
        assessments = []
        verify_prompt = Prompt("claim_verification", CLAIM_PROMPT.version, CLAIM_VERIFY, {})
        for index, proposed in enumerate(batch.claims):
            ids = proposed.evidence_for_ids + proposed.evidence_against_ids
            scoped = {item_id: str(by_id[item_id]["source_text"]) for item_id in ids}
            context = {"claim_text": proposed.text, "claim_type": proposed.claim_type,
                       "supporting_evidence": [by_id[item_id] for item_id in proposed.evidence_for_ids],
                       "challenging_evidence": [by_id[item_id] for item_id in proposed.evidence_against_ids]}
            self._check_run_context(run_id, context, attempts)

            def validate(result: BaseModel) -> None:
                assert isinstance(result, ClaimVerification)
                self._validate_verifier_sources(result, scoped)
                self._validate_claim_risk_compatibility(result)
                if result.verdict == "directly_supported" and not (
                    set(result.evidence_refs) & set(proposed.evidence_for_ids)
                ):
                    raise ValueError("Direct support reference must come from supporting Evidence")

            verified, attempts = self._generate(run_id, verify_prompt, ClaimVerification, context, validate,
                                                 prior_attempts=attempts, repair_instruction=CLAIM_VERIFIER_REPAIR,
                                                 trace_stage=f"claim_verification[{index}]",
                                                 trace_validator="_validate_claim_verification")
            assessments.append(verified)
        return assessments, attempts

    def _bind_landscape_verification(
        self, verified: LandscapeItemVerification | LandscapeSummaryVerification,
        sources: dict[str, dict[str, object]], supporting_ids: set[str],
        claim_sources: dict[str, set[str]] | None = None,
    ) -> dict[str, object]:
        """Resolve new reference-only judgments; historical quote responses are never rewritten."""
        source_text = {source_id: str(source["source_text"]) for source_id, source in sources.items()}
        refs = set(verified.evidence_refs)
        positive = (verified.support_verdict == "supported" if isinstance(verified, LandscapeItemVerification)
                    else verified.verdict == "supported")
        if positive and not refs & supporting_ids:
            raise ValueError("Landscape support reference must come from supporting Evidence")
        if (isinstance(verified, LandscapeItemVerification) and verified.verdict == "direct_conflict"
                and claim_sources and len(claim_sources) >= 2):
            # Match each Claim to a distinct referenced source, including overlapping link sets.
            assigned: dict[str, str] = {}

            def match(claim_id: str, visited: set[str]) -> bool:
                for source_id in sorted(refs & (claim_sources or {})[claim_id]):
                    if source_id in visited:
                        continue
                    visited.add(source_id)
                    if source_id not in assigned or match(assigned[source_id], visited):
                        assigned[source_id] = claim_id
                        return True
                return False

            if not all(match(claim_id, set()) for claim_id in claim_sources):
                raise ValueError("Direct conflict references must independently cover each Claim")
        # Foreign refs fail the legacy validator before any lookup or binding is performed.
        legacy_values = {key: value for key, value in verified.model_dump().items()
                         if key not in {"support_verdict", "flagged_statements", "qualifications"}}
        if isinstance(verified, LandscapeSummaryVerification):
            legacy_values["flagged_statements"] = verified.flagged_statements
        legacy_schema = ConflictVerification if isinstance(verified, LandscapeItemVerification) else SummaryVerification
        legacy_values["support_excerpts"] = []
        self._validate_verifier_sources(legacy_schema.model_validate(legacy_values), source_text)
        legacy_values["support_excerpts"] = [{"evidence_id": source_id, "excerpt": source_text[source_id]}
                                             for source_id in verified.evidence_refs]
        self._validate_verifier_sources(legacy_schema.model_validate(legacy_values), source_text)
        bound = []
        for source_id in verified.evidence_refs:
            source = sources[source_id]
            bound.append({"evidence_id": source_id, "excerpt": source_text[source_id],
                          "source_hash": _hash_text(source_text[source_id]),
                          **{key: source[key] for key in ("paper_chunk_id", "paper_id", "paper_title", "page_number", "section")
                             if key in source}})
        return {**verified.model_dump(), "support_excerpts": bound,
                "source_binding_origin": "server_bound_canonical_source_text",
                "source_binding_schema_version": LANDSCAPE_SCHEMA_VERSION}

    def _verify_landscape(self, run_id: str, output: LandscapeOutput, context: dict[str, object],
                          claim_ids: set[str], evidence_ids: set[str], provenance: dict[str, set[str]],
                          attempts: int) -> tuple[LandscapeOutput, list[dict[str, object]], list[dict[str, object]], int]:
        try:
            budget = LandscapeBudget(context, self.settings.llm_max_context_chars)
            budget.check_output(output.model_dump())
        except LandscapeBudgetExceeded as exc:
            error = exc.service_error(repair_attempted=False)
            self._failed(run_id, error.code, attempts)
            error.details["generation_run_id"] = run_id
            raise error from exc
        except ServiceError as exc:
            self._failed(run_id, exc.code, attempts)
            exc.details["generation_run_id"] = run_id
            raise
        claims = {str(item["id"]): item for item in context["claims"]}
        evidence = {str(item["id"]): item for item in context["evidence"]}
        conflict_prompt = Prompt("conflict_verification", LANDSCAPE_PROMPT.version, CONFLICT_VERIFY, {})
        for repair_round in range(2):
            failed_conflict = None
            item_assessments: list[dict[str, object]] = []
            for index, item in enumerate(output.items):
                related_ids = sorted({source_id for claim_id in item.claim_ids for source_id in
                                      claims[claim_id]["evidence_for_ids"] + claims[claim_id]["evidence_against_ids"]})
                scoped = {item_id: evidence[item_id] for item_id in related_ids}
                supporting_ids = {source_id for claim_id in item.claim_ids for source_id in claims[claim_id]["evidence_for_ids"]}
                claim_sources = {claim_id: set(claims[claim_id]["evidence_for_ids"] + claims[claim_id]["evidence_against_ids"])
                                 for claim_id in item.claim_ids}
                verify_context = {"item_text": item.text, "item_category": item.category, "item_uncertainty": item.uncertainty,
                                  "claims": [claims[item_id] for item_id in item.claim_ids],
                                  "evidence": [evidence[item_id] for item_id in related_ids]}
                self._check_landscape_context(run_id, budget, verify_context, attempts,
                                              f"conflict_verification[{index}]", repair_attempted=bool(repair_round))
                verified, attempts = self._generate(run_id, conflict_prompt, LandscapeItemVerification,
                    verify_context, lambda result: self._bind_landscape_verification(result, scoped, supporting_ids, claim_sources),
                    prior_attempts=attempts, repair_instruction=LANDSCAPE_VERIFIER_REPAIR,
                    trace_stage=f"conflict_verification[{index}]",
                    trace_validator="_bind_landscape_verification",
                    trace_verification_round=repair_round, landscape_budget=budget,
                    prior_assessments=item_assessments,
                    remaining_items=[candidate.model_dump() for candidate in output.items[index:]])
                asserts_conflict = (self._explicit_conflict_wording(item.text) or
                                    (verified.asserts_direct_conflict and len(item.claim_ids) >= 2))
                verified_direct = (verified.verdict == "direct_conflict" and
                                   self._direct_conflict_comparable(verified, [claims[item_id] for item_id in item.claim_ids]))
                item_assessments.append({**self._bind_landscape_verification(verified, scoped, supporting_ids, claim_sources),
                                         "server_direct_conflict_role": asserts_conflict})
                if verified.verdict == "direct_conflict":
                    self._landscape_trace(run_id, f"conflict_verification[{index}]", None,
                                          layer="comparability_validation",
                                          outcome="pass" if verified_direct else "fail",
                                          validation_code=None if verified_direct else "comparability_not_established",
                                          verdict=verified.verdict, verification_round=repair_round)
                if verified.support_verdict != "supported" or (item.category == "conflict" and (not asserts_conflict or not verified_direct)) or (
                    item.category != "conflict" and asserts_conflict
                ):
                    failure_code = ("item_unsupported_assertion" if verified.support_verdict != "supported" else
                                    "comparability_not_established" if verified.verdict == "direct_conflict" and
                                    not verified_direct else "conflict_not_supported" if item.category == "conflict" else
                                    "conflict_category_mismatch")
                    self._landscape_trace(run_id, f"conflict_verification[{index}]", None,
                                          layer="comparability_validation", outcome="fail",
                                          validation_code=failure_code, verdict=verified.verdict,
                                          verification_round=repair_round)
                    failed_conflict = {"text": item.text, "category": item.category,
                                       "verdict": verified.verdict,
                                       "support_verdict": verified.support_verdict,
                                       "flagged_statements": verified.flagged_statements,
                                       "qualifications": verified.qualifications,
                                       "asserts_direct_conflict": asserts_conflict,
                                       "comparison_dimensions": verified.dimensions.model_dump()}
                    break
                self._landscape_trace(run_id, f"conflict_verification[{index}]", None,
                                      layer="domain_validation", outcome="pass", verdict=verified.verdict,
                                      verification_round=repair_round)
            if failed_conflict is None:
                break
            if repair_round == 1:
                self._failed(run_id, "scientific_verification_failed", attempts)
                raise ServiceError(502, "scientific_verification_failed", "Landscape item did not verify",
                                   "landscape_generation", {"generation_run_id": run_id})
            repair_prompt = Prompt("conflict_repair", LANDSCAPE_PROMPT.version, CONFLICT_REPAIR, {})
            repair_context = {**context, "rejected_conflict": failed_conflict}
            self._check_landscape_context(run_id, budget, repair_context, attempts,
                                          f"conflict_verification[{index}].repair", repair_attempted=bool(repair_round))
            self._landscape_trace(run_id, f"conflict_verification[{index}].repair", 1,
                                  layer="repair", outcome="scheduled", reason="conflict_not_verified",
                                  verification_round=repair_round)
            output, attempts = self._generate(run_id, repair_prompt, LandscapeOutput, repair_context,
                lambda result: self._validate_landscape_output(result, claim_ids, evidence_ids, provenance),
                prior_attempts=attempts, max_calls=1,
                trace_stage=f"conflict_verification[{index}].repair",
                trace_validator="_validate_landscape_output", trace_verification_round=repair_round,
                landscape_budget=budget)
            if any(item.text.strip().casefold() == failed_conflict["text"].strip().casefold()
                   for item in output.items):
                self._landscape_trace(run_id, f"conflict_verification[{index}].repair", 1,
                                      layer="domain_validation", outcome="fail",
                                      validation_code="rejected_conflict_retained",
                                      verification_round=repair_round)
                self._failed(run_id, "scientific_verification_failed", attempts)
                raise ServiceError(502, "scientific_verification_failed", "Rejected conflict text was retained",
                                   "landscape_generation", {"generation_run_id": run_id})

        summary_prompt = Prompt("summary_verification", LANDSCAPE_PROMPT.version, SUMMARY_VERIFY, {})
        summary_claim_ids = {claim_id for item in output.items for claim_id in item.claim_ids}
        summary_evidence_ids = {source_id for claim_id in summary_claim_ids for source_id in
                                claims[claim_id]["evidence_for_ids"] + claims[claim_id]["evidence_against_ids"]}
        summary_sources = {source_id: evidence[source_id] for source_id in sorted(summary_evidence_ids)}
        summary_supporting_ids = {source_id for claim_id in summary_claim_ids for source_id in claims[claim_id]["evidence_for_ids"]}
        summary_assessments: list[dict[str, object]] = []
        for repair_round in range(2):
            verify_context = {"summary": output.summary, "uncertainty": output.uncertainty,
                              "accepted_items": [item.model_dump() for item in output.items],
                              "item_assessments": assessment_payload(item_assessments),
                              **summary_source_context({
                                  "claims": [claims[claim_id] for claim_id in sorted(summary_claim_ids)],
                                  "evidence": list(summary_sources.values())})}
            self._check_landscape_context(run_id, budget, verify_context, attempts,
                                          "summary_verification", repair_attempted=bool(repair_round))
            verified, attempts = self._generate(run_id, summary_prompt, LandscapeSummaryVerification, verify_context,
                lambda result: self._bind_landscape_verification(result, summary_sources, summary_supporting_ids),
                prior_attempts=attempts, repair_instruction=LANDSCAPE_VERIFIER_REPAIR,
                trace_stage="summary_verification", trace_validator="_bind_landscape_verification",
                trace_verification_round=repair_round, landscape_budget=budget)
            summary_assessments.append(self._bind_landscape_verification(verified, summary_sources, summary_supporting_ids))
            if verified.verdict == "supported":
                self._landscape_trace(run_id, "summary_verification", None, layer="domain_validation",
                                      outcome="pass", verdict=verified.verdict,
                                      verification_round=repair_round)
                return output, item_assessments, summary_assessments, attempts
            self._landscape_trace(run_id, "summary_verification", None, layer="domain_validation",
                                  outcome="fail", verdict=verified.verdict,
                                  validation_code="summary_unsupported_assertion",
                                  verification_round=repair_round)
            if repair_round == 1:
                self._failed(run_id, "scientific_verification_failed", attempts)
                raise ServiceError(502, "scientific_verification_failed", "Landscape summary did not verify",
                                   "landscape_generation", {"generation_run_id": run_id})
            repair_prompt = Prompt("summary_repair", LANDSCAPE_PROMPT.version, SUMMARY_REPAIR, {})
            repair_context = {**verify_context, "flagged_statements": verified.flagged_statements,
                              "qualifications": verified.qualifications}
            self._check_landscape_context(run_id, budget, repair_context, attempts,
                                          "summary_verification.repair", repair_attempted=bool(repair_round))
            self._landscape_trace(run_id, "summary_verification.repair", 1, layer="repair",
                                  outcome="scheduled", reason="summary_unsupported_assertion",
                                  verification_round=repair_round)
            repaired, attempts = self._generate(run_id, repair_prompt, SummaryRepair, repair_context,
                                                lambda result: None, prior_attempts=attempts, max_calls=1,
                                                trace_stage="summary_verification.repair",
                                                trace_validator="summary_repair_schema",
                                                trace_verification_round=repair_round, landscape_budget=budget,
                                                landscape_output=output)
            output = LandscapeOutput.model_validate({**output.model_dump(), "summary": repaired.summary,
                                                     "uncertainty": repaired.uncertainty})
        raise AssertionError("Unreachable summary verification state")

    @staticmethod
    def _explicit_conflict_wording(text: str) -> bool:
        lower = text.casefold()
        # Remove only the negated span, retaining affirmative claims in other clauses.
        lower = re.sub(
            r"(?:并非|不是|并不|不构成|未形成|"
            r"(?:不能|不可|无法)(?:直接|据此)?(?:合并|认定|判定|断言|认为|视为|看作|解释为))"
            r"(?:(?!但|却|而是)[^,，。.!！?？;；\n]){0,30}"
            r"(?:直接矛盾|直接冲突|相互矛盾|互相矛盾)", "", lower,
        )
        return any(phrase in lower for phrase in (
            "directly contradict", "direct contradiction", "direct conflict",
            "contradict one another", "contradict each other", "mutually contradictory",
            "findings are incompatible", "results are incompatible", "reports are incompatible",
            "直接矛盾", "直接冲突", "相互矛盾", "互相矛盾",
        )) and not any(phrase in lower for phrase in (
            "not directly contradict", "no direct contradiction", "not a direct conflict",
            "do not contradict one another", "do not contradict each other",
            "并非直接矛盾", "并非直接冲突", "并非相互矛盾", "并非互相矛盾",
        ))

    @staticmethod
    def _direct_conflict_comparable(verified: ConflictVerification | LandscapeItemVerification, claims: list[dict[str, object]]) -> bool:
        dimensions = verified.dimensions
        if len(claims) < 2 or any(value == "different" for value in dimensions.model_dump().values()):
            return False
        if any(getattr(dimensions, name) != "comparable" for name in
               ("task", "metric", "experimental_condition")):
            return False
        if dimensions.dataset != "comparable" and dimensions.population != "comparable":
            return False
        baseline_terms = ("baseline", "compared to", "compared with", "relative to", "outperform", " beat ",
                          " versus ", " vs ", "基线", "优于", "相比")
        baseline_relevant = any(claim["claim_type"] == "comparison" or
                                any(term in f" {str(claim['text']).casefold()} " for term in baseline_terms)
                                for claim in claims)
        return not baseline_relevant or dimensions.baseline == "comparable"

    def generate_claims(self, project_id: str, evidence_ids: list[str], revision: int) -> dict[str, object]:
        with self.session_factory() as session:
            project = self._project(session, project_id, revision)
            _, sources, manifest = self._evidence_input(session, project_id, revision, evidence_ids)
            context = {"research_question": project.research_question, "evidence": sources}
            self._check_size(context, self.settings)
            question = project.research_question
        run_id = self._start_run(project_id, revision, question, manifest, CLAIM_PROMPT, CLAIM_SCHEMA_VERSION, "claim", evidence_ids)
        batch, attempts = self._generate(run_id, CLAIM_PROMPT, ClaimBatch, context,
                                         lambda output: self._validate_claim_output(output, set(evidence_ids)),
                                         trace_stage="claim_batch", trace_validator="_validate_claim_output")
        assessments, attempts = self._verify_claim_batch(run_id, batch, sources, attempts)
        try:
            with self.session_factory() as session:
                try:
                    project = self._project(session, project_id, revision, lock=True)
                    current_evidence, _, current_manifest = self._evidence_input(session, project_id, revision, evidence_ids)
                except ServiceError as exc:
                    self._claim_trace(run_id, "final_source_revalidation", None, layer="source_state",
                                      outcome="fail", category=exc.code)
                    raise
                if canonical_digest({"question": project.research_question, "revision": revision, "inputs": current_manifest}) != canonical_digest({"question": question, "revision": revision, "inputs": manifest}):
                    self._claim_trace(run_id, "final_source_revalidation", None, layer="source_state",
                                      outcome="fail", category="invalid_state")
                    raise ServiceError(409, "invalid_state", "Evidence input changed during generation", "claim_generation")
                self._claim_trace(run_id, "final_source_revalidation", None, layer="source_state", outcome="pass")
                run = session.scalar(select(GenerationRun).where(
                    GenerationRun.id == run_id, GenerationRun.project_id == project_id,
                    GenerationRun.stage == "claim").with_for_update())
                if run is None or run.status != "started":
                    self._claim_trace(run_id, "generation_run_fence", None, layer="run_status",
                                      outcome="fail", category="generation_superseded")
                    raise ServiceError(409, "generation_superseded", "Generation Run is no longer active",
                                       "claim_generation")
                self._claim_trace(run_id, "generation_run_fence", None, layer="run_status", outcome="pass")
                canonical_evidence = {item.id: item for item in current_evidence}
                claims = []
                for proposed, verified in zip(batch.claims, assessments, strict=True):
                    support_excerpts = [
                        {"evidence_id": evidence_id, "excerpt": canonical_evidence[evidence_id].source_text}
                        for evidence_id in verified.evidence_refs
                        if verified.verdict == "directly_supported" and evidence_id in proposed.evidence_for_ids
                    ]
                    claim = Claim(project_id=project_id, text=proposed.text, claim_type=proposed.claim_type,
                                  legacy_grounding_status=None, ai_grounding_assessment=verified.verdict,
                                  ai_risk_flags=verified.risk_flags, ai_justification=verified.justification,
                                  ai_evidence_refs=verified.evidence_refs,
                                  ai_support_excerpts=support_excerpts,
                                  human_grounding_status="pending_review", confidence=proposed.confidence,
                                  uncertainty=proposed.uncertainty, selection_status="candidate", question_revision=revision,
                                  origin="model", generation_run_id=run_id)
                    session.add(claim)
                    session.flush()
                    for evidence_id in proposed.evidence_for_ids:
                        session.add(ClaimEvidence(claim_id=claim.id, evidence_id=evidence_id, role="supports"))
                    for evidence_id in proposed.evidence_against_ids:
                        session.add(ClaimEvidence(claim_id=claim.id, evidence_id=evidence_id, role="challenges"))
                    claims.append(claim)
                transitioned = session.execute(update(GenerationRun).where(
                    GenerationRun.id == run_id, GenerationRun.status == "started").values(
                    status="succeeded", attempt_count=attempts, completed_at=now_utc()))
                if transitioned.rowcount != 1:
                    self._claim_trace(run_id, "generation_run_fence", None, layer="run_transition",
                                      outcome="fail", category="generation_superseded")
                    raise ServiceError(409, "generation_superseded", "Generation Run is no longer active",
                                       "claim_generation")
                session.flush()
                response = {"generation_run_id": run_id, "claims": [self._claim_read(session, item) for item in claims]}
                session.commit()
                self._claim_trace(run_id, "database_commit", None, layer="commit", outcome="pass",
                                  persisted_claim_count=len(claims), cumulative_attempts=attempts)
                return response
        except ServiceError as exc:
            self._claim_trace(run_id, "database_commit", None, layer="commit", outcome="fail", category=exc.code)
            self._failed(run_id, exc.code, attempts)
            exc.details["generation_run_id"] = run_id
            raise
        except SQLAlchemyError as exc:
            self._claim_trace(run_id, "database_commit", None, layer="commit", outcome="fail",
                              category="database_failed", error_type=type(exc).__name__)
            self._failed(run_id, "database_failed", attempts)
            raise ServiceError(500, "database_failed", "Scientific batch could not be saved", "claim_generation",
                               {"generation_run_id": run_id}) from exc

    def _claim_read(self, session: Session, claim: Claim) -> dict[str, object]:
        project = self._project(session, claim.project_id)
        superseded = session.scalar(select(Claim.id).where(Claim.supersedes_id == claim.id)) is not None
        sources = list(session.scalars(select(Evidence).where(Evidence.id.in_([link.evidence_id for link in claim.evidence_links]))))
        eligible = (not superseded and claim.question_revision == project.question_revision and
                    claim.selection_status == "selected" and claim.human_grounding_status in ("supported", "inferred") and
                    bool(claim.evidence_links) and len(sources) == len(claim.evidence_links) and
                    (claim.human_grounding_status != "supported" or any(
                        link.role == "supports" for link in claim.evidence_links)) and
                    all(source.selection_status == "selected" and
                        source.question_revision == project.question_revision and
                        source.project_id == claim.project_id and
                        source.chunk.paper.project_id == claim.project_id and
                        source.source_text in source.chunk.text for source in sources))
        return {"id": claim.id, "project_id": claim.project_id, "text": claim.text, "claim_type": claim.claim_type,
                "legacy_grounding_status": claim.legacy_grounding_status,
                "ai_grounding_assessment": claim.ai_grounding_assessment, "ai_risk_flags": claim.ai_risk_flags,
                "ai_justification": claim.ai_justification, "ai_evidence_refs": claim.ai_evidence_refs,
                "ai_support_excerpts": claim.ai_support_excerpts,
                "human_grounding_status": claim.human_grounding_status,
                "human_reviewed_at": claim.human_reviewed_at,
                "confidence": claim.confidence, "uncertainty": claim.uncertainty,
                "evidence_for_ids": sorted(link.evidence_id for link in claim.evidence_links if link.role == "supports"),
                "evidence_against_ids": sorted(link.evidence_id for link in claim.evidence_links if link.role == "challenges"),
                "selection_status": claim.selection_status, "question_revision": claim.question_revision,
                "supersedes_id": claim.supersedes_id, "generation_run_id": claim.generation_run_id,
                "origin": claim.origin, "eligible_for_generation": eligible, "created_at": claim.created_at}

    def list_claims(self, project_id: str, selection_status: str | None = None) -> list[dict[str, object]]:
        with self.session_factory() as session:
            self._project(session, project_id)
            query = select(Claim).where(Claim.project_id == project_id).options(selectinload(Claim.evidence_links)).order_by(Claim.created_at, Claim.id)
            if selection_status:
                query = query.where(Claim.selection_status == selection_status)
            return [self._claim_read(session, item) for item in session.scalars(query)]

    def get_claim(self, project_id: str, claim_id: str) -> dict[str, object]:
        with self.session_factory() as session:
            self._project(session, project_id)
            claim = session.scalar(select(Claim).where(Claim.id == claim_id, Claim.project_id == project_id).options(selectinload(Claim.evidence_links)))
            if claim is None:
                raise ServiceError(404, "not_found", "Claim not found", "claim")
            return self._claim_read(session, claim)

    def select_claim(self, project_id: str, claim_id: str, status: str, revision: int) -> dict[str, object]:
        with self.session_factory() as session:
            self._project(session, project_id, revision, lock=True)
            claim = session.scalar(select(Claim).where(Claim.id == claim_id, Claim.project_id == project_id).with_for_update())
            if claim is None:
                raise ServiceError(404, "not_found", "Claim not found", "claim")
            if claim.question_revision != revision:
                raise ServiceError(409, "invalid_state", "Claim is not in the current revision", "claim")
            if session.scalar(select(Claim.id).where(Claim.supersedes_id == claim_id)):
                raise ServiceError(409, "superseded_resource", "Claim has a newer version", "claim")
            claim.selection_status = status
            session.commit()
        return self.get_claim(project_id, claim_id)

    def review_claim_grounding(self, project_id: str, claim_id: str, status: str, revision: int) -> dict[str, object]:
        with self.session_factory() as session:
            self._project(session, project_id, revision, lock=True)
            claim = session.scalar(select(Claim).where(Claim.id == claim_id, Claim.project_id == project_id).with_for_update())
            if claim is None:
                raise ServiceError(404, "not_found", "Claim not found", "claim_review")
            if claim.question_revision != revision or session.scalar(select(Claim.id).where(Claim.supersedes_id == claim_id)):
                raise ServiceError(409, "superseded_resource", "Claim cannot be reviewed", "claim_review")
            self._evidence_input(session, project_id, revision, [link.evidence_id for link in claim.evidence_links])
            if status == "supported" and not any(link.role == "supports" for link in claim.evidence_links):
                raise ServiceError(422, "invalid_reference", "Supported Claim requires supporting Evidence", "claim_review")
            claim.human_grounding_status = status
            claim.human_reviewed_at = now_utc()
            session.commit()
        return self.get_claim(project_id, claim_id)

    def revise_claim(self, project_id: str, claim_id: str, revision: int, proposal: HumanClaimRevision) -> dict[str, object]:
        with self.session_factory() as session:
            self._project(session, project_id, revision, lock=True)
            prior = session.scalar(select(Claim).where(Claim.id == claim_id, Claim.project_id == project_id).with_for_update())
            if prior is None:
                raise ServiceError(404, "not_found", "Claim not found", "claim")
            if prior.question_revision != revision or session.scalar(select(Claim.id).where(Claim.supersedes_id == claim_id)):
                raise ServiceError(409, "superseded_resource", "Claim cannot be revised", "claim")
            ids = proposal.evidence_for_ids + proposal.evidence_against_ids
            self._evidence_input(session, project_id, revision, ids)
            revised = Claim(project_id=project_id, text=proposal.text, claim_type=proposal.claim_type,
                            legacy_grounding_status=None, ai_grounding_assessment=None,
                            human_grounding_status="pending_review", confidence=proposal.confidence,
                            uncertainty=proposal.uncertainty, selection_status="candidate", question_revision=revision,
                            origin="human_revision", supersedes_id=claim_id, generation_run_id=None)
            session.add(revised)
            try:
                session.flush()
                session.add_all([ClaimEvidence(claim_id=revised.id, evidence_id=eid, role=role)
                                 for role, values in (("supports", proposal.evidence_for_ids), ("challenges", proposal.evidence_against_ids))
                                 for eid in values])
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise ServiceError(409, "superseded_resource", "Claim already has a direct successor", "claim") from exc
            return self._claim_read(session, revised)

    def get_run(self, project_id: str, run_id: str) -> dict[str, object]:
        with self.session_factory() as session:
            self._project(session, project_id)
            run = session.scalar(select(GenerationRun).where(GenerationRun.id == run_id, GenerationRun.project_id == project_id))
            if run is None:
                raise ServiceError(404, "not_found", "GenerationRun not found", "generation_run")
            return {field: getattr(run, field) for field in ("id", "project_id", "stage", "status", "provider", "model",
                    "prompt_name", "prompt_version", "prompt_hash", "schema_version", "research_question_snapshot",
                    "question_revision", "input_manifest", "input_digest", "attempt_count", "error_type", "created_at", "completed_at")}

    @staticmethod
    def _store_landscape_items(session: Session, landscape_id: str, proposals: list[LandscapeItemProposal],
                               assessments: list[dict[str, object]] | None = None) -> None:
        if assessments is not None and len(assessments) != len(proposals):
            raise ValueError("Landscape assessment count does not match item count")
        for index, proposed in enumerate(proposals):
            item = LandscapeItem(landscape_id=landscape_id, category=proposed.category,
                                 text=proposed.text, uncertainty=proposed.uncertainty,
                                 ai_conflict_assessment=assessments[index] if assessments is not None else None)
            session.add(item)
            session.flush()
            for claim_id in proposed.claim_ids:
                session.add(LandscapeItemClaim(item_id=item.id, claim_id=claim_id))
            for evidence_id in proposed.evidence_ids:
                session.add(LandscapeItemEvidence(item_id=item.id, evidence_id=evidence_id))

    def generate_landscape(self, project_id: str, claim_ids: list[str], revision: int) -> dict[str, object]:
        with self.session_factory() as session:
            project = self._project(session, project_id, revision)
            claims, source_context, manifest, evidence_ids = self._claim_input(session, project_id, revision, claim_ids)
            provenance = {claim.id: {link.evidence_id for link in claim.evidence_links} for claim in claims}
            context = {"research_question": project.research_question, **source_context}
            budget = LandscapeBudget(context, self.settings.llm_max_context_chars)
            self._check_size(context, self.settings)
            question = project.research_question
        run_id = self._start_run(project_id, revision, question, manifest, LANDSCAPE_PROMPT, LANDSCAPE_SCHEMA_VERSION, "landscape", claim_ids)
        output, attempts = self._generate(run_id, LANDSCAPE_PROMPT, LandscapeOutput, context,
                                          lambda result: self._validate_landscape_output(result, set(claim_ids), evidence_ids, provenance),
                                          trace_stage="landscape_generation",
                                          trace_validator="_validate_landscape_output", landscape_budget=budget)
        output, item_assessments, summary_assessments, attempts = self._verify_landscape(
            run_id, output, context, set(claim_ids), evidence_ids, provenance, attempts)
        try:
            with self.session_factory() as session:
                try:
                    project = self._project(session, project_id, revision, lock=True)
                    _, _, current_manifest, _ = self._claim_input(session, project_id, revision, claim_ids)
                except ServiceError as exc:
                    self._landscape_trace(run_id, "final_claim_state_revalidation", None,
                                          layer="source_state", outcome="fail", category=exc.code)
                    raise
                if canonical_digest({"question": project.research_question, "revision": revision, "inputs": current_manifest}) != canonical_digest({"question": question, "revision": revision, "inputs": manifest}):
                    self._landscape_trace(run_id, "final_claim_state_revalidation", None,
                                          layer="source_state", outcome="fail", category="invalid_state")
                    raise ServiceError(409, "invalid_state", "Claim input changed during generation", "landscape_generation")
                self._landscape_trace(run_id, "final_claim_state_revalidation", None,
                                      layer="source_state", outcome="pass")
                run = session.scalar(select(GenerationRun).where(
                    GenerationRun.id == run_id, GenerationRun.project_id == project_id,
                    GenerationRun.stage == "landscape").with_for_update())
                if run is None or run.status != "started":
                    self._landscape_trace(run_id, "generation_run_fence", None,
                                          layer="run_status", outcome="fail", category="generation_superseded")
                    raise ServiceError(409, "generation_superseded", "Generation Run is no longer active",
                                       "landscape_generation")
                self._landscape_trace(run_id, "generation_run_fence", None,
                                      layer="run_status", outcome="pass")
                landscape = ResearchLandscape(project_id=project_id, summary=output.summary,
                                              uncertainty=output.uncertainty, question_revision=revision,
                                              review_status="pending_review",
                                              ai_summary_assessments=summary_assessments,
                                              origin="model", generation_run_id=run_id)
                session.add(landscape)
                session.flush()
                self._store_landscape_items(session, landscape.id, output.items, item_assessments)
                transitioned = session.execute(update(GenerationRun).where(
                    GenerationRun.id == run_id, GenerationRun.status == "started").values(
                    status="succeeded", attempt_count=attempts, completed_at=now_utc()))
                if transitioned.rowcount != 1:
                    self._landscape_trace(run_id, "generation_run_fence", None,
                                          layer="run_transition", outcome="fail", category="generation_superseded")
                    raise ServiceError(409, "generation_superseded", "Generation Run is no longer active",
                                       "landscape_generation")
                session.flush()
                response = {"generation_run_id": run_id, "landscape": self._landscape_read(session, landscape)}
                session.commit()
                self._landscape_trace(run_id, "database_commit", None, layer="commit", outcome="pass",
                                      persisted_landscape_count=1, cumulative_attempts=attempts)
                return response
        except ServiceError as exc:
            self._landscape_trace(run_id, "database_commit", None, layer="commit", outcome="fail", category=exc.code)
            self._failed(run_id, exc.code, attempts)
            exc.details["generation_run_id"] = run_id
            raise
        except SQLAlchemyError as exc:
            self._landscape_trace(run_id, "database_commit", None, layer="commit", outcome="fail",
                                  category="database_failed", error_type=type(exc).__name__)
            self._failed(run_id, "database_failed", attempts)
            raise ServiceError(500, "database_failed", "Scientific batch could not be saved", "landscape_generation",
                               {"generation_run_id": run_id}) from exc

    def _landscape_read(self, session: Session, landscape: ResearchLandscape) -> dict[str, object]:
        project = self._project(session, landscape.project_id)
        superseded = session.scalar(select(ResearchLandscape.id).where(ResearchLandscape.supersedes_id == landscape.id)) is not None
        eligible = (not superseded and landscape.question_revision == project.question_revision and
                    landscape.review_status == "accepted")
        if eligible:
            referenced_claim_ids = sorted({link.claim_id for item in landscape.items for link in item.claim_links})
            try:
                self._claim_input(session, landscape.project_id, project.question_revision, referenced_claim_ids)
            except ServiceError:
                eligible = False
        items = [{"id": item.id, "category": item.category, "text": item.text, "uncertainty": item.uncertainty,
                  "ai_conflict_assessment": item.ai_conflict_assessment,
                  "claim_ids": sorted(link.claim_id for link in item.claim_links),
                  "evidence_ids": sorted(link.evidence_id for link in item.evidence_links)}
                 for item in landscape.items]
        return {"id": landscape.id, "project_id": landscape.project_id, "summary": landscape.summary,
                "uncertainty": landscape.uncertainty, "review_status": landscape.review_status,
                "reviewed_at": landscape.reviewed_at,
                "ai_summary_assessments": landscape.ai_summary_assessments,
                "items": items, "question_revision": landscape.question_revision,
                "supersedes_id": landscape.supersedes_id, "generation_run_id": landscape.generation_run_id,
                "origin": landscape.origin, "eligible_for_downstream": eligible, "created_at": landscape.created_at}

    def review_landscape(self, project_id: str, landscape_id: str, status: str, revision: int) -> dict[str, object]:
        with self.session_factory() as session:
            self._project(session, project_id, revision, lock=True)
            landscape = session.scalar(select(ResearchLandscape).where(
                ResearchLandscape.id == landscape_id, ResearchLandscape.project_id == project_id).with_for_update())
            if landscape is None:
                raise ServiceError(404, "not_found", "ResearchLandscape not found", "landscape_review")
            if landscape.question_revision != revision or session.scalar(select(ResearchLandscape.id).where(
                ResearchLandscape.supersedes_id == landscape_id)):
                raise ServiceError(409, "superseded_resource", "ResearchLandscape cannot be reviewed", "landscape_review")
            if status == "accepted":
                claim_ids = sorted({link.claim_id for item in landscape.items for link in item.claim_links})
                self._claim_input(session, project_id, revision, claim_ids)
            landscape.review_status = status
            landscape.reviewed_at = now_utc()
            session.commit()
        return self.get_landscape(project_id, landscape_id)

    def list_landscapes(self, project_id: str) -> list[dict[str, object]]:
        with self.session_factory() as session:
            self._project(session, project_id)
            query = select(ResearchLandscape).where(ResearchLandscape.project_id == project_id).options(
                selectinload(ResearchLandscape.items).selectinload(LandscapeItem.claim_links),
                selectinload(ResearchLandscape.items).selectinload(LandscapeItem.evidence_links),
            ).order_by(ResearchLandscape.created_at, ResearchLandscape.id)
            return [self._landscape_read(session, item) for item in session.scalars(query)]

    def get_landscape(self, project_id: str, landscape_id: str) -> dict[str, object]:
        with self.session_factory() as session:
            self._project(session, project_id)
            landscape = session.scalar(select(ResearchLandscape).where(ResearchLandscape.id == landscape_id,
                                                                       ResearchLandscape.project_id == project_id))
            if landscape is None:
                raise ServiceError(404, "not_found", "ResearchLandscape not found", "landscape")
            return self._landscape_read(session, landscape)

    def revise_landscape(self, project_id: str, landscape_id: str, revision: int, output: LandscapeOutput) -> dict[str, object]:
        with self.session_factory() as session:
            self._project(session, project_id, revision, lock=True)
            prior = session.scalar(select(ResearchLandscape).where(ResearchLandscape.id == landscape_id,
                                                                     ResearchLandscape.project_id == project_id).with_for_update())
            if prior is None:
                raise ServiceError(404, "not_found", "ResearchLandscape not found", "landscape")
            if prior.question_revision != revision or session.scalar(select(ResearchLandscape.id).where(ResearchLandscape.supersedes_id == landscape_id)):
                raise ServiceError(409, "superseded_resource", "ResearchLandscape cannot be revised", "landscape")
            claim_ids = sorted({claim_id for item in output.items for claim_id in item.claim_ids})
            claims, _, _, evidence_ids = self._claim_input(session, project_id, revision, claim_ids)
            provenance = {claim.id: {link.evidence_id for link in claim.evidence_links} for claim in claims}
            try:
                self._validate_landscape_output(output, set(claim_ids), evidence_ids, provenance)
            except ValueError as exc:
                raise ServiceError(422, "invalid_reference", "ResearchLandscape source relationship is invalid", "landscape") from exc
            revised = ResearchLandscape(project_id=project_id, summary=output.summary, uncertainty=output.uncertainty,
                                        question_revision=revision, review_status="pending_review",
                                        origin="human_revision", supersedes_id=landscape_id,
                                        generation_run_id=None)
            session.add(revised)
            try:
                session.flush()
                self._store_landscape_items(session, revised.id, output.items)
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise ServiceError(409, "superseded_resource", "ResearchLandscape already has a direct successor", "landscape") from exc
            return self._landscape_read(session, revised)
