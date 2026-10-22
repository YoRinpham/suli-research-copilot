"""Gap-only fixed lineage metadata; never retain rejected scientific output."""
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

GapLineageCode = Literal['gap_foreign_landscape_item', 'gap_incomplete_lineage', 'gap_evidence_claim_mismatch']
_MESSAGES = {
    'gap_foreign_landscape_item': 'Gap cited a foreign LandscapeItem',
    'gap_incomplete_lineage': 'Gap source lineage is incomplete',
    'gap_evidence_claim_mismatch': 'Gap Evidence does not belong to cited Claims',
}
GAP_REPAIR_FEEDBACK_SPEC = {
    'prefix': 'Gap repair feedback: ',
    'rebuild': 'Construct a fresh replacement batch from the unchanged authoritative context. Discarded candidate wording is not supplied. Rebuild references using the row mappings and check every candidate. Do not substitute IDs, add Claims automatically, or move challenging citations into support to force validation.',
    'gap_foreign_landscape_item': 'A cited LandscapeItem was outside the current items rows. Copy landscape_item_ids only from exact canonical item IDs in those rows.',
    'gap_incomplete_lineage': 'Check that explicitly cited Claims belong to the chosen items, cited Evidence belongs to the items including inherited sources, and at least one supporting Evidence legitimately motivates each Gap.',
    'gap_evidence_claim_mismatch': 'Check both supporting and challenging Evidence against the union of Evidence linked to the explicitly cited Claims, as well as the chosen items. Item membership alone is insufficient.',
    'gap_generation_invalid': 'The previous batch was invalid. Recheck the supplied schema and all reference relationships against the unchanged authoritative context.',
}

class GapRepairFeedback(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    validation_code: Literal['gap_foreign_landscape_item', 'gap_incomplete_lineage',
                             'gap_evidence_claim_mismatch', 'gap_generation_invalid'] = 'gap_generation_invalid'
    candidate_index: int | None = Field(default=None, ge=0, le=9)

    @classmethod
    def from_error(cls, error: Exception) -> 'GapRepairFeedback':
        # Reject arbitrary exception attributes and forged typed model contents.
        if type(error) is GapLineageError and type(getattr(error, 'feedback', None)) is cls:
            try:
                if error.feedback.__pydantic_extra__:
                    return cls()
                return cls.model_validate(error.feedback.model_dump())
            except (TypeError, ValueError):
                pass
        return cls()

    def instructions(self) -> str:
        return ('\n' + GAP_REPAIR_FEEDBACK_SPEC['prefix'] + json.dumps(self.model_dump(), separators=(',', ':'))
                + '\n' + GAP_REPAIR_FEEDBACK_SPEC[self.validation_code]
                + ' ' + GAP_REPAIR_FEEDBACK_SPEC['rebuild'])

class GapLineageError(ValueError):
    def __init__(self, validation_code: GapLineageCode, candidate_index: int | None = None) -> None:
        self.feedback = GapRepairFeedback(validation_code=validation_code, candidate_index=candidate_index)
        super().__init__(_MESSAGES[validation_code])

    @property
    def validation_code(self) -> str:
        return GapRepairFeedback.from_error(self).validation_code

    def at_candidate(self, index: int) -> 'GapLineageError':
        if self.validation_code == 'gap_generation_invalid':
            return self
        return GapLineageError(self.validation_code, index)
