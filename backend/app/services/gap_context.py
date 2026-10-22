"""Gap-only, lossless request representation with canonical public references."""
from copy import deepcopy
from typing import Any, get_args

from app.services.m4_schemas import GapProposal

ENCODING = "gap-context-v1"
COLUMNS = {
    "landscape": ["id", "summary", "uncertainty"],
    "items": ["id", "category", "text", "uncertainty", "claim_ids", "evidence_ids"],
    "claims": ["id", "text", "claim_type", "human_grounding_status", "uncertainty", "evidence_for_ids", "evidence_against_ids"],
    "evidence": ["id", "source_text", "paper_chunk_id", "paper_index", "page_number"],
    "papers": ["paper_id", "paper_title"],
}
PROPOSAL_COLUMNS = list(GapProposal.model_fields)
PROTOCOL = {
    "encoding": ENCODING, "columns": COLUMNS,
    "identity_rule": "Each domain row retains its full canonical ID. Item claim_ids index claims; all evidence ID lists index evidence. paper_index indexes papers. Public generated source references must be full canonical IDs, never row indexes.",
    "claim_values_rule": "Claim claim_type, human_grounding_status and uncertainty cells index claim_values; decode exact strings without shortening.",
    "proposal_columns": PROPOSAL_COLUMNS,
    "proposal_rule": "Proposal landscape_item_ids, claim_ids, evidence_for_ids, evidence_against_ids index items, claims, evidence, evidence respectively. Preserve separate source roles and full scientific strings.",
}


class GapCapacityError(ValueError):
    def __init__(self, measured_chars: int, limit: int, stage: str, candidate_limit: int | None = None) -> None:
        self.measured_chars = measured_chars
        self.limit = limit
        self.stage = stage
        self.candidate_limit = candidate_limit
        super().__init__(f"Gap {stage} context requires {measured_chars} Python representation characters; limit {limit}; excess {max(0, measured_chars - limit)}; encoded candidate limit {candidate_limit}")


def _keys(value: Any, expected: set[str]) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("Gap encoding received missing or unknown contract fields")


def _text(value: Any) -> None:
    if type(value) is not str:
        raise ValueError("Gap source text must remain an exact string")


def _identity_string(value: Any) -> None:
    # Canonical means the exact persisted identity; do not tighten the public
    # list[str] contract or rewrite identities into a different UUID spelling.
    if type(value) is not str or not value.strip():
        raise ValueError("Source identity must be its exact nonblank canonical string")


