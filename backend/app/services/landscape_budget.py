"""Generation-only cumulative Landscape contract, measured with the provider guard.

Historical output schemas and stored scientific objects remain readable. All
accepted output (including escaped strings and reference/list overhead) is
bounded before it can enter another provider context. No text is truncated.
"""

from copy import copy
from dataclasses import asdict, dataclass
from typing import Annotated, get_args

from annotated_types import MaxLen
from pydantic import BaseModel, ValidationError, create_model, model_validator

from app.services.errors import ServiceError
from app.prompts.scientific import (
    LANDSCAPE_BUDGET_CONTRACT, LANDSCAPE_BUDGET_REPAIR, LANDSCAPE_ASSESSMENT_CHARS, LANDSCAPE_CRITIQUE_CHARS,
    LANDSCAPE_MIN_OUTPUT_CHARS, LANDSCAPE_MAX_OUTPUT_CHARS,
    LANDSCAPE_ASSESSMENT_ALLOCATION, LANDSCAPE_ASSESSMENT_REPAIR,
    ASSESSMENT_CONTEXT_VERSION,
)


ASSESSMENT_CHARS = LANDSCAPE_ASSESSMENT_CHARS
CRITIQUE_CHARS = LANDSCAPE_CRITIQUE_CHARS
MIN_OUTPUT_CHARS = LANDSCAPE_MIN_OUTPUT_CHARS
MAX_OUTPUT_CHARS = LANDSCAPE_MAX_OUTPUT_CHARS
_BINDING_FIELDS = {"support_excerpts", "source_binding_origin", "source_binding_schema_version"}
_ASSESSMENT_COLUMNS = ('verdict', 'asserts_direct_conflict', 'dimensions', 'support_verdict',
                       'flagged_statements', 'qualifications', 'evidence_refs', 'justification')
_DIMENSION_COLUMNS = ('dataset', 'population', 'task', 'metric', 'experimental_condition', 'baseline')
_SERVER_ROLE = 'server_direct_conflict_role'


def encode_assessment(assessment: dict[str, object]) -> list[object]:
    """assessment-context-v1: exact values, fixed columns, optional server role.

    Canonical provenance projection is separate below. No unknown key, missing
    column, or invalid shape can enter a row. Empty lists/strings are retained.
    Public Pydantic schemas remain the authority for scientific validity.
    """
    if not isinstance(assessment, dict) or set(assessment) not in (
            set(_ASSESSMENT_COLUMNS), {*_ASSESSMENT_COLUMNS, _SERVER_ROLE}):
        raise ValueError('Invalid assessment context fields')
    dimensions = assessment['dimensions']
    if not isinstance(dimensions, dict) or set(dimensions) != set(_DIMENSION_COLUMNS):
        raise ValueError('Invalid assessment context dimensions')
    if (any(not isinstance(assessment[k], str) for k in ('verdict','support_verdict','justification'))
            or type(assessment['asserts_direct_conflict']) is not bool
            or (_SERVER_ROLE in assessment and type(assessment[_SERVER_ROLE]) is not bool)
            or any(not isinstance(dimensions[k], str) for k in _DIMENSION_COLUMNS)
            or any(not isinstance(assessment[k], list) or any(not isinstance(v, str) for v in assessment[k])
                   for k in ('flagged_statements','qualifications','evidence_refs'))):
        raise ValueError('Invalid assessment context values')
    row = [([dimensions[d] for d in _DIMENSION_COLUMNS] if k == 'dimensions'
            else list(assessment[k]) if isinstance(assessment[k], list) else assessment[k])
           for k in _ASSESSMENT_COLUMNS]
    if _SERVER_ROLE in assessment:
        row.append(assessment[_SERVER_ROLE])
    return row


def decode_assessment(row: list[object]) -> dict[str, object]:
    """Decode this version only; reject unknown columns instead of dropping them."""
    if (not isinstance(row, list) or len(row) not in (8, 9)
            or not isinstance(row[2], list) or len(row[2]) != len(_DIMENSION_COLUMNS)):
        raise ValueError('Invalid assessment context row')
    result = dict(zip(_ASSESSMENT_COLUMNS, row[:8]))
    result['dimensions'] = dict(zip(_DIMENSION_COLUMNS, row[2]))
    if len(row) == 9:
        result[_SERVER_ROLE] = row[8]
    # The encoder validates types and makes independent copies of mutable lists.
    validated = encode_assessment(result)
    for index, key in enumerate(_ASSESSMENT_COLUMNS):
        if key != 'dimensions':
            result[key] = validated[index]
    return result


