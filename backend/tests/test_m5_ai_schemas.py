import pytest
from pydantic import ValidationError

from app.services.m5_ai_schemas import InterpretationProposal, validate_interpretation_language
from app.prompts.m5 import CLEANING_PROMPT, ANALYSIS_PROMPT, INTERPRETATION_PROMPT
from app.prompts.scientific import Prompt


def test_m5_prompt_hashes_include_repair_and_verification():
    for prompt in (CLEANING_PROMPT, ANALYSIS_PROMPT, INTERPRETATION_PROMPT):
        assert "repair" in prompt.bundle
        assert prompt.bundle["generation"] == prompt.text
        assert "untrusted" in prompt.text
        assert len(prompt.digest) == 64
        assert Prompt(prompt.name, prompt.version, prompt.text + " changed", {**prompt.bundle, "generation": prompt.text + " changed"}).digest != prompt.digest
    assert "assessment" in INTERPRETATION_PROMPT.bundle


def test_interpretation_rejects_numeric_mutation_and_overclaim():
    value = {"summary": "The observations are compatible with the prediction.",
             "hypothesis_relationship": "Compatible under the analyzed design.",
             "uncertainty": "The sample is limited.", "limitations": ["Small dataset"],
             "alternative_explanations": ["Unmeasured context"],
             "statistical_result_ids": ["result-id"], "figure_artifact_ids": []}
    proposal = InterpretationProposal.model_validate(value)
    validate_interpretation_language(proposal)
    for bad in ({**value, "summary": "p = 0.03 proves the hypothesis"},
                {**value, "summary": "The null hypothesis is true"},
                {**value, "summary": "This proves causality"},
                {**value, "summary": "There is no effect"},
                {**value, "summary": "The association is statistically significant"}):
        with pytest.raises((ValidationError, ValueError)):
            validate_interpretation_language(InterpretationProposal.model_validate(bad))


def test_interpretation_prompt_routes_uuid_references_outside_qualitative_prose():
    assert INTERPRETATION_PROMPT.version == "m5-v3"
    for instructions in (INTERPRETATION_PROMPT.text, INTERPRETATION_PROMPT.bundle["repair"]):
        for field in ("summary", "hypothesis_relationship", "uncertainty", "limitations",
                      "alternative_explanations", "statistical_result_ids", "figure_artifact_ids"):
            assert field in instructions
        assert "only in" in instructions
        assert "even in negated" in instructions

    proposal = InterpretationProposal(
        summary="Within the fixed synthetic file, the group mean ordering is compatible with the assertion.",
        hypothesis_relationship="A post hoc descriptive check within this file.",
        uncertainty="No population inference is warranted.", limitations=[], alternative_explanations=[],
        statistical_result_ids=["439cb369-fece-4968-83d2-f027fcdda2aa"],
        figure_artifact_ids=["8a54cf2b-58ca-4bff-ad78-27c78d4d680e"])
    validate_interpretation_language(proposal)
    with pytest.raises(ValueError, match="must not reproduce numerical results"):
        validate_interpretation_language(proposal.model_copy(update={
            "summary": proposal.summary + " Result 439cb369-fece-4968-83d2-f027fcdda2aa."}))
    with pytest.raises(ValueError, match="overstates"):
        validate_interpretation_language(proposal.model_copy(update={
            "summary": "This does not prove the hypothesis."}))