def _index(rows: list[dict[str, Any]]) -> dict[str, int]:
    if type(rows) is not list or not rows:
        raise ValueError("Gap source domain rows must be a nonempty list")
    ids = [row["id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate canonical source identity")
    return {value: i for i, value in enumerate(ids)}


def _refs(value: Any, allowed: dict[str, int]) -> list[int]:
    if type(value) is not list or any(type(v) is not str or v not in allowed for v in value):
        raise ValueError("Gap encoding encountered a foreign source reference")
    if len(value) != len(set(value)):
        raise ValueError("Duplicate source reference")
    return [allowed[v] for v in value]


def _lookup(rows: list[Any], index: Any) -> Any:
    if type(index) is not int or not 0 <= index < len(rows):
        raise ValueError("Invalid typed Gap row reference")
    return rows[index]


def _assessment_payload(source: dict[str, Any], proposal: list[Any]) -> dict[str, Any]:
    """Shared actual wrapper for provider contexts and every budget calculation."""
    return {"scientific_input": deepcopy(source), "proposal": proposal}


def encode_gap_context(context: dict[str, Any], revision: int) -> dict[str, Any]:
    _keys(context, {"research_question", "landscape", "claims", "evidence"})
    _keys(context["landscape"], {"id", "summary", "uncertainty", "items"})
    if type(revision) is not int or revision < 1:
        raise ValueError("Question revision must be a positive integer")
    _text(context["research_question"])
    for field in ("summary", "uncertainty"):
        _text(context["landscape"][field])
    items = context["landscape"]["items"]
    claims, evidence = context["claims"], context["evidence"]
    for rows, columns in [(items, COLUMNS["items"]), (claims, COLUMNS["claims"]),
                          (evidence, ["id", "source_text", "paper_chunk_id", "paper_id", "paper_title", "page_number"])]:
        if type(rows) is not list or not rows:
            raise ValueError("Gap requires complete nonempty source domains")
        for row in rows:
            _keys(row, set(columns))
    claim_index, evidence_index = _index(claims), _index(evidence)
    _index(items)
    identities: dict[str, str] = {}

    def identity(value: str, kind: str) -> None:
        _identity_string(value)
        if value in identities and identities[value] != kind:
            raise ValueError("Canonical source identity collides across domains")
        identities[value] = kind

    identity(context["landscape"]["id"], "landscape")
    for kind, rows in [("item", items), ("claim", claims), ("evidence", evidence)]:
        for row in rows:
            identity(row["id"], kind)
    papers: dict[str, Any] = {}
    chunks: dict[str, tuple[str, int]] = {}
    for row in evidence:
        _text(row["source_text"])
        if row["paper_title"] is not None:
            _text(row["paper_title"])
        if type(row["page_number"]) is not int or row["page_number"] < 1:
            raise ValueError("Evidence physical page must be a positive integer")
        identity(row["paper_id"], "paper")
        identity(row["paper_chunk_id"], "paper_chunk")
        if row["paper_id"] in papers and papers[row["paper_id"]] != row["paper_title"]:
            raise ValueError("One Paper identity has conflicting canonical metadata")
        papers[row["paper_id"]] = row["paper_title"]
        provenance = (row["paper_id"], row["page_number"])
        if row["paper_chunk_id"] in chunks and chunks[row["paper_chunk_id"]] != provenance:
            raise ValueError("One PaperChunk identity has conflicting physical provenance")
        chunks[row["paper_chunk_id"]] = provenance
    paper_rows = [[pid, papers[pid]] for pid in sorted(papers)]
    paper_index = {row[0]: i for i, row in enumerate(paper_rows)}
    for row in items:
        for field in ("category", "text", "uncertainty"):
            _text(row[field])
        _refs(row["claim_ids"], claim_index)
        _refs(row["evidence_ids"], evidence_index)
    for row in claims:
        for field in ("text", "claim_type", "human_grounding_status", "uncertainty"):
            _text(row[field])
        _refs(row["evidence_for_ids"], evidence_index)
        _refs(row["evidence_against_ids"], evidence_index)
        if set(row["evidence_for_ids"]) & set(row["evidence_against_ids"]):
            raise ValueError("Claim Evidence roles conflict")
    pool = sorted({row[k] for row in claims for k in ("claim_type", "human_grounding_status", "uncertainty")})
    result = {
        "encoding": ENCODING, "research_question": context["research_question"],
        "landscape": [context["landscape"][k] for k in COLUMNS["landscape"]],
        "items": [[r["id"], r["category"], r["text"], r["uncertainty"], _refs(r["claim_ids"], claim_index), _refs(r["evidence_ids"], evidence_index)] for r in items],
        "claims": [[r["id"], r["text"], *(pool.index(r[k]) for k in ("claim_type", "human_grounding_status", "uncertainty")), _refs(r["evidence_for_ids"], evidence_index), _refs(r["evidence_against_ids"], evidence_index)] for r in claims],
        "evidence": [[r["id"], r["source_text"], r["paper_chunk_id"], paper_index[r["paper_id"]], r["page_number"]] for r in evidence],
        "papers": paper_rows, "claim_values": pool, "question_revision": revision,
    }
    return result


def decode_gap_context(payload: dict[str, Any]) -> dict[str, Any]:
    _keys(payload, {"encoding", "research_question", "landscape", "items", "claims", "evidence", "papers", "claim_values", "question_revision"})
    if payload["encoding"] != ENCODING:
        raise ValueError("Unsupported Gap context encoding")
    value = deepcopy(payload)
    for key in ("items", "claims", "evidence", "papers"):
        if type(value[key]) is not list or any(type(row) is not list or len(row) != len(COLUMNS[key]) for row in value[key]):
            raise ValueError("Gap encoded row has an invalid fixed-column shape")
    for key, positions in (("items", (4, 5)), ("claims", (5, 6))):
        if any(type(row[position]) is not list for row in value[key] for position in positions):
            raise ValueError("Gap source role references must be typed lists")
    if type(value["landscape"]) is not list or len(value["landscape"]) != len(COLUMNS["landscape"]):
        raise ValueError("Gap encoded Landscape has an invalid fixed-column shape")
    if type(value["claim_values"]) is not list or any(type(v) is not str for v in value["claim_values"]):
        raise ValueError("Gap exact-value pool must contain complete strings")
    for item in value["items"]:
        item[4] = [_lookup(value["claims"], i)[0] for i in item[4]]
        item[5] = [_lookup(value["evidence"], i)[0] for i in item[5]]
    for claim in value["claims"]:
        for position in (2, 3, 4):
            claim[position] = _lookup(value["claim_values"], claim[position])
        claim[5] = [_lookup(value["evidence"], i)[0] for i in claim[5]]
        claim[6] = [_lookup(value["evidence"], i)[0] for i in claim[6]]
    result = {
        "research_question": value["research_question"],
        "landscape": {**dict(zip(COLUMNS["landscape"], value["landscape"], strict=True)),
                      "items": [dict(zip(COLUMNS["items"], row, strict=True)) for row in value["items"]]},
        "claims": [dict(zip(COLUMNS["claims"], row, strict=True)) for row in value["claims"]],
        "evidence": [dict(zip(["id", "source_text", "paper_chunk_id", "paper_id", "paper_title", "page_number"],
                              [r[0], r[1], r[2], *_lookup(value["papers"], r[3]), r[4]], strict=True)) for r in value["evidence"]],
    }
    if encode_gap_context(result, value["question_revision"]) != payload:
        raise ValueError("Gap encoded source is not the complete canonical representation")
    return result


def encode_gap_proposal(proposal: dict[str, Any] | GapProposal, payload: dict[str, Any]) -> list[Any]:
    value = proposal.model_dump() if isinstance(proposal, GapProposal) else proposal
    # Public schema validates canonical fields before request-local interpretation.
    GapProposal.model_validate(value)
    result = [deepcopy(value[k]) for k in PROPOSAL_COLUMNS]
    for field, rows in [("landscape_item_ids", "items"), ("claim_ids", "claims"),
                        ("evidence_for_ids", "evidence"), ("evidence_against_ids", "evidence")]:
        mapping = {row[0]: i for i, row in enumerate(payload[rows])}
        result[PROPOSAL_COLUMNS.index(field)] = _refs(value[field], mapping)
    return result


def decode_gap_proposal(proposal: list[Any], payload: dict[str, Any]) -> dict[str, Any]:
    if type(proposal) is not list or len(proposal) != len(PROPOSAL_COLUMNS):
        raise ValueError("Gap proposal has an invalid fixed-column shape")
    value = dict(zip(PROPOSAL_COLUMNS, deepcopy(proposal), strict=True))
    for field, rows in [("landscape_item_ids", "items"), ("claim_ids", "claims"),
                        ("evidence_for_ids", "evidence"), ("evidence_against_ids", "evidence")]:
        if type(value[field]) is not list:
            raise ValueError("Gap proposal source references must be typed lists")
        value[field] = [_lookup(payload[rows], i)[0] for i in value[field]]
    GapProposal.model_validate(value)
    return value


class GapContext:
    def __init__(self, context: dict[str, Any], revision: int, limit: int) -> None:
        if type(limit) is not int or limit < 1:
            raise ValueError("Gap capacity must be a positive integer")
        self.limit = limit
        self._context = encode_gap_context(context, revision)
        base_size = len(str(self._context))
        wrapper_size = len(str(_assessment_payload(self._context, []))) - len(str([]))
        self.candidate_limit = limit - wrapper_size
        if base_size > limit:
            raise GapCapacityError(base_size, limit, "initial", self.candidate_limit)
        # Strict lower bound only: every valid Gap needs these fields and three IDs.
        gap_type = min(get_args(GapProposal.model_fields["gap_type"].annotation), key=lambda v: len(str(v)))
        minimum = ["x", "x", gap_type, "x", "x", [], "supplied_literature", [0], [0], [0], []]
        smallest_assessment = len(str(_assessment_payload(self._context, minimum)))
        if smallest_assessment > limit:
            raise GapCapacityError(smallest_assessment, limit, "assessment_infeasible", self.candidate_limit)

    @property
    def context(self) -> dict[str, Any]:
        return deepcopy(self._context)

    @property
    def candidate_budget(self) -> int:
        return self.candidate_limit

    def assessment_context(self, proposal: dict[str, Any] | GapProposal) -> dict[str, Any]:
        return _assessment_payload(self._context, encode_gap_proposal(proposal, self._context))

    def validate_candidate(self, proposal: dict[str, Any] | GapProposal) -> None:
        measured = len(str(self.assessment_context(proposal)))
        if measured > self.limit:
            raise GapCapacityError(measured, self.limit, "assessment", self.candidate_limit)
