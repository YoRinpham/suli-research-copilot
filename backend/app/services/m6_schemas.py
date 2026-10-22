"""Strict, source-bound narrative for report presentation only."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ReportBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    section: Literal["executive_summary", "evidence_landscape", "limitations", "conclusion"]
    text: str = Field(min_length=1, max_length=3000)
    source_refs: list[str] = Field(min_length=1, max_length=20)


class ReportNarrative(BaseModel):
    model_config = ConfigDict(extra="forbid")
    blocks: list[ReportBlock] = Field(min_length=1, max_length=12)


_OVERCLAIM = re.compile(
    r"\b(first (?:study|ever)|world first|never before|unprecedented|globally novel|"
    r"proves?|proven|confirms?|confirmed|definitively|definitely|"
    r"caus(?:es|ed|ation)|causal(?:ly)?|leads to|drives|statistically significant|"
    r"no effect exists|null hypothesis is true|all studies|universally)\b|"
    r"首次研究|全球首创|前所未有|证明|证实|必然|一定导致|因果关系|统计学显著",
    re.IGNORECASE,
)

_SOURCE_IDENTITY = re.compile(r"\b(?:CLAIM|EVIDENCE|PAPER|RESULT|FIGURE|INTERPRETATION)_[A-Z0-9_]+\b", re.IGNORECASE)
_ABSOLUTE_NULL = re.compile(
    r"\b(?:no (?:effect|difference)|(?:the )?effect does not exist|"
    r"(?:proves?|proven|definitely|definitively) no (?:effect|difference)|"
    r"no (?:effect|difference) exists)\b|不存在(?:效应|差异)|证明(?:没有|不存在)(?:效应|差异)",
    re.IGNORECASE,
)
_CAUTIOUS_NULL = re.compile(
    r"\b(?:no statistically detectable (?:effect|difference)|(?:did not|does not|failed to) "
    r"(?:provide sufficient evidence|detect (?:an? )?(?:effect|difference))|inconclusive)\b|"
    r"未发现统计学上可检出的(?:效应|差异)|证据不足|结论不确定",
    re.IGNORECASE,
)
_INCONCLUSIVE_CONTEXT = re.compile(
    r"\b(?:inconclusive|uncertain|insufficient evidence|not enough evidence|"
    r"did not provide sufficient evidence|failed to detect|not statistically detectable)\b|"
    r"不确定|证据不足|未检出|无足够证据",
    re.IGNORECASE,
)


def validate_narrative(narrative: ReportNarrative, allowed_tokens: set[str],
                       interpretation: dict | None = None) -> None:
    for block in narrative.blocks:
        if len(block.source_refs) != len(set(block.source_refs)) or not set(block.source_refs) <= allowed_tokens:
            raise ValueError("Report source reference is not whitelisted")
        if _SOURCE_IDENTITY.search(block.text):
            raise ValueError("Report narrative contains source identity; citations come from source_refs")
        if _ABSOLUTE_NULL.search(block.text):
            raise ValueError("Report narrative overclaims absence of an effect")
        if _CAUTIOUS_NULL.search(block.text) and interpretation is not None:
            context = " ".join(str(interpretation.get(key, "")) for key in
                               ("summary", "hypothesis_relationship", "uncertainty"))
            if "INTERPRETATION_1" not in block.source_refs or not _INCONCLUSIVE_CONTEXT.search(context):
                raise ValueError("Report narrative null-result claim conflicts with accepted interpretation")
        # Numerical results are always rendered from StatisticalResult, never copied from prose.
        if re.search(r"\d", block.text):
            raise ValueError("Report narrative contains numerical text")
        if _OVERCLAIM.search(block.text):
            raise ValueError("Report narrative overclaims scientific authority")