@dataclass(frozen=True)
class AssessmentAllocation:
    """Representation reservation only; never a scientific adequacy guarantee."""

    cumulative_cap: int
    immutable_prior_chars: int
    server_overhead_chars: int
    list_overhead_chars: int
    later_reservation_chars: int
    current_response_allowance: int
    minimum_response_chars: int
    minimum_required_chars: int
    remaining_items: int


class LandscapeBudgetExceeded(ValueError):
    def __init__(self, stage: str, budget: int, measured: int, *, generated_chars: int | None = None,
                 allocation: AssessmentAllocation | None = None, candidate_chars: int | None = None):
        self.stage, self.budget, self.measured = stage, budget, measured
        self.generated_chars = measured if generated_chars is None else generated_chars
        self.allocation, self.candidate_chars = allocation, candidate_chars
        super().__init__("Landscape generated output exceeds its cumulative budget")

    def repair_instructions(self) -> str:
        if self.allocation is not None:
            return LANDSCAPE_ASSESSMENT_REPAIR.format(**self.allocation_details())
        return LANDSCAPE_BUDGET_REPAIR.format(measured=self.measured, allowance=self.budget,
                                             excess=self.measured - self.budget)

    def allocation_details(self) -> dict[str, object]:
        if self.allocation is None:
            return {}
        return {**asdict(self.allocation), 'assessment_encoding_version': ASSESSMENT_CONTEXT_VERSION,
                'current_candidate_chars': self.candidate_chars,
                'required_reduction_chars': max(0, (self.candidate_chars or 0) -
                    self.allocation.current_response_allowance, self.measured - self.budget)}

    @staticmethod
    def from_validation_error(error: BaseException | None) -> "LandscapeBudgetExceeded | None":
        if not isinstance(error, ValidationError):
            return None
        return next((row.get("ctx", {}).get("error") for row in error.errors(include_input=False)
                     if isinstance(row.get("ctx", {}).get("error"), LandscapeBudgetExceeded)), None)

    def service_error(self, *, repair_attempted: bool, context_chars: int = 0) -> ServiceError:
        return ServiceError(422, "landscape_context_budget_exceeded",
                            "Landscape context cannot satisfy the generated-output budget", self.stage,
                            {"size_budget": self.budget, "measured_size": self.measured,
                             **({'assessment_encoding_version': ASSESSMENT_CONTEXT_VERSION}
                                if self.stage.startswith('landscape_item_assessments') else {}),
                             "context_chars": context_chars, "generated_output_chars": self.generated_chars,
                             "repair_attempted": repair_attempted, "persistence_occurred": False,
                             **self.allocation_details()})


def assessment_payload(assessments: list[dict[str, object]]) -> list[list[object]]:
    return [encode_assessment({key: value for key, value in item.items() if key not in _BINDING_FIELDS})
            for item in assessments]


def summary_source_context(context: dict[str, object]) -> dict[str, object]:
    """Lossless summary-only layout; identity is the map key, never omitted."""
    aliases = {"claim_type": "type", "human_grounding_status": "grounding",
               "evidence_for_ids": "supports", "evidence_against_ids": "challenges"}
    return {"claims": {
        claim["id"]: {aliases.get(key, key): value for key, value in claim.items() if key != "id"}
        for claim in context["claims"]},
        "evidence": {source["id"]: source["source_text"] for source in context["evidence"]}}


class LandscapeBudget:
    def __init__(self, context: dict[str, object], limit: int):
        self.limit = limit
        # Exact Claim content/roles and complete canonical Evidence text remain.
        # Repeated bibliographic metadata is still bound from the original context
        # when storing every assessment; it is not needed for scientific entailment.
        self.summary_sources = summary_source_context(context)
        self.source_chars = len(str({**self.summary_sources, "summary": "", "uncertainty": "",
                                    "accepted_items": [], "item_assessments": []}))
        self.assessment_chars = ASSESSMENT_CHARS
        self.critique_chars = CRITIQUE_CHARS
        self.shortest_evidence_id = min((source["id"] for source in context["evidence"]),
                                       key=len, default="")
        self.claim_sources = {claim['id']: set(claim['evidence_for_ids'] + claim['evidence_against_ids'])
                              for claim in context['claims']}
        self.evidence_ids = {source['id'] for source in context['evidence']}
        # Worst linked-source union for any item, and whole-output regeneration.
        item_base = len(str({"item_text": "", "item_category": "conflict", "item_uncertainty": "",
                             "claims": context["claims"], "evidence": context["evidence"]}))
        # An allowance safe for summary sources may exceed the full-source item
        # envelope. Reserve both actual stage shapes before choosing the output cap.
        self.output_chars = min(MAX_OUTPUT_CHARS, limit - item_base,
                                limit - self.source_chars - ASSESSMENT_CHARS - CRITIQUE_CHARS)
        initial = len(str(context))
        requirements = [("landscape_generation.preflight", limit, initial + CRITIQUE_CHARS + 24),
                        ("item_verification.preflight", limit, item_base + max(self.output_chars, MIN_OUTPUT_CHARS)),
                        ("summary_verification.preflight", limit,
                         self.source_chars + MIN_OUTPUT_CHARS + ASSESSMENT_CHARS + CRITIQUE_CHARS)]
        for stage, allowed, measured in requirements:
            if measured > allowed:
                error = LandscapeBudgetExceeded(stage, allowed, measured, generated_chars=0).service_error(
                    repair_attempted=False, context_chars=initial)
                error.details["reserved_context_chars"] = max(0, measured - initial)
                raise error

    def _assessment_floor(self, item: dict[str, object]) -> int:
        """Mechanical slots, using longest enum spellings, not a support fixture.

        One flag, qualification and nonblank justification slot permit negative
        judgments mechanically. Required scientific prose can be larger and must
        fit the allocated quota or fail. IDs are measured exactly, never shortened.
        """
        from app.services.scientific_schemas import ComparisonDimensions, LandscapeItemVerification
        def longest(model: type[BaseModel], field: str) -> str:
            return max(get_args(model.model_fields[field].annotation), key=lambda v: len(str(v)))
        related = set().union(*(self.claim_sources.get(claim_id, set())
                               for claim_id in item.get('claim_ids', [])))
        allowed = related & self.evidence_ids
        references = sorted(set(item.get('evidence_ids', [])) & allowed)
        if item.get('category') == 'conflict':
            # Mirror the existing distinct-source coverage requirement, while
            # retaining all directly cited IDs. This reserves identity footprints,
            # not an entailment judgment. An impossible matching remains subject
            # to the unchanged scientific validator, including negative verdicts.
            assigned: dict[str, str] = {}
            def match(claim_id: str, visited: set[str]) -> bool:
                candidates = self.claim_sources.get(claim_id, set()) & allowed
                for reference in sorted(candidates, key=lambda v: (v not in references, len(repr(v)), v)):
                    if reference in visited:
                        continue
                    visited.add(reference)
                    if reference not in assigned or match(assigned[reference], visited):
                        assigned[reference] = claim_id
                        return True
                return False
            for claim_id in dict.fromkeys(item.get('claim_ids', [])):
                match(claim_id, set())
            references = sorted(set(references) | set(assigned))
        elif not references and allowed:
            references = [min(allowed, key=lambda v: (len(repr(v)), v))]
        if not references and not item:  # Compatibility for a standalone schema request.
            references = [self.shortest_evidence_id] if self.shortest_evidence_id else []
        shape = {'verdict': longest(LandscapeItemVerification, 'verdict'),
                 'asserts_direct_conflict': False,
                 'dimensions': {key: longest(ComparisonDimensions, key)
                                for key in ComparisonDimensions.model_fields},
                 'support_verdict': longest(LandscapeItemVerification, 'support_verdict'),
                 'flagged_statements': ['x'], 'qualifications': ['x'],
                 'evidence_refs': references, 'justification': 'x'}
        return len(str(encode_assessment(shape)))

    def assessment_allocation(self, prior_assessments: list[dict[str, object]],
                              remaining_items: list[dict[str, object]]) -> AssessmentAllocation:
        if not remaining_items:
            raise ValueError('Assessment allocation requires a current item')
        prior = len(str(assessment_payload(prior_assessments)))
        # Appending the longest role boolean to the encoded row adds exactly
        # comma+space+False. Derive from the same encoder, not a parallel formula.
        shape = decode_assessment(encode_assessment({
            'verdict':'', 'asserts_direct_conflict':False, 'dimensions':dict.fromkeys(_DIMENSION_COLUMNS,''),
            'support_verdict':'', 'flagged_statements':[], 'qualifications':[], 'evidence_refs':[], 'justification':''}))
        role = len(str(encode_assessment({**shape, _SERVER_ROLE:False}))) - len(str(encode_assessment(shape)))
        separator = 2 if prior_assessments else 0
        floors = [self._assessment_floor(item) for item in remaining_items]
        later_minimum = sum(floor + role + 2 for floor in floors[1:])
        required = prior + separator + role + floors[0] + later_minimum
        slack = max(0, self.assessment_chars - required)
        current_extra = slack // len(floors)
        allocation = AssessmentAllocation(self.assessment_chars, prior, role, separator,
            later_minimum + slack - current_extra, floors[0] + current_extra,
            floors[0], required, len(floors))
        if required > self.assessment_chars:
            raise LandscapeBudgetExceeded('landscape_item_assessments.preflight', self.assessment_chars,
                required, generated_chars=0, allocation=allocation)
        return allocation

    def response_allowance(self, schema_name: str, *,
                     prior_assessments: list[dict[str, object]] | None = None,
                     remaining_items: list[dict[str, object]] | None = None,
                     landscape: dict[str, object] | None = None) -> int:
        allowance = self.output_chars
        if schema_name == "LandscapeItemVerification":
            allowance = self.assessment_allocation(prior_assessments or [],
                remaining_items if remaining_items is not None else [{}]).current_response_allowance
        elif schema_name == "LandscapeSummaryVerification":
            allowance = self.critique_chars
        elif schema_name == "SummaryRepair" and landscape is not None:
            allowance = (self.output_chars - len(str({**landscape, "summary": "", "uncertainty": ""})) +
                         len(str({"summary": "", "uncertainty": ""})))
        return max(0, allowance)

    def instructions(self, schema_name: str = "LandscapeOutput", *,
                     prior_assessments: list[dict[str, object]] | None = None,
                     remaining_items: list[dict[str, object]] | None = None,
                     landscape: dict[str, object] | None = None) -> str:
        instructions = LANDSCAPE_BUDGET_CONTRACT.format(output_chars=self.output_chars,
                                               assessment_chars=self.assessment_chars,
                                               critique_chars=self.critique_chars,
                                               response_allowance=self.response_allowance(schema_name,
                                                   prior_assessments=prior_assessments, landscape=landscape,
                                                   remaining_items=remaining_items))
        if schema_name == 'LandscapeItemVerification':
            instructions += LANDSCAPE_ASSESSMENT_ALLOCATION.format(**asdict(self.assessment_allocation(
                prior_assessments or [], remaining_items if remaining_items is not None else [{}])))
        return instructions

    def response_schema(self, schema: type[BaseModel], *,
                        prior_assessments: list[dict[str, object]] | None = None,
                        remaining_items: list[dict[str, object]] | None = None,
                        landscape: dict[str, object] | None = None) -> type[BaseModel]:
        """Request-local provider boundary; public and historical schemas stay intact.

        JSON Schema cannot express len(str(dict)) across fields. Dynamic individual
        bounds guide decoding, and before/after validators enforce the exact total.
        The service repeats this check for providers returning unvalidated dictionaries.
        """
        previous = copy(prior_assessments or [])
        name = schema.__name__
        allowance = self.response_allowance(name, prior_assessments=previous, landscape=landscape,
                                            remaining_items=remaining_items)

        def bounded_fields(model: type[BaseModel]) -> dict[str, tuple[object, object]]:
            fields = {}
            for key, original in model.model_fields.items():
                field = copy(original)
                maxima = [m.max_length for m in field.metadata if hasattr(m, "max_length")]
                if original.annotation is str:
                    field.metadata = [m for m in field.metadata if not hasattr(m, "max_length")]
                    field.metadata.append(MaxLen(min([allowance, *maxima])))
                annotation = original.annotation
                if annotation == list[str]:
                    # Bounds aid decoding only; the raw aggregate check is authoritative.
                    annotation = list[Annotated[str, MaxLen(allowance)]]
                    field.metadata = [m for m in field.metadata if not hasattr(m, 'max_length')]
                    field.metadata.append(MaxLen(min([allowance, *maxima])))
                fields[key] = (annotation, field)
            return fields

        fields = bounded_fields(schema)
        if name == "LandscapeOutput":
            from app.services.scientific_schemas import LandscapeItemProposal
            item_schema = create_model("LandscapeItemProposal", __base__=LandscapeItemProposal,
                                       **bounded_fields(LandscapeItemProposal))
            # Each item needs an independent assessment. Even an otherwise minimal
            # assessment consumes fixed dimensions/roles/reference overhead.
            minimum_assessment = {"verdict": "unclear", "asserts_direct_conflict": True,
                "dimensions": {key: "unknown" for key in (
                    "dataset", "population", "task", "metric", "experimental_condition", "baseline")},
                "support_verdict": "supported", "flagged_statements": [], "qualifications": [],
                "evidence_refs": [self.shortest_evidence_id], "justification": "x",
                "server_direct_conflict_role": False}
            max_items = min(50, (self.assessment_chars - 2) // (len(str(minimum_assessment)) + 2))
            field = copy(schema.model_fields["items"])
            field.metadata = [m for m in field.metadata if not hasattr(m, "max_length")]
            field.metadata.append(MaxLen(max_items))
            fields["items"] = (list[item_schema], field)

        @model_validator(mode="before")
        @classmethod
        def cumulative_raw(cls, value: object) -> object:
            raw = value.model_dump(mode="python") if isinstance(value, BaseModel) else value
            self.check_response(name, raw, prior_assessments=previous, landscape=landscape,
                                remaining_items=remaining_items)
            return value

        @model_validator(mode="after")
        def cumulative_normalized(value: BaseModel) -> BaseModel:
            self.check_response(name, value.model_dump(mode="python"),
                                prior_assessments=previous, landscape=landscape, remaining_items=remaining_items)
            return value

        return create_model(name, __base__=schema, __validators__={
            "cumulative_raw": cumulative_raw, "cumulative_normalized": cumulative_normalized}, **fields)

    @staticmethod
    def _check(stage: str, payload: object, limit: int) -> None:
        measured = len(str(payload))
        if measured > limit:
            raise LandscapeBudgetExceeded(stage, limit, measured)

    def check_output(self, output: dict[str, object]) -> None:
        self._check("landscape_generated_output", output, self.output_chars)

    def check_assessments(self, assessments: list[dict[str, object]]) -> None:
        self._check("landscape_item_assessments", assessment_payload(assessments), self.assessment_chars)

    def check_critique(self, critique: dict[str, object]) -> None:
        self._check("landscape_scientific_repair", critique, self.critique_chars)

    def check_response(self, schema_name: str, raw: object, *, prior_assessments: list[dict[str, object]],
                       landscape: dict[str, object] | None,
                       remaining_items: list[dict[str, object]] | None = None) -> None:
        if schema_name == "LandscapeOutput":
            self._check("landscape_generated_output", raw, self.output_chars)
        elif schema_name == "SummaryRepair":
            self._check("landscape_summary_repair", raw, self.output_chars)
            if isinstance(raw, dict) and landscape is not None:
                self.check_output({**landscape, **raw})
        elif schema_name == "LandscapeItemVerification":
            # Reserve the deterministic stored role's longer boolean representation.
            # Before-validation malformed shapes belong to the unchanged public
            # schema. Never project unknown fields away. After validation always
            # reaches the exact encoded aggregate check.
            if not isinstance(raw, dict):
                return
            if set(raw) - set(_ASSESSMENT_COLUMNS):
                return  # Public extra_forbidden preserves sanitized diagnostics.
            try:
                candidate_payload = encode_assessment(raw)
            except ValueError:
                return
            payload = encode_assessment({**raw, "server_direct_conflict_role": False})
            allocation = self.assessment_allocation(prior_assessments,
                remaining_items if remaining_items is not None else [{}])
            measured = len(str([*assessment_payload(prior_assessments), payload]))
            candidate = len(str(candidate_payload))
            if candidate > allocation.current_response_allowance or measured > self.assessment_chars:
                raise LandscapeBudgetExceeded('landscape_item_assessments', self.assessment_chars, measured,
                    allocation=allocation, candidate_chars=candidate)
        elif schema_name == "LandscapeSummaryVerification":
            self._check("landscape_summary_assessment", raw, self.critique_chars)

    def check_context(self, stage: str, context: dict[str, object]) -> None:
        measured = len(str(context))
        if measured > self.limit:
            generated = {key: value for key, value in context.items()
                         if key not in {"research_question", "claims", "evidence"}}
            raise LandscapeBudgetExceeded(stage, self.limit, measured,
                                           generated_chars=max(0, len(str(generated)) - 2))
